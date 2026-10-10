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
                        "concept1": (row.get("概念1") or "").strip(),
                        "concept2": (row.get("概念2") or "").strip(),
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
    """页面 D：与页面 B 完全相同（展示一级+二级+三级+股票），股票名称可跳转页面 E"""
    groups, stats = _build_full_groups()
    return render_template("pageD.html", groups=groups, stats=stats)


@app.route("/pageE")
def page_e():
    """页面 E：按股票代码展示单只股票的详细信息（含行业路径）"""
    code = (request.args.get("code") or "").strip()
    name = (request.args.get("name") or "").strip()
    stock = None
    path = None
    if code or name:
        for primary_name, primary in INDUSTRY_TREE.items():
            for secondary_name, secondary in primary["secondaries"].items():
                for tertiary_name, tertiary in secondary["tertiaries"].items():
                    for st in tertiary["stocks"]:
                        if (code and st["code"] == code) or (
                            not code and name and st["name"] == name
                        ):
                            stock = st
                            path = {
                                "primary": primary_name,
                                "primaryCode": primary["code"],
                                "secondary": secondary_name,
                                "secondaryCode": secondary["code"],
                                "tertiary": tertiary_name,
                                "tertiaryCode": tertiary["code"],
                            }
                            break
                    if stock:
                        break
                if stock:
                    break
            if stock:
                break
    if stock is None:
        # 未找到（或未传参数）时给出提示页
        return render_template(
            "pageE.html", stock=None, path=None,
            query={"code": code, "name": name},
        )
    return render_template("pageE.html", stock=stock, path=path, query=None)


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
        "",                            # 概念2
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
