"""
通达信行业分类 Web 系统
- 首页仅展示 30 个一级行业卡片
- 点击一级卡片 -> 异步加载其下二级行业
- 点击二级 -> 异步加载其下三级行业
- 点击三级 -> 异步加载对应股票

数据存储：SQLite（data/tongdaxin.db），由 db.py 提供访问层
"""
import csv  # noqa: F401  （保留 csv 以便兼容旧代码）
import os
from collections import OrderedDict
from typing import Any, Dict

from flask import Flask, jsonify, render_template, abort, request

from db import (
    DB_FILE,
    CSV_FILE as DATA_FILE,
    find_stock_by_name,
    get_conn,
    init_db_from_csv,
    load_industry_tree,
    update_stock_concept,
)

app = Flask(__name__)

# 启动期检测：若 SQLite 数据库不存在则从 CSV 自动迁移
if not os.path.exists(DB_FILE):
    if os.path.exists(DATA_FILE):
        init_db_from_csv()
    else:
        raise FileNotFoundError(
            f"数据库 {DB_FILE} 不存在，且找不到 CSV 源文件 {DATA_FILE}"
        )

# 增量迁移：确保 concepts 概念表存在（旧数据库升级）
from db import ensure_concepts_table  # noqa: E402
ensure_concepts_table()

# 模块级 INDUSTRY_TREE，启动时一次性从 SQLite 加载到内存；写入时同步刷新
INDUSTRY_TREE: Dict[str, dict] = load_industry_tree()


