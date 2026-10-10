"""
通达信行业分类 Web 系统
- 首页仅展示 30 个一级行业卡片
- 点击一级卡片 -> 异步加载其下二级行业
- 点击二级 -> 异步加载其下三级行业
- 点击三级 -> 异步加载对应股票
"""
import csv
import os
from collections import OrderedDict
from typing import Dict

from flask import Flask, jsonify, render_template, abort, request

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(
    BASE_DIR, "data", "通达信一级二级三级行业汇总20261007 - 副本.csv"
)
# CSV 表头顺序（14 列），新增股票时按此顺序追加行
CSV_COLUMNS = [
    "一级编码", "一级代码", "一级行业(30)", "二级代码", "二级行业(128)",
    "三级代码", "三级行业(345)", "股票名称", "股票代码", "所在地",
    "主营", "概念1", "概念2", "概念2",
]


def load_industry_tree():
    """
    读取 CSV 并构建层级树:
    {
      "一级行业": {
          "code": "881001",
          "secondaries": {
              "二级行业": {
                  "code": "881002",
                  "tertiaries": {
                      "三级行业": {
                          "code": "881003",
                          "stocks": [
                              {"name": "郑州煤电", "code": "600121", ...},
                          ]
                      }
                  }
              }
          }
      },
      ...
    }
    """
    tree = OrderedDict()
    with open(DATA_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            primary_acode = (row.get("一级编码") or "").strip()
            primary_code = (row.get("一级代码") or "").strip()
            primary_name = (row.get("一级行业(30)") or "").strip()
            secondary_code = (row.get("二级代码") or "").strip()
            secondary_name = (row.get("二级行业(128)") or "").strip()
            tertiary_code = (row.get("三级代码") or "").strip()
            tertiary_name = (row.get("三级行业(345)") or "").strip()

            stock_name = (row.get("股票名称") or "").strip()
            stock_code = (row.get("股票代码") or "").strip()

            if not primary_name:
                continue

            if primary_name not in tree:
                tree[primary_name] = {
                    "acode": primary_acode,   # 一级编码（A1、A2...）
                    "code": primary_code,     # 一级代码（881001...）
                    "secondaries": OrderedDict(),
                }
            primary = tree[primary_name]

            if not secondary_name:
                continue

            if secondary_name not in primary["secondaries"]:
                primary["secondaries"][secondary_name] = {
                    "code": secondary_code,
                    "tertiaries": OrderedDict(),
                }
            secondary = primary["secondaries"][secondary_name]

            if not tertiary_name:
                continue

            if tertiary_name not in secondary["tertiaries"]:
                secondary["tertiaries"][tertiary_name] = {
                    "code": tertiary_code,
                    "stocks": [],
                }
            tertiary = secondary["tertiaries"][tertiary_name]

            if stock_name:
                tertiary["stocks"].append(
                    {
                        "name": stock_name,
                        "code": stock_code,
                        "location": (row.get("所在地") or "").strip(),
                        "main_business": (row.get("主营") or "").strip(),
                        "concept1": (row.get("概念") or "").strip(),
                        "concept2": "",
                        "concept3": "",
                        "concept4": "",
                        "concept5": "",
                    }
                )

    return tree


# 启动时加载一次
INDUSTRY_TREE = load_industry_tree()


def serialize_primary(name, primary):
    """序列化一个一级行业（仅含自身元信息 + 统计数量）"""
    secondary_count = len(primary["secondaries"])
    tertiary_count = sum(
        len(s["tertiaries"]) for s in primary["secondaries"].values()
    )
    stock_count = sum(
        len(t["stocks"])
        for s in primary["secondaries"].values()
        for t in s["tertiaries"].values()
    )
    return {
        "name": name,
        "code": primary["code"],
        "secondaryCount": secondary_count,
        "tertiaryCount": tertiary_count,
        "stockCount": stock_count,
    }


def serialize_secondary(name, secondary):
    tertiary_count = len(secondary["tertiaries"])
    stock_count = sum(
        len(t["stocks"]) for t in secondary["tertiaries"].values()
    )
    return {
        "name": name,
        "code": secondary["code"],
        "tertiaryCount": tertiary_count,
        "stockCount": stock_count,
        "tertiaries": [
            {
                "name": tname,
                "code": t["code"],
                "stockCount": len(t["stocks"]),
            }
            for tname, t in secondary["tertiaries"].items()
        ],
    }


# ============== 页面 ==============

@app.route("/")
def index():
    """首页：30 个一级行业卡片（只列名称/代码/统计）"""
    primaries = [
        serialize_primary(name, primary)
        for name, primary in INDUSTRY_TREE.items()
    ]
    total_primary = len(primaries)
    total_secondary = sum(p["secondaryCount"] for p in primaries)
    total_tertiary = sum(p["tertiaryCount"] for p in primaries)
    total_stocks = sum(p["stockCount"] for p in primaries)
    return render_template(
        "index.html",
        primaries=primaries,
        stats={
            "primary": total_primary,
            "secondary": total_secondary,
            "tertiary": total_tertiary,
            "stocks": total_stocks,
        },
    )


@app.route("/pageA")
def page_a():
    """页面 A：仅展示一级行业和二级行业（不含三级、股票）"""
    groups = []
    for primary_name, primary in INDUSTRY_TREE.items():
        secondaries = []
        tertiary_count = 0
        stock_count = 0
        for sname, secondary in primary["secondaries"].items():
            tertiary_count += len(secondary["tertiaries"])
            stock_count += sum(
                len(t["stocks"]) for t in secondary["tertiaries"].values()
            )
            secondaries.append(
                {
                    "name": sname,
                    "code": secondary["code"],
                }
            )
        groups.append(
            {
                "name": primary_name,
                "code": primary["code"],
                "secondaryCount": len(secondaries),
                "tertiaryCount": tertiary_count,
                "stockCount": stock_count,
                "secondaries": secondaries,
            }
        )

    total_primary = len(groups)
    total_secondary = sum(g["secondaryCount"] for g in groups)
    return render_template(
        "pageA.html",
        groups=groups,
        stats={
            "primary": total_primary,
            "secondary": total_secondary,
        },
    )


def _build_full_groups():
    """构造页面 B/C 所需的完整层级数据（避免代码重复）"""
    groups = []
    total_secondary = 0
    total_tertiary = 0
    total_stocks = 0
    for primary_name, primary in INDUSTRY_TREE.items():
        secondaries = []
        primary_stock_count = 0
        for sname, secondary in primary["secondaries"].items():
            tertiaries = []
            secondary_stock_count = 0
            for tname, tertiary in secondary["tertiaries"].items():
                tertiaries.append(
                    {
                        "name": tname,
                        "code": tertiary["code"],
                        "stockCount": len(tertiary["stocks"]),
                        "stocks": tertiary["stocks"],
                    }
                )
                secondary_stock_count += len(tertiary["stocks"])
            total_tertiary += len(tertiaries)
            total_stocks += secondary_stock_count
            primary_stock_count += secondary_stock_count
            secondaries.append(
                {
                    "name": sname,
                    "code": secondary["code"],
                    "tertiaryCount": len(tertiaries),
                    "stockCount": secondary_stock_count,
                    "tertiaries": tertiaries,
                }
            )
        total_secondary += len(secondaries)
        groups.append(
            {
                "name": primary_name,
                "code": primary["code"],
                "secondaryCount": len(secondaries),
                "tertiaryCount": total_tertiary,
                "stockCount": primary_stock_count,
                "secondaries": secondaries,
            }
        )
    return groups, {
        "primary": len(groups),
        "secondary": total_secondary,
        "tertiary": total_tertiary,
        "stocks": total_stocks,
    }


@app.route("/pageB")
def page_b():
    """页面 B：展示一级 + 二级 + 三级 + 股票（沿用页面 A 的左列粘性布局）"""
    groups, stats = _build_full_groups()
    return render_template("pageB.html", groups=groups, stats=stats)


@app.route("/pageD")
def page_d():
    """页面 D：与页面 B 完全相同（展示一级+二级+三级+股票），股票名称可跳转页面 F 搜索"""
    groups, stats = _build_full_groups()
    return render_template("pageD.html", groups=groups, stats=stats)


@app.route("/pageF")
def page_f():
    """页面 F：多股票批量搜索"""
    return render_template("pageF.html")


@app.route("/pageG")
def page_g():
    """
    页面 G：按行业展示股票列表。
    参数：level（1/2/3，或 primary/secondary/tertiary）+ name（行业名称）。
    三级行业直接展示其下股票；一级/二级行业按三级行业分组展示其下全部股票。
    """
    level = (request.args.get("level") or "").strip().lower()
    name = (request.args.get("name") or "").strip()
    level_map = {
        "1": 1, "2": 2, "3": 3,
        "primary": 1, "secondary": 2, "tertiary": 3,
    }
    level = level_map.get(level, 0)

    groups = []   # [{name, code, stocks: [...]}]
    industry = None
    if level and name:
        if level == 1:
            primary = INDUSTRY_TREE.get(name)
            if primary:
                industry = {"name": name, "code": primary["code"], "level": "一级行业"}
                for sname, secondary in primary["secondaries"].items():
                    for tname, tertiary in secondary["tertiaries"].items():
                        groups.append(
                            {"name": tname, "code": tertiary["code"],
                             "stocks": tertiary["stocks"]}
                        )
        elif level == 2:
            for primary_name, primary in INDUSTRY_TREE.items():
                secondary = primary["secondaries"].get(name)
                if secondary:
                    industry = {
                        "name": name, "code": secondary["code"],
                        "level": "二级行业", "parent": primary_name,
                    }
                    for tname, tertiary in secondary["tertiaries"].items():
                        groups.append(
                            {"name": tname, "code": tertiary["code"],
                             "stocks": tertiary["stocks"]}
                        )
                    break
        elif level == 3:
            for primary_name, primary in INDUSTRY_TREE.items():
                for sname, secondary in primary["secondaries"].items():
                    tertiary = secondary["tertiaries"].get(name)
                    if tertiary:
                        industry = {
                            "name": name, "code": tertiary["code"],
                            "level": "三级行业",
                            "parent": f"{primary_name} / {sname}",
                        }
                        groups.append(
                            {"name": name, "code": tertiary["code"],
                             "stocks": tertiary["stocks"]}
                        )
                        break
                if industry:
                    break

    total = sum(len(g["stocks"]) for g in groups)
    return render_template(
        "pageG.html",
        industry=industry,
        groups=groups,
        total=total,
        query_name=name,
        query_level=level,
    )


@app.route("/api/pageH/add_concept", methods=["POST"])
def api_page_h_add_concept():
    """
    为指定股票添加一个概念，写入 CSV 「概念」列。
    多概念以 '-' 分隔（兼容旧的 ','、'，'、'、'、' ' 分隔符）。
    请求 JSON: {"path": "一级||二级||三级", "name": "股票名称", "concept": "火电"}
    返回: {"ok": true, "concept": "火电-机器人"} 或 {"ok": false, "error": "..."}
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    stock_name = (data.get("name") or "").strip()
    concept = (data.get("concept") or "").strip()

    if not concept:
        return jsonify({"ok": False, "error": "概念名称不能为空"}), 400
    if not stock_name:
        return jsonify({"ok": False, "error": "请填写股票名称"}), 400
    if path.count("||") != 2:
        return jsonify({"ok": False, "error": "行业路径不合法"}), 400

    primary_name, secondary_name, tertiary_name = (s.strip() for s in path.split("||"))
    primary, secondary, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404

    target = None
    for st in tertiary["stocks"]:
        if st["name"] == stock_name:
            target = st
            break
    if target is None:
        return jsonify({"ok": False, "error": f"未找到股票：{stock_name}"}), 404

    # 检查重复：扫描概念1~5 所有列
    all_pieces = set()
    for key in ("concept1", "concept2", "concept3", "concept4", "concept5"):
        v = target.get(key) or ""
        if not v:
            continue
        norm = (
            v.replace("-", ",")
             .replace("，", ",")
             .replace("、", ",")
             .replace(" ", ",")
             .replace(";", ",")
        )
        for piece in norm.split(","):
            piece = piece.strip()
            if piece:
                all_pieces.add(piece)
    if concept in all_pieces:
        return jsonify({"ok": False, "error": "该股票已有此概念"}), 400

    # 写入策略：CSV 实际只有「概念」一列
    # 若概念1 已存在则追加（用 '-'），否则直接填入
    cur = (target.get("concept1") or "").strip()
    target_key = "concept1"
    target_value = f"{cur}-{concept}" if cur else concept

    # 写回 CSV
    try:
        with open(DATA_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        return jsonify({"ok": False, "error": f"读取CSV失败：{e}"}), 500

    if not rows:
        return jsonify({"ok": False, "error": "CSV文件无表头"}), 500

    header = rows[0]
    # 兼容：CSV 实际只有「概念」列
    col_name_to_index = {n: i for i, n in enumerate(header)}
    if "概念" not in col_name_to_index:
        header.append("概念")
        col_name_to_index["概念"] = len(header) - 1
    rows[0] = header

    # 把所有行对齐到 header 长度
    for i in range(1, len(rows)):
        if len(rows[i]) < len(header):
            rows[i].extend([""] * (len(header) - len(rows[i])))

    # 写入列：实际只写入「概念」列
    col_target = col_name_to_index["概念"]
    col_name = col_name_to_index["股票名称"]
    idx_primary = col_name_to_index["一级行业(30)"]
    idx_secondary = col_name_to_index["二级行业(128)"]
    idx_tertiary = col_name_to_index["三级行业(345)"]

    target_idx = -1
    for i in range(1, len(rows)):
        r = rows[i]
        if (
            r[idx_primary] == primary_name
            and r[idx_secondary] == secondary_name
            and r[idx_tertiary] == tertiary_name
            and r[col_name] == stock_name
        ):
            target_idx = i
            break

    if target_idx < 0:
        return jsonify({"ok": False, "error": "CSV中未找到该股票行"}), 500

    rows[target_idx][col_target] = target_value

    try:
        with open(DATA_FILE, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
    except OSError as e:
        return jsonify({"ok": False, "error": f"写入CSV失败：{e}"}), 500

    # 同步更新内存树
    target[target_key] = target_value

    return jsonify({
        "ok": True,
        "concept": target_value,
        "stock": {
            "name": target["name"],
            "code": target.get("code", ""),
            "primary": primary_name,
            "secondary": secondary_name,
            "tertiary": tertiary_name,
        },
    })


@app.route("/pageJ")
def page_j():
    """
    页面 J：展示所有概念（概念1~5），并按股票数倒序排列。
    点击概念可跳转到 pageH 查看该概念下的股票列表。
    """
    counts: Dict[str, int] = {}
    total_stocks_with_concept = 0
    for primary_name, primary in INDUSTRY_TREE.items():
        for secondary_name, secondary in primary["secondaries"].items():
            for tertiary_name, tertiary in secondary["tertiaries"].items():
                for st in tertiary["stocks"]:
                    fields = (
                        st.get("concept1", ""),
                        st.get("concept2", ""),
                        st.get("concept3", ""),
                        st.get("concept4", ""),
                        st.get("concept5", ""),
                    )
                    has_any = False
                    for raw in fields:
                        # 兼容 - , ， 、 空格 ; 等多种分隔符
                        normalized = (
                            (raw or "")
                            .replace("-", ",")
                            .replace("，", ",")
                            .replace("、", ",")
                            .replace(" ", ",")
                            .replace(";", ",")
                        )
                        for piece in normalized.split(","):
                            piece = piece.strip()
                            if not piece:
                                continue
                            counts[piece] = counts.get(piece, 0) + 1
                            has_any = True
                    if has_any:
                        total_stocks_with_concept += 1

    # 按股票数倒序，同数量按名称升序
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return render_template(
        "pageJ.html",
        concepts=items,
        total_concepts=len(items),
        total_stocks=len({st["name"]
                          for p in INDUSTRY_TREE.values()
                          for s in p["secondaries"].values()
                          for t in s["tertiaries"].values()
                          for st in t["stocks"]}),
        total_stocks_with_concept=total_stocks_with_concept,
    )


def _concept_match(raw, concept_name):
    """
    判断字段值 raw（可能用 - , ， 、 空格 ; 等分隔）中是否包含概念 concept_name。
    """
    if not raw or not concept_name:
        return False
    normalized = (
        raw.replace("-", ",")
           .replace("，", ",")
           .replace("、", ",")
           .replace(" ", ",")
           .replace(";", ",")
    )
    for piece in normalized.split(","):
        if piece.strip() == concept_name:
            return True
    return False


@app.route("/pageH")
def page_h():
    """
    页面 H：按股票概念（概念1~5）展示股票列表。
    参数：name（概念名称）。
    """
    name = (request.args.get("name") or "").strip()
    concept = None
    stocks = []
    if name:
        for primary_name, primary in INDUSTRY_TREE.items():
            for secondary_name, secondary in primary["secondaries"].items():
                for tertiary_name, tertiary in secondary["tertiaries"].items():
                    for st in tertiary["stocks"]:
                        # 概念1~5 任一列包含该概念（按多种分隔符切分后匹配）
                        matched = any(
                            _concept_match(st.get(k), name)
                            for k in ("concept1", "concept2", "concept3", "concept4", "concept5")
                        )
                        if matched:
                            stocks.append(
                                {
                                    "name": st["name"],
                                    "code": st["code"],
                                    "primary": primary_name,
                                    "secondary": secondary_name,
                                    "tertiary": tertiary_name,
                                    "concept1": st.get("concept1", ""),
                                }
                            )
        # 概念统计：哪些概念在数据库中存在，方便用 existing
        # 这里仅取当前 name 作为唯一展示概念
        concept = {
            "name": name,
            "count": len(stocks),
        }

    # 按股票名称升序，便于查找
    stocks.sort(key=lambda s: s["name"])

    # 构造三级行业下拉选项（供「添加概念」面板使用）
    groups_full, _ = _build_full_groups()
    tertiary_options = []
    for g in groups_full:
        for s in g["secondaries"]:
            for t in s["tertiaries"]:
                tertiary_options.append({
                    "value": f'{g["name"]}||{s["name"]}||{t["name"]}',
                    "label": f'{g["name"]} / {s["name"]} / {t["name"]}',
                })

    # 按三级行业路径聚合股票名称（用于 datalist 自动补全）
    stocks_by_tertiary = {}
    for primary_name, primary in INDUSTRY_TREE.items():
        for secondary_name, secondary in primary["secondaries"].items():
            for tertiary_name, tertiary in secondary["tertiaries"].items():
                key = f"{primary_name}||{secondary_name}||{tertiary_name}"
                stocks_by_tertiary[key] = [
                    {"name": st["name"], "code": st.get("code", "")}
                    for st in tertiary["stocks"]
                ]

    return render_template(
        "pageH.html",
        concept=concept,
        stocks=stocks,
        query_name=name,
        tertiary_options=tertiary_options,
        stocks_by_tertiary=stocks_by_tertiary,
    )


@app.route("/pageC")
def page_c():
    """页面 C：与页面 B 完全相同（展示一级+二级+三级+股票）"""
    groups, stats = _build_full_groups()
    # 构造三级下拉选项：含路径 + 名称 + 当前股票数
    tertiary_options = []
    for g in groups:
        for s in g["secondaries"]:
            for t in s["tertiaries"]:
                tertiary_options.append(
                    {
                        "value": f'{g["name"]}||{s["name"]}||{t["name"]}',
                        "label": f'{g["name"]} / {s["name"]} / {t["name"]}（{t["stockCount"]} 只）',
                    }
                )
    # 在页面 C 通过 session 存储的新增记录（重启丢失）
    added = session_store.get("added", [])
    return render_template(
        "pageC.html",
        groups=groups,
        stats=stats,
        tertiary_options=tertiary_options,
        added=added,
    )


# ============== API ==============

@app.route("/api/stocks/search_batch", methods=["POST"])
def api_stocks_search_batch():
    """
    批量搜索多只股票（页面 F）。
    请求 JSON: {"q": "关键词串"}，关键词按换行 / 逗号 / 顿号 / 空格分隔。
    对每只股票做全字段模糊匹配（名称/代码/所在地/主营/概念/三级行业），
    返回所有至少命中一个关键词的股票，附命中关键词列表（名称命中的排前）。
    """
    data = request.get_json(silent=True) or {}
    raw = (data.get("q") or "").strip()
    # 分隔关键词：换行、中英文逗号、顿号、分号、空白
    keywords = [
        kw.strip().lower()
        for kw in raw.replace("，", ",").replace("、", ",").replace(";", ",")
        .replace("；", ",").replace("\n", ",").split(",")
        if kw.strip()
    ]
    if not keywords:
        return jsonify({"keywords": [], "items": [], "total": 0})

    items = []
    for primary_name, primary in INDUSTRY_TREE.items():
        for secondary_name, secondary in primary["secondaries"].items():
            for tertiary_name, tertiary in secondary["tertiaries"].items():
                for st in tertiary["stocks"]:
                    matched = [kw for kw in keywords
                               if _stock_hit_keyword(st, tertiary_name, kw)]
                    if not matched:
                        continue
                    items.append(
                        {
                            "name": st["name"],
                            "code": st["code"],
                            "location": st["location"],
                            "main_business": st["main_business"],
                            "concept1": st["concept1"],
                            "concept2": st["concept2"],
                            "concept3": st.get("concept3", ""),
                            "concept4": st.get("concept4", ""),
                            "concept5": st.get("concept5", ""),
                            "primary": primary_name,
                            "secondary": secondary_name,
                            "tertiary": tertiary_name,
                            "nameHit": any(kw in st["name"].lower() for kw in matched),
                            "matched": matched,
                        }
                    )
    # 名称命中优先
    items.sort(key=lambda r: 0 if r["nameHit"] else 1)
    return jsonify(
        {"keywords": keywords, "items": items, "total": len(items)}
    )


def _stock_hit_keyword(st, tertiary_name, kw):
    """判断股票 st 在全字段上是否包含关键词 kw（均为小写比较）"""
    fields = (
        st["name"], st["code"], st["location"], st["main_business"],
        st["concept1"], st["concept2"], tertiary_name,
    )
    return any(kw in (f or "").lower() for f in fields)

@app.route("/api/primaries")
def api_primaries():
    """一级行业列表（仅元信息）"""
    return jsonify(
        [
            serialize_primary(name, primary)
            for name, primary in INDUSTRY_TREE.items()
        ]
    )


@app.route("/api/primary/<path:primary_name>/secondaries")
def api_primary_secondaries(primary_name):
    """一级行业下的二级行业（含三级缩略）"""
    primary = INDUSTRY_TREE.get(primary_name)
    if primary is None:
        abort(404, description=f"未找到一级行业: {primary_name}")
    return jsonify(
        [
            serialize_secondary(name, secondary)
            for name, secondary in primary["secondaries"].items()
        ]
    )


@app.route(
    "/api/primary/<path:primary_name>/secondary/<path:secondary_name>/tertiaries"
)
def api_secondary_tertiaries(primary_name, secondary_name):
    """二级行业下的三级行业（含股票数）"""
    primary = INDUSTRY_TREE.get(primary_name)
    if primary is None:
        abort(404, description=f"未找到一级行业: {primary_name}")
    secondary = primary["secondaries"].get(secondary_name)
    if secondary is None:
        abort(404, description=f"未找到二级行业: {secondary_name}")
    return jsonify(
        [
            {
                "name": tname,
                "code": t["code"],
                "stockCount": len(t["stocks"]),
            }
            for tname, t in secondary["tertiaries"].items()
        ]
    )


@app.route(
    "/api/primary/<path:primary_name>/secondary/<path:secondary_name>/tertiary/<path:tertiary_name>/stocks"
)
def api_tertiary_stocks(primary_name, secondary_name, tertiary_name):
    """三级行业下的股票"""
    primary = INDUSTRY_TREE.get(primary_name)
    if primary is None:
        abort(404, description=f"未找到一级行业: {primary_name}")
    secondary = primary["secondaries"].get(secondary_name)
    if secondary is None:
        abort(404, description=f"未找到二级行业: {secondary_name}")
    tertiary = secondary["tertiaries"].get(tertiary_name)
    if tertiary is None:
        abort(404, description=f"未找到三级行业: {tertiary_name}")
    return jsonify(tertiary["stocks"])


# ============== 页面 C：新增股票（直接追加保存到 CSV 文件） ==============

# 本次运行新增的记录（用于 UI 列表展示 + reset 时识别要从 CSV 移除的行）
session_store = {"added": []}


def _find_tertiary(primary_name, secondary_name, tertiary_name):
    primary = INDUSTRY_TREE.get(primary_name)
    if primary is None:
        return None, None, None, f"未找到一级行业：{primary_name}"
    secondary = primary["secondaries"].get(secondary_name)
    if secondary is None:
        return None, None, None, f"未找到二级行业：{secondary_name}"
    tertiary = secondary["tertiaries"].get(tertiary_name)
    if tertiary is None:
        return None, None, None, f"未找到三级行业：{tertiary_name}"
    return primary, secondary, tertiary, None


def _insert_stock_row_to_csv(row):
    """
    把新股票行插入到 CSV 中同行业分组的末尾（保持行业聚集）。
    row: [一级编码, 一级代码, 一级行业, 二级代码, 二级行业, 三级代码, 三级行业, 股票名称, ...]
    定位方式：从后往前找第一行（一级/二级/三级行业名称都相同的行），插到它后面。
    若找不到该行业（理论上不会发生），退化为追加到文件末尾。
    """
    try:
        with open(DATA_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        print(f"[错误] 读取 CSV 失败: {e}")
        return False

    primary_name, secondary_name, tertiary_name = row[2], row[4], row[6]
    insert_at = None
    for i in range(len(rows) - 1, 0, -1):  # 跳过表头（索引 0）
        r = rows[i]
        if (
            len(r) >= 7
            and r[2] == primary_name
            and r[4] == secondary_name
            and r[6] == tertiary_name
        ):
            insert_at = i + 1
            break
    if insert_at is None:
        insert_at = len(rows)  # 兜底：追加到末尾

    rows.insert(insert_at, row)
    try:
        with open(DATA_FILE, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
    except OSError as e:
        print(f"[错误] 写入 CSV 失败: {e}")
        return False
    return True


def _rewrite_csv_without(removed_rows):
    """重写 CSV：读入全部行，剔除本次新增的行，再写回（保留 BOM）"""
    try:
        with open(DATA_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        keep = [r for r in rows if r not in removed_rows]
        with open(DATA_FILE, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(keep)
        return len(rows) - len(keep)
    except OSError as e:
        print(f"[错误] 重写 CSV 失败: {e}")
        return 0


@app.route("/api/pageC/add_stock", methods=["POST"])
def api_page_c_add_stock():
    """为指定三级行业新增一只股票：追加一行到 CSV 文件 + 更新内存树"""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()
    location = (data.get("location") or "").strip()
    main_business = (data.get("main_business") or "").strip()

    if not path or "||" not in path:
        return jsonify({"ok": False, "error": "请选择三级行业"}), 400
    if not name:
        return jsonify({"ok": False, "error": "请填写股票名称"}), 400

    parts = path.split("||")
    if len(parts) != 3:
        return jsonify({"ok": False, "error": "三级行业路径不合法"}), 400
    primary_name, secondary_name, tertiary_name = parts

    primary, secondary, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404

    new_stock = {
        "name": name,
        "code": code,
        "location": location,
        "main_business": main_business,
    }

    # 组装与表头顺序一致的 14 列 CSV 行（一级编码 A1/A2...，一级代码 881001...）
    csv_row = [
        primary.get("acode", ""),      # 一级编码
        primary.get("code", ""),       # 一级代码
        primary_name,                  # 一级行业(30)
        secondary.get("code", ""),     # 二级代码
        secondary_name,                # 二级行业(128)
        tertiary.get("code", ""),      # 三级代码
        tertiary_name,                 # 三级行业(345)
        name,                          # 股票名称
        code,                          # 股票代码
        location,                      # 所在地
        main_business,                 # 主营
        "",                            # 概念1
        "",                            # 概念2
        "",                            # 概念3
        "",                            # 概念4
        "",                            # 概念5
    ]

    # 先写盘（插入到同行业分组末尾），成功后再更新内存树
    if not _insert_stock_row_to_csv(csv_row):
        return jsonify({"ok": False, "error": "写入 CSV 文件失败，请检查文件权限"}), 500

    tertiary["stocks"].append(new_stock)

    record = {
        "primary": primary_name,
        "secondary": secondary_name,
        "tertiary": tertiary_name,
        "stock": new_stock,
        "csv_row": csv_row,
    }
    session_store["added"].append(record)

    return jsonify(
        {
            "ok": True,
            "record": {
                "primary": primary_name,
                "secondary": secondary_name,
                "tertiary": tertiary_name,
                "stock": new_stock,
            },
            "stockCount": len(tertiary["stocks"]),
        }
    )


@app.route("/api/stock/update_concepts", methods=["POST"])
def api_update_stock_concepts():
    """
    更新一只股票的全部可编辑字段（页面 F 编辑功能）。
    请求 JSON: {
        "path": "一级||二级||三级",      # 行业路径
        "name": "原股票名称",             # 用于定位
        "fields": {                       # 任意可编辑字段
            "name": "...",                # 新的股票名称（可选）
            "code": "...",
            "location": "...",
            "main_business": "...",
            "concept1": "...",
            "concept2": "...",
            "concept3": "...",
            "concept4": "...",
            "concept5": "...",
        }
    }
    通过 行业路径 + 股票名称 唯一定位（兼容无代码的股票）。
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        return jsonify({"ok": False, "error": "fields 必须为对象"}), 400

    if "||" not in path or path.count("||") != 2:
        return jsonify({"ok": False, "error": "行业路径不合法"}), 400
    if not name:
        return jsonify({"ok": False, "error": "股票名称不能为空"}), 400

    # 白名单字段
    allowed = {
        "name", "code", "location", "main_business",
        "concept1", "concept2", "concept3", "concept4", "concept5",
    }
    update = {}
    for k in allowed:
        if k in fields:
            update[k] = (fields.get(k) or "").strip()
    if not update:
        return jsonify({"ok": False, "error": "没有可更新字段"}), 400

    parts = path.split("||")
    primary_name, secondary_name, tertiary_name = parts
    primary, secondary, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404

    target = None
    for st in tertiary["stocks"]:
        if st["name"] == name:
            target = st
            break
    if target is None:
        return jsonify({"ok": False, "error": "未找到该股票"}), 404

    # 若修改了名称，先检查新名称在同一三级下是否冲突
    new_name = update.get("name", name)
    if new_name and new_name != name:
        for st in tertiary["stocks"]:
            if st is not target and st["name"] == new_name:
                return jsonify({"ok": False, "error": f"新股票名称与「{new_name}」冲突"}), 400

    # 写回 CSV：按行业路径 + 股票名称定位行
    try:
        with open(DATA_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        return jsonify({"ok": False, "error": f"读取 CSV 失败: {e}"}), 500

    header = rows[0] if rows else []
    # 追加新概念列（如需要）
    for new_col in ("概念3", "概念4", "概念5"):
        if new_col not in header:
            header.append(new_col)
    rows[0] = header
    # 补齐所有数据行
    for i in range(1, len(rows)):
        if len(rows[i]) < len(header):
            rows[i].extend([""] * (len(header) - len(rows[i])))

    # 列索引（已知列）
    col_map = {
        "name": header.index("股票名称"),
        "code": header.index("股票代码"),
        "location": header.index("所在地"),
        "main_business": header.index("主营"),
        "concept1": header.index("概念1"),
        "concept2": header.index("概念2"),
        "concept3": header.index("概念3"),
        "concept4": header.index("概念4"),
        "concept5": header.index("概念5"),
    }
    idx_primary = header.index("一级行业(30)")
    idx_secondary = header.index("二级行业(128)")
    idx_tertiary = header.index("三级行业(345)")

    found_idx = -1
    for i in range(1, len(rows)):
        r = rows[i]
        if (
            len(r) > max(idx_primary, idx_secondary, idx_tertiary, col_map["name"])
            and r[idx_primary] == primary_name
            and r[idx_secondary] == secondary_name
            and r[idx_tertiary] == tertiary_name
            and r[col_map["name"]] == name
        ):
            found_idx = i
            break

    if found_idx < 0:
        return jsonify({"ok": False, "error": "CSV 中未找到该股票行"}), 500

    # 写回
    for k, v in update.items():
        rows[found_idx][col_map[k]] = v
        # 同步更新内存树
        if k in target:
            target[k] = v

    try:
        with open(DATA_FILE, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
    except OSError as e:
        return jsonify({"ok": False, "error": f"写入 CSV 失败: {e}"}), 500

    return jsonify(
        {
            "ok": True,
            "stock": {
                "name": target["name"],
                "code": target.get("code", ""),
                "location": target.get("location", ""),
                "main_business": target.get("main_business", ""),
                "concept1": target.get("concept1", ""),
                "concept2": target.get("concept2", ""),
                "concept3": target.get("concept3", ""),
                "concept4": target.get("concept4", ""),
                "concept5": target.get("concept5", ""),
            },
        }
    )


@app.route("/api/pageC/add_stocks_batch", methods=["POST"])
def api_page_c_add_stocks_batch():
    """批量为指定三级行业新增多只股票（每行一个名称），一次写盘插入到分组末尾"""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    names_text = (data.get("names") or "").strip()

    if not path or "||" not in path:
        return jsonify({"ok": False, "error": "请选择三级行业"}), 400

    # 解析名称：按换行分隔，去空、去重（保持顺序）
    names = []
    seen = set()
    for line in names_text.splitlines():
        n = line.strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    if not names:
        return jsonify({"ok": False, "error": "请填写至少一个股票名称（每行一个）"}), 400
    if len(names) > 500:
        return jsonify({"ok": False, "error": "单次最多批量新增 500 只"}), 400

    parts = path.split("||")
    if len(parts) != 3:
        return jsonify({"ok": False, "error": "三级行业路径不合法"}), 400
    primary_name, secondary_name, tertiary_name = parts

    primary, secondary, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404

    # 组装多条 14 列 CSV 行
    csv_rows = []
    for n in names:
        csv_rows.append(
            [
                primary.get("acode", ""),      # 一级编码
                primary.get("code", ""),       # 一级代码
                primary_name,                  # 一级行业(30)
                secondary.get("code", ""),     # 二级代码
                secondary_name,                # 二级行业(128)
                tertiary.get("code", ""),      # 三级代码
                tertiary_name,                 # 三级行业(345)
                n,                             # 股票名称
                "",                            # 股票代码
                "",                            # 所在地
                "",                            # 主营
                "", "", "",                    # 概念
            ]
        )

    # 一次性读文件、找到分组末尾、批量插入、一次写回
    try:
        with open(DATA_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        insert_at = None
        for i in range(len(rows) - 1, 0, -1):
            r = rows[i]
            if (
                len(r) >= 7
                and r[2] == primary_name
                and r[4] == secondary_name
                and r[6] == tertiary_name
            ):
                insert_at = i + 1
                break
        if insert_at is None:
            insert_at = len(rows)
        rows[insert_at:insert_at] = csv_rows
        with open(DATA_FILE, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
    except OSError as e:
        print(f"[错误] 批量写入 CSV 失败: {e}")
        return jsonify({"ok": False, "error": "写入 CSV 文件失败，请检查文件权限"}), 500

    # 更新内存树 + 记录
    records = []
    for n, csv_row in zip(names, csv_rows):
        new_stock = {"name": n, "code": "", "location": "", "main_business": ""}
        tertiary["stocks"].append(new_stock)
        record = {
            "primary": primary_name,
            "secondary": secondary_name,
            "tertiary": tertiary_name,
            "stock": new_stock,
            "csv_row": csv_row,
        }
        session_store["added"].append(record)
        records.append(
            {
                "primary": primary_name,
                "secondary": secondary_name,
                "tertiary": tertiary_name,
                "stock": new_stock,
            }
        )

    return jsonify(
        {
            "ok": True,
            "count": len(records),
            "records": records,
            "stockCount": len(tertiary["stocks"]),
        }
    )


@app.route("/api/pageC/added")
def api_page_c_added():
    """返回本次运行新增的股票记录（不含历史）"""
    return jsonify(
        [
            {
                "primary": r["primary"],
                "secondary": r["secondary"],
                "tertiary": r["tertiary"],
                "stock": r["stock"],
            }
            for r in session_store.get("added", [])
        ]
    )


@app.route("/api/pageC/reset", methods=["POST"])
def api_page_c_reset():
    """撤销本次运行新增的全部股票：从内存树移除并从 CSV 文件删除对应行"""
    removed_rows = [r["csv_row"] for r in session_store["added"] if "csv_row" in r]
    removed = 0
    for record in session_store["added"]:
        _, _, tertiary, _ = _find_tertiary(
            record.get("primary"), record.get("secondary"), record.get("tertiary")
        )
        if tertiary is not None and record.get("stock") in tertiary["stocks"]:
            tertiary["stocks"].remove(record["stock"])
            removed += 1
    session_store["added"] = []
    if removed_rows:
        _rewrite_csv_without(removed_rows)
    return jsonify({"ok": True, "removed": removed})


if __name__ == "__main__":
    import os as _os
    port = int(_os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=True)