def reload_industry_tree():
    """从 SQLite 重新加载整棵树（在写入操作后调用，保持内存与磁盘一致）。"""
    global INDUSTRY_TREE
    INDUSTRY_TREE = load_industry_tree()
    return INDUSTRY_TREE


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

    # 写入策略：直接更新 SQLite stocks 表的 concept 列（用 '-' 串接）
    try:
        new_concept = update_stock_concept(
            stock_name=stock_name,
            new_concept=concept,
        )
    except (LookupError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库写入失败：{e}"}), 500

    target_value = new_concept

    # 同步更新内存树（重新加载以保持数据一致）
    reload_industry_tree()
    # 当前 target 引用指向旧树，刷新一下 target 引用
    primary = INDUSTRY_TREE.get(primary_name)
    if primary:
        secondary = primary["secondaries"].get(secondary_name)
        if secondary:
            tertiary = secondary["tertiaries"].get(tertiary_name)
            if tertiary:
                for s in tertiary["stocks"]:
                    if s["name"] == stock_name:
                        target = s
                        break

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


@app.route("/pageL")
def page_l():
    """
    页面 L：以页面 B 的左列布局展示所有概念的层级树及其关联股票。
    左列为一级概念（含股票数统计，点击可跳页面 H），右列为嵌套的
    子概念层级 + 该概念下的股票（仅名称）。
    """
    from db import _split_concept_raw, list_concepts

    # 1) 按概念名聚合股票（仅名称）
    stocks_by_concept: Dict[str, list] = {}
    stock_names = set()
    for primary_name, primary in INDUSTRY_TREE.items():
        for secondary_name, secondary in primary["secondaries"].items():
            for tertiary_name, tertiary in secondary["tertiaries"].items():
                for st in tertiary["stocks"]:
                    stock_names.add(st["name"])
                    seen = set()
                    for key in ("concept1", "concept2", "concept3",
                                "concept4", "concept5"):
                        for piece in _split_concept_raw(st.get(key)):
                            if piece not in seen:
                                seen.add(piece)
                                stocks_by_concept.setdefault(piece, []).append(
                                    st["name"]
                                )

    # 2) 依据概念表构建层级树
    concepts = list_concepts()  # 树形顺序，含 {id, name, level, parent_id, path}
    nodes: Dict[Any, dict] = {}
    roots: list = []
    for c in concepts:
        node = {
            "name": c["name"],
            "level": c["level"] or 1,
            "count": len(stocks_by_concept.get(c["name"], [])),
            "total": 0,
            "children": [],
        }
        nodes[c["id"]] = node
        parent = c.get("parent_id")
        if parent and parent in nodes:
            nodes[parent]["children"].append(node)
        else:
            roots.append(node)

    # 3) 未入概念表但被股票使用的概念，作为根节点补充展示
    known = set()
    for c in concepts:
        known.add(c["name"])

    def _finalize(node):
        node["total"] = node["count"] + sum(_finalize(ch) for ch in node["children"])
        node["children"].sort(key=lambda ch: (-ch["total"], ch["name"]))
        node["stocks"] = stocks_by_concept.get(node["name"], [])
        return node["total"]

    for node in roots:
        _finalize(node)
    for name in sorted(stocks_by_concept):
        if name not in known:
            stocks = stocks_by_concept[name]
            roots.append(
                {
                    "name": name,
                    "level": 1,
                    "count": len(stocks),
                    "total": len(stocks),
                    "children": [],
                    "stocks": stocks,
                }
            )

    roots.sort(key=lambda nd: (-nd["total"], nd["name"]))

    return render_template(
        "pageL.html",
        roots=roots,
        stats={
            "concepts": sum(1 + _count_desc(nd) for nd in roots),
            "stocks": len(stock_names),
            "stocks_with_concept": len(
                {s for lst in stocks_by_concept.values() for s in lst}
            ),
        },
    )


def _count_desc(node):
    return sum(1 + _count_desc(ch) for ch in node["children"])


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
    （已迁移至 SQLite）兼容占位：新股票插入改为调用 db.add_stock。
    row 参数保留旧格式 [一级编码, ..., 三级行业, 股票名称, 股票代码, 所在地, 主营, ...]。
    """
    from db import add_stock, find_tertiary_id
    primary_name, secondary_name, tertiary_name = row[2], row[4], row[6]
    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return False
    try:
        add_stock(
            tid,
            name=row[7],
            code=row[8] if len(row) > 8 else "",
            location=row[9] if len(row) > 9 else "",
            main_business=row[10] if len(row) > 10 else "",
            concept=row[11] if len(row) > 11 else "",
        )
    except Exception as e:
        print(f"[错误] SQLite 新增股票失败: {e}")
        return False
    return True


def _rewrite_csv_without(removed_rows):
    """
    （已迁移至 SQLite）兼容占位：删除股票改为调用 db.delete_stock。
    removed_rows 中每行为旧 CSV 格式，取 [7]=股票名称, [2]/[4]/[6]=行业路径。
    """
    from db import delete_stock, find_tertiary_id
    removed = 0
    for r in removed_rows:
        if not r or len(r) < 8:
            continue
        tid = find_tertiary_id(r[2], r[4], r[6])
        try:
            removed += delete_stock(r[7], tertiary_id=tid)
        except Exception as e:
            print(f"[错误] SQLite 删除股票失败: {e}")
    return removed


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

    # 写入 SQLite
    from db import add_stock, find_tertiary_id
    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return jsonify({"ok": False, "error": "SQLite 中未找到该三级行业"}), 404
    try:
        add_stock(
            tid,
            name=name,
            code=code,
            location=location,
            main_business=main_business,
            concept="",
        )
    except Exception as e:
        print(f"[错误] SQLite 新增股票失败: {e}")
        return jsonify({"ok": False, "error": f"写入数据库失败: {e}"}), 500

    tertiary["stocks"].append(new_stock)

    record = {
        "primary": primary_name,
        "secondary": secondary_name,
        "tertiary": tertiary_name,
        "stock": new_stock,
        "tertiary_id": tid,
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

    # 写 SQLite
    from db import find_tertiary_id, update_stock
    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return jsonify({"ok": False, "error": "SQLite 中未找到该三级行业"}), 404

    try:
        _, final_name = update_stock(tid, name, update)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库写入失败: {e}"}), 500

    # 同步更新内存树（简单做法：重新加载）
    reload_industry_tree()
    # 从新树中定位该股票以返回最新字段
    primary2 = INDUSTRY_TREE.get(primary_name)
    target = None
    if primary2:
        secondary2 = primary2["secondaries"].get(secondary_name)
        if secondary2:
            tertiary2 = secondary2["tertiaries"].get(tertiary_name)
            if tertiary2:
                for s in tertiary2["stocks"]:
                    if s["name"] == final_name:
                        target = s
                        break

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

    # 组装批量插入数据（SQLite）
    from db import add_stocks_batch, find_tertiary_id
    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return jsonify({"ok": False, "error": "SQLite 中未找到该三级行业"}), 404

    stock_list = [{"name": n, "code": "", "location": "", "main_business": "", "concept": ""} for n in names]
    try:
        inserted = add_stocks_batch(tid, stock_list)
    except Exception as e:
        print(f"[错误] 批量写入 SQLite 失败: {e}")
        return jsonify({"ok": False, "error": f"写入数据库失败: {e}"}), 500

    # 更新内存树 + 记录
    records = []
    for n, s_data in zip(names, stock_list):
        new_stock = {"name": n, "code": "", "location": "", "main_business": ""}
        tertiary["stocks"].append(new_stock)
        record = {
            "primary": primary_name,
            "secondary": secondary_name,
            "tertiary": tertiary_name,
            "stock": new_stock,
            "tertiary_id": tid,
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
    """撤销本次运行新增的全部股票：从内存树移除并从 SQLite 删除对应行"""
    from db import delete_stock, find_tertiary_id
    removed = 0
    for record in session_store["added"]:
        _, _, tertiary, _ = _find_tertiary(
            record.get("primary"), record.get("secondary"), record.get("tertiary")
        )
        stock_name = (record.get("stock") or {}).get("name", "")
        # 按名称从内存树移除（编辑操作可能重载内存树，对象引用会失效）
        if tertiary is not None and stock_name:
            before = len(tertiary["stocks"])
            tertiary["stocks"] = [s for s in tertiary["stocks"] if s.get("name") != stock_name]
            if len(tertiary["stocks"]) < before:
                removed += 1
        # SQLite 删除
        tid = find_tertiary_id(
            record.get("primary"), record.get("secondary"), record.get("tertiary")
        )
        if tid is not None and stock_name:
            try:
                delete_stock(stock_name, tertiary_id=tid)
            except Exception as e:
                print(f"[错误] SQLite 删除失败: {e}")
    session_store["added"] = []
    return jsonify({"ok": True, "removed": removed})


# ==================== 页面 K：概念表编辑 ====================

@app.route("/pageK")
def page_k():
    """页面 K：概念字典表编辑（增/改/删/导入，可选同步股票数据）"""
    from db import list_concepts
    concepts = list_concepts()
    return render_template("pageK.html", concepts=concepts)


@app.route("/api/pageK/add_concept", methods=["POST"])
def api_page_k_add_concept():
    """新增概念 {"name": "...", "description": "...", "parent_id": 1 或 null}"""
    from db import add_concept
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    parent_id = data.get("parent_id")
    if parent_id in ("", None):
        parent_id = None
    else:
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "上级分类 id 无效"}), 400

    if not name:
        return jsonify({"ok": False, "error": "概念名称不能为空"}), 400
    try:
        new_id = add_concept(name, description, parent_id=parent_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库写入失败：{e}"}), 500
    return jsonify({"ok": True, "id": new_id, "name": name})


@app.route("/api/pageK/update_concept", methods=["POST"])
def api_page_k_update_concept():
    """
    编辑概念 {"id": 1, "name": "新名称", "description": "...", "parent_id": 2, "sync_stocks": true}
    parent_id 可省略表示保持原上级；传 null 表示移到根（一级）。
    sync_stocks=true 时，把所有股票概念串中的旧名称替换为新名称。
    """
    from db import update_concept, sync_concept_in_stocks
    data = request.get_json(silent=True) or {}
    concept_id = data.get("id")
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    sync = bool(data.get("sync_stocks"))

    # parent_id：缺省 → 保持；null/"" → 根；否则整数上级 id
    parent_id = data.get("parent_id", "__keep__")
    if parent_id is None or parent_id == "":
        parent_id = None
    elif parent_id != "__keep__":
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "上级分类 id 无效"}), 400

    if concept_id is None:
        return jsonify({"ok": False, "error": "缺少概念 id"}), 400

    try:
        old_name, new_name = update_concept(
            concept_id,
            name=name or None,
            description=description,
            parent_id=parent_id,
        )
    except (LookupError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库写入失败：{e}"}), 500

    synced = 0
    if sync and new_name != old_name:
        try:
            synced = sync_concept_in_stocks(old_name, new_name)
            reload_industry_tree()
        except Exception as e:
            return jsonify(
                {"ok": False, "error": f"概念已更新，但同步股票失败：{e}"}
            ), 500

    return jsonify({
        "ok": True,
        "old_name": old_name,
        "new_name": new_name,
        "synced_stocks": synced,
    })


@app.route("/api/pageK/delete_concept", methods=["POST"])
def api_page_k_delete_concept():
    """
    删除概念 {"id": 1, "sync_stocks": true}
    级联删除其所有子孙分类。sync_stocks=true 时，把被删除的所有概念从股票串中移除。
    """
    from db import delete_concept, sync_concept_in_stocks
    data = request.get_json(silent=True) or {}
    concept_id = data.get("id")
    sync = bool(data.get("sync_stocks"))

    if concept_id is None:
        return jsonify({"ok": False, "error": "缺少概念 id"}), 400

    try:
        names = delete_concept(concept_id)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库删除失败：{e}"}), 500

    synced = 0
    if sync:
        try:
            for n in names:
                synced += sync_concept_in_stocks(n, new_name=None)
            reload_industry_tree()
        except Exception as e:
            return jsonify(
                {"ok": False, "error": f"概念已删除，但同步股票失败：{e}"}
            ), 500

    return jsonify({"ok": True, "names": names, "synced_stocks": synced})


@app.route("/api/pageK/import_from_stocks", methods=["POST"])
def api_page_k_import_from_stocks():
    """把股票表中出现的所有概念导入概念表（去重，已存在跳过）"""
    from db import import_concepts_from_stocks
    try:
        added = import_concepts_from_stocks()
    except Exception as e:
        return jsonify({"ok": False, "error": f"导入失败：{e}"}), 500
    return jsonify({"ok": True, "added": added})


@app.route("/api/pageK/concept_stocks")
def api_page_k_concept_stocks():
    """查询某概念下已使用的股票列表 ?name=火电"""
    from db import get_concept_stocks
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "缺少概念名称"}), 400
    try:
        stocks = get_concept_stocks(name)
    except Exception as e:
        return jsonify({"ok": False, "error": f"查询失败：{e}"}), 500
    return jsonify({"ok": True, "name": name, "stocks": stocks})


@app.route("/api/pageK/all_stocks")
def api_page_k_all_stocks():
    """所有股票扁平列表（供添加股票时的 datalist 自动补全）"""
    from db import get_all_stocks_flat
    try:
        stocks = get_all_stocks_flat()
    except Exception as e:
        return jsonify({"ok": False, "error": f"查询失败：{e}"}), 500
    return jsonify({"ok": True, "stocks": stocks})


@app.route("/api/pageK/add_stocks_to_concept", methods=["POST"])
def api_page_k_add_stocks_to_concept():
    """
    为一批已存在的股票添加某概念。
    请求: {"name": "火电", "stocks": ["股票A", "股票B"]}
    - 概念不存在于概念表时自动创建
    - 已拥有该概念的股票自动跳过
    返回 {"ok": true, "added": [...], "skipped": [...], "not_found": [...]}
    """
    from db import _split_concept_raw, add_concept, update_stock_concept
    data = request.get_json(silent=True) or {}
    concept_name = (data.get("name") or "").strip()
    stock_names = data.get("stocks") or []
    if isinstance(stock_names, str):
        stock_names = [s.strip() for s in stock_names.replace("，", ",").split(",")]
    stock_names = [s.strip() for s in stock_names if s.strip()]

    if not concept_name:
        return jsonify({"ok": False, "error": "缺少概念名称"}), 400
    if not stock_names:
        return jsonify({"ok": False, "error": "请填写至少一个股票名称"}), 400

    # 确保概念存在于概念表（不存在则自动创建）
    conn_ok = True
    try:
        add_concept(concept_name, "")
    except ValueError:
        pass  # 已存在，正常
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库写入失败：{e}"}), 500

    added, skipped, not_found = [], [], []
    for sn in stock_names:
        try:
            new_val = update_stock_concept(stock_name=sn, new_concept=concept_name)
            added.append({"name": sn, "concept": new_val})
        except LookupError:
            not_found.append(sn)
        except ValueError:
            skipped.append(sn)  # 已有此概念
        except Exception as e:
            return jsonify({"ok": False, "error": f"为 {sn} 写入失败：{e}"}), 500

    if added:
        reload_industry_tree()

    return jsonify({
        "ok": True,
        "added": added,
        "skipped": skipped,
        "not_found": not_found,
    })


@app.route("/api/pageK/remove_stock_from_concept", methods=["POST"])
def api_page_k_remove_stock_from_concept():
    """
    从指定股票移除某概念。
    请求: {"name": "火电", "stock": "郑州煤电"}
    返回 {"ok": true, "new_concept": "机器人"}
    """
    from db import remove_concept_from_stock
    data = request.get_json(silent=True) or {}
    concept_name = (data.get("name") or "").strip()
    stock_name = (data.get("stock") or "").strip()

    if not concept_name or not stock_name:
        return jsonify({"ok": False, "error": "缺少概念或股票名称"}), 400

    try:
        new_val, changed = remove_concept_from_stock(stock_name, concept_name)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库写入失败：{e}"}), 500

    if changed:
        reload_industry_tree()

    return jsonify({"ok": True, "new_concept": new_val, "changed": changed})


if __name__ == "__main__":
    import os as _os
    port = int(_os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=True)
