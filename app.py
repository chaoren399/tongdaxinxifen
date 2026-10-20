"""
通达信行业分类 Web 系统
- 首页仅展示 30 个一级行业卡片
- 点击一级卡片 -> 异步加载其下二级行业
- 点击二级 -> 异步加载其下三级行业
- 点击三级 -> 异步加载对应股票

数据存储：SQLite（data/tongdaxin.db），由 db.py 提供访问层
"""
import csv  # noqa: F401  （保留 csv 以便兼容旧代码）
import gzip
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict

from flask import Flask, jsonify, render_template, abort, request, send_from_directory

from db import (
    CONCEPT_PATH_SEP,
    DB_FILE,
    CSV_FILE as DATA_FILE,
    _split_concept_raw,
    find_stock_by_name,
    get_conn,
    init_db_from_csv,
    load_industry_tree,
    update_stock_concept,
)

app = Flask(__name__)

# 股票图片：最大 8 MB，仅允许常见图片格式
STOCK_IMAGES_DIR = os.path.join(os.path.dirname(DB_FILE), "stock_images")
os.makedirs(STOCK_IMAGES_DIR, exist_ok=True)
app.config["STOCK_IMAGES_DIR"] = STOCK_IMAGES_DIR
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# 单只股票最多可保存的图片张数
MAX_IMAGES_PER_STOCK = 20


def _images_payload(filenames):
    """把文件名列表转成前端使用的 [{filename, url}] 结构。"""
    return [
        {"filename": fn, "url": f"/stock_images/{fn}"}
        for fn in (filenames or [])
    ]

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

# 增量迁移：确保 stocks 表存在「选项A ~ 选项H」8 个列（旧数据库升级）
from db import ensure_stocks_columns, STOCK_OPTION_FIELDS  # noqa: E402
ensure_stocks_columns()

# 增量迁移：确保 stock_images 多图表存在，并把旧的单图迁移进来
from db import ensure_stock_images_table  # noqa: E402
ensure_stock_images_table()

# 增量迁移：物理删除已废弃的 stocks.location / main_business / image_path 三列
from db import drop_obsolete_stock_columns  # noqa: E402
drop_obsolete_stock_columns()

# 增量迁移：确保 stock_modification_log 修改日志表存在
from db import (  # noqa: E402
    ensure_stock_modification_log_table,
    get_stock_modification_dates,
    get_businesses_for_stock_ids,
)
ensure_stock_modification_log_table()

# 增量迁移：确保 stock_concept_businesses 业务描述表存在
from db import ensure_stock_concept_businesses_table  # noqa: E402
ensure_stock_concept_businesses_table()

# 增量迁移：确保 stock_concepts（股票×概念加入时间）表存在，首次建表会回填历史
from db import ensure_stock_concepts_table  # noqa: E402
ensure_stock_concepts_table()

# 增量迁移：确保 search_history 搜索历史表存在
from db import (  # noqa: E402
    ensure_search_history_table,
    get_recent_search_history,
    list_search_history,
    clear_search_history,
    prune_search_history,
    record_search_history_batch,
    SEARCH_HISTORY_RETENTION_DAYS,
)
ensure_search_history_table()

# 模块级 INDUSTRY_TREE，启动时一次性从 SQLite 加载到内存；写入时同步刷新
INDUSTRY_TREE: Dict[str, dict] = load_industry_tree()


def reload_industry_tree():
    """从 SQLite 重新加载整棵树（在写入操作后调用，保持内存与磁盘一致）。"""
    global INDUSTRY_TREE
    INDUSTRY_TREE = load_industry_tree()
    _invalidate_caches()
    return INDUSTRY_TREE


# ====== 数据/渲染缓存 ======
# 数据代数：内存树重载或被直接修改时 +1，使所有缓存自动失效
_DATA_GEN = 0
_ADDED_VERSION = 0                 # 页面 C「本次新增」列表版本（新增/撤销时 +1）
_GROUPS_CACHE = {"gen": -1, "value": None}   # _build_full_groups() 结果缓存
_RENDER_CACHE = {}                # 渲染结果缓存：key=(模板, 版本key, 数据代数)


def _invalidate_caches(bump_added: bool = False):
    """使缓存失效：任何写操作后调用。bump_added 表示页面 C 新增记录有变化。"""
    global _DATA_GEN, _ADDED_VERSION
    _DATA_GEN += 1
    _RENDER_CACHE.clear()
    if bump_added:
        _ADDED_VERSION += 1


def _build_full_groups_cached():
    """带代数缓存的 _build_full_groups()：数据未变化时直接复用上次结果。"""
    if _GROUPS_CACHE["gen"] != _DATA_GEN:
        _GROUPS_CACHE["gen"] = _DATA_GEN
        _GROUPS_CACHE["value"] = _build_full_groups()
    return _GROUPS_CACHE["value"]


def _cached_render(template: str, version_key, render_fn):
    """渲染缓存：相同 (模板, 版本key, 数据代数) 直接返回上次渲染的 HTML。"""
    key = (template, version_key, _DATA_GEN)
    html = _RENDER_CACHE.get(key)
    if html is None:
        html = render_fn()
        _RENDER_CACHE[key] = html
    return html


# ====== 请求耗时统计 ======
_REQUEST_START = {}


@app.before_request
def _timing_before():
    _REQUEST_START[threading.get_ident()] = time.perf_counter()


@app.after_request
def _timing_after(response):
    start = _REQUEST_START.pop(threading.get_ident(), None)
    if start is not None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        size = response.calculate_content_length() or 0
        size_kb = size / 1024
        size_text = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.2f} MB"
        app.logger.info(
            "[%s] %s -> %s  %.1f ms  %s%s",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
            size_text,
            "  (gzip)" if response.headers.get("Content-Encoding") == "gzip" else "",
        )
    return response


class _GzipMiddleware:
    """轻量 gzip 压缩中间件：对文本类响应（HTML/JS/CSS/JSON）启用压缩。
    响应在内存中缓冲后压缩，适用于本项目的开发/小型部署场景。"""

    COMPRESSIBLE_PREFIXES = ("text/", "application/json", "application/javascript")
    MIN_SIZE = 1024

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        accept = (environ.get("HTTP_ACCEPT_ENCODING") or "").lower()
        if "gzip" not in accept:
            return self.wsgi_app(environ, start_response)

        captured = {}

        def _capture(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = list(headers)
            return lambda data: None

        body = b"".join(
            chunk
            for chunk in self.wsgi_app(environ, _capture)
            if isinstance(chunk, bytes)
        )

        headers = captured["headers"]
        has_encoding = any(
            k.lower() == "content-encoding" for k, _ in headers
        )
        content_type = next(
            (v for k, v in headers if k.lower() == "content-type"), ""
        )
        compressible = content_type.lower().startswith(self.COMPRESSIBLE_PREFIXES)

        if has_encoding or not compressible or len(body) < self.MIN_SIZE:
            start_response(captured["status"], headers)
            return [body]

        gzipped = gzip.compress(body, 6)
        headers = [
            (k, v)
            for k, v in headers
            if k.lower() not in ("content-length", "content-encoding")
        ]
        headers.append(("Content-Encoding", "gzip"))
        headers.append(("Content-Length", str(len(gzipped))))
        start_response(captured["status"], headers)
        return [gzipped]


app.wsgi_app = _GzipMiddleware(app.wsgi_app)


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
    """首页：多股票批量搜索"""
    return render_template("pageF.html")


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
    """页面 B：30 个一级行业卡片（与首页一致），点击后弹窗逐级加载。"""
    primaries = [
        serialize_primary(name, primary)
        for name, primary in INDUSTRY_TREE.items()
    ]
    total_primary = len(primaries)
    total_secondary = sum(p["secondaryCount"] for p in primaries)
    total_tertiary = sum(p["tertiaryCount"] for p in primaries)
    total_stocks = sum(p["stockCount"] for p in primaries)
    return _cached_render(
        "pageB.html",
        (),
        lambda: render_template(
            "pageB.html",
            primaries=primaries,
            stats={
                "primary": total_primary,
                "secondary": total_secondary,
                "tertiary": total_tertiary,
                "stocks": total_stocks,
            },
        ),
    )


@app.route("/pageD")
def page_d():
    """页面 D：与页面 B 完全相同（展示一级+二级+三级+股票），股票名称可跳转页面 F 搜索"""
    groups, stats = _build_full_groups_cached()
    return _cached_render(
        "pageD.html",
        (),
        lambda: render_template("pageD.html", groups=groups, stats=stats),
    )


@app.route("/pageE")
def page_e():
    """页面 E：选择三级行业后，对其中的股票进行 新增 / 编辑 / 删除 管理。"""
    groups, _ = _build_full_groups_cached()
    return render_template(
        "pageE.html",
        tertiary_options=_build_tertiary_options(groups),
    )


@app.route("/api/pageE/stocks", methods=["GET"])
def api_page_e_stocks():
    """页面 E：返回指定三级行业下的全部股票（含全部字段）。"""
    path = (request.args.get("path") or "").strip()
    if "||" not in path or path.count("||") != 2:
        return jsonify({"ok": False, "error": "行业路径不合法"}), 400
    primary_name, secondary_name, tertiary_name = path.split("||")
    _, _, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404
    return jsonify(
        {
            "ok": True,
            "path": path,
            "stockCount": len(tertiary["stocks"]),
            "stocks": tertiary["stocks"],
        }
    )


@app.route("/api/pageE/add_stock", methods=["POST"])
def api_page_e_add_stock():
    """页面 E：为指定三级行业新增一只股票（写入 SQLite + 内存树，不进撤销记录）。"""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()
    options = {}
    for field in STOCK_OPTION_FIELDS:
        options[field] = (data.get(field) or "").strip()

    if not path or path.count("||") != 2:
        return jsonify({"ok": False, "error": "请选择三级行业"}), 400
    if not name:
        return jsonify({"ok": False, "error": "请填写股票名称"}), 400

    primary_name, secondary_name, tertiary_name = path.split("||")
    primary, secondary, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404

    for st in tertiary["stocks"]:
        if st["name"] == name:
            return jsonify(
                {"ok": False, "error": f"该三级行业下已存在股票「{name}」"}
            ), 400

    from db import add_stock, find_tertiary_id
    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return jsonify({"ok": False, "error": "SQLite 中未找到该三级行业"}), 404
    try:
        add_stock(
            tid,
            name=name,
            code=code,
            concept="",
            options=options,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"写入数据库失败: {e}"}), 500

    new_stock = {
        "name": name,
        "code": code,
    }
    new_stock.update(options)
    tertiary["stocks"].append(new_stock)
    _invalidate_caches()

    return jsonify(
        {"ok": True, "stock": new_stock, "stockCount": len(tertiary["stocks"])}
    )


@app.route("/api/pageE/delete_stock", methods=["POST"])
def api_page_e_delete_stock():
    """页面 E：从指定三级行业删除一只股票（SQLite + 内存树）。"""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()

    if not path or path.count("||") != 2:
        return jsonify({"ok": False, "error": "行业路径不合法"}), 400
    if not name:
        return jsonify({"ok": False, "error": "股票名称不能为空"}), 400

    primary_name, secondary_name, tertiary_name = path.split("||")
    _, _, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404

    if not any(st["name"] == name for st in tertiary["stocks"]):
        return jsonify({"ok": False, "error": "未找到该股票"}), 404

    from db import delete_stock, find_tertiary_id
    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return jsonify({"ok": False, "error": "SQLite 中未找到该三级行业"}), 404
    try:
        removed = delete_stock(name, tertiary_id=tid)
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库删除失败: {e}"}), 500
    if not removed:
        return jsonify({"ok": False, "error": "数据库中未找到该股票"}), 404

    tertiary["stocks"] = [
        st for st in tertiary["stocks"] if st["name"] != name
    ]
    _invalidate_caches()

    return jsonify({"ok": True, "removed": removed, "name": name})


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


@app.route("/pageL")
def page_l():
    """
    页面 L：以页面 B 的左列布局展示所有概念的层级树及其关联股票。
    左列为一级概念（含股票数统计，点击可跳页面 H），右列为嵌套的
    子概念层级 + 该概念下的股票（仅名称）。
    """
    from db import _split_concept_segments, list_concepts

    # 1) 按概念名聚合股票（仅名称）。
    #    概念串中可能含完整路径 "一级/二级/..."，需展开成各级概念。
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
                        for piece in _split_concept_segments(st.get(key)):
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
    判断字段值 raw（可能用 - , ， 、 空格 ; 等分隔，且每项可能是
    "一级/二级/..." 的完整路径）中是否包含概念 concept_name。
    """
    if not raw or not concept_name:
        return False
    # 复用 db 的切分逻辑：概念间按 - ， 、 空格 ; 切分，
    # 路径内部按 / 展开成各级，任一级命中即算匹配
    from db import _split_concept_segments
    return concept_name in _split_concept_segments(raw)


@app.route("/pageH")
def page_h():
    """
    页面 H：按股票概念（概念1~5）展示股票列表。
    参数：name（概念名称）。
    """
    name = (request.args.get("name") or "").strip()
    concept = None
    stocks = []
    tree = {}  # 一级 -> 二级 -> 三级 -> [股票]，用于层级表格展示
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
                            # 命中的那条概念的「完整路径」，用于在页面展示股票
                            # 具体挂在哪一级（如 固态电池/材料/负极/硅基负极/硅氧负极）
                            matched_path = ""
                            for k in ("concept1", "concept2", "concept3",
                                      "concept4", "concept5"):
                                for piece in _split_concept_raw(st.get(k)):
                                    segs = [
                                        s.strip()
                                        for s in piece.split(CONCEPT_PATH_SEP)
                                        if s.strip()
                                    ]
                                    if name in segs:
                                        matched_path = CONCEPT_PATH_SEP.join(segs)
                                        break
                                if matched_path:
                                    break
                            item = {
                                "name": st["name"],
                                "code": st["code"],
                                "primary": primary_name,
                                "secondary": secondary_name,
                                "tertiary": tertiary_name,
                                "concept1": st.get("concept1", ""),
                                "conceptPath": matched_path,
                            }
                            stocks.append(item)
                            tree.setdefault(primary_name, {}) \
                                .setdefault(secondary_name, {}) \
                                .setdefault(tertiary_name, []) \
                                .append(item)
        # 批量取业务描述：按每只股票命中路径的「末级概念」关联
        from db import get_businesses_by_concept_names
        leaf_names = sorted({
            (it.get("conceptPath") or "").split(CONCEPT_PATH_SEP)[-1].strip()
            for it in stocks if it.get("conceptPath")
        })
        biz_map = get_businesses_by_concept_names(leaf_names)
        for it in stocks:
            leaf = (it.get("conceptPath") or "").split(CONCEPT_PATH_SEP)[-1].strip()
            it["business"] = biz_map.get(leaf, {}).get(it["name"], "")
        # 概念统计：哪些概念在数据库中存在，方便用 existing
        # 这里仅取当前 name 作为唯一展示概念
        concept = {
            "name": name,
            "count": len(stocks),
        }

    # 按股票名称升序，便于查找
    stocks.sort(key=lambda s: s["name"])

    # 构建「概念层级表格」：以查询概念为一级，按每只股票的完整概念路径
    # 逐级展开（固态电池/材料/负极/硅基负极/硅氧负极 → 1~5 级列）
    root_node = {"name": name, "stocks": [], "children": {}}
    for it in stocks:
        segs = [
            s.strip()
            for s in (it.get("conceptPath") or "").split(CONCEPT_PATH_SEP)
            if s.strip()
        ]
        node = root_node
        # 从查询概念之后的每一级依次挂到树上
        chain = segs[segs.index(name) + 1:] if name in segs else []
        for seg in chain:
            node = node["children"].setdefault(
                seg, {"name": seg, "stocks": [], "children": {}}
            )
        node["stocks"].append({
            "name": it["name"],
            "code": it["code"],
            "primary": it.get("primary", ""),
            "secondary": it.get("secondary", ""),
            "tertiary": it.get("tertiary", ""),
            # 业务描述挂在「该股票路径的末级概念」上
            "business": it.get("business", ""),
        })

    # 深度优先展开为「一行一只股票」，每行带其概念层级链
    flat_rows: List[Dict] = []

    def _collect(node, chain):
        for st in sorted(node["stocks"], key=lambda s: s["name"]):
            flat_rows.append({"levels": chain, "stock": st})
        for key in sorted(node["children"]):
            _collect(node["children"][key], chain + [node["children"][key]["name"]])

    if name:
        _collect(root_node, [name])

    # 计算每列的 rowspan（相邻行同前缀则合并）
    # active[d] = 第 d 列还有多少行被已渲染单元格的 rowspan 覆盖。
    # 严格推进可避免股票挂在深浅不一的分支上时出现列错位。
    max_depth = max((len(r["levels"]) for r in flat_rows), default=1) or 1
    table_rows: List[Dict] = []
    total = len(flat_rows)
    active = [0] * max_depth
    for i, r in enumerate(flat_rows):
        cells = []
        for d in range(max_depth):
            if active[d] > 0:
                active[d] -= 1
                cells.append(None)  # 已被上方 rowspan 覆盖
                continue
            prefix = r["levels"][: d + 1]
            if d >= len(prefix):
                # 该股票的概念路径不足此层级：输出空单元格占位，保证列对齐
                cells.append({"name": "", "span": 1})
                continue
            span = 1
            for j in range(i + 1, total):
                if flat_rows[j]["levels"][: d + 1] == prefix:
                    span += 1
                else:
                    break
            cells.append({
                "name": prefix[d],
                "span": span,
            })
            active[d] = span - 1
        table_rows.append({"cells": cells, "stock": r["stock"]})

    if concept is not None:
        concept["levelCount"] = max_depth
        concept["leafCount"] = sum(
            1 for r in flat_rows if len(r["levels"]) == max_depth
        ) or len({tuple(r["levels"]) for r in flat_rows})

    return render_template(
        "pageH.html",
        concept=concept,
        stocks=stocks,
        table_rows=table_rows,
        max_depth=max_depth,
        query_name=name,
    )


@app.route("/api/stock_tip")
def api_stock_tip():
    """返回某只股票的「选项B」前 10 个字符，供全局悬浮提示使用。

    参数：name（股票名称，必填）
    返回：{ name, found, option_b, preview }
      preview = option_b 的前 10 个字符（超出则追加省略号）
    """
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "缺少 name"}), 400
    from db import get_stock_option_b
    option_b = get_stock_option_b(name)
    found = option_b is not None
    preview = (option_b or "")[:10]
    if option_b and len(option_b) > 10:
        preview += "…"
    return jsonify(
        {
            "ok": True,
            "name": name,
            "found": found,
            "option_b": option_b or "",
            "preview": preview,
        }
    )


@app.route("/pageC")
def page_c():
    """页面 C：与页面 B 完全相同（展示一级+二级+三级+股票）"""
    groups, stats = _build_full_groups_cached()
    added = session_store.get("added", [])
    # 渲染结果缓存：随数据代数 + 新增记录版本一起失效
    return _cached_render(
        "pageC.html",
        ("added", _ADDED_VERSION),
        lambda: render_template(
            "pageC.html",
            groups=groups,
            stats=stats,
            tertiary_options=_build_tertiary_options(groups),
            secondary_options=_build_secondary_options(groups),
            added=added,
        ),
    )


def _build_tertiary_options(groups):
    """构造三级下拉选项：含路径 + 名称 + 当前股票数"""
    options = []
    for g in groups:
        for s in g["secondaries"]:
            for t in s["tertiaries"]:
                options.append(
                    {
                        "value": f'{g["name"]}||{s["name"]}||{t["name"]}',
                        "label": f'{g["name"]} / {s["name"]} / {t["name"]}（{t["stockCount"]} 只）',
                    }
                )
    return options


def _build_secondary_options(groups):
    """构造二级下拉选项（一级||二级），用于「新增三级行业」选择父级。"""
    options = []
    for g in groups:
        for s in g["secondaries"]:
            options.append(
                {
                    "value": f'{g["name"]}||{s["name"]}',
                    "label": f'{g["name"]} / {s["name"]}（{s["tertiaryCount"]} 三级）',
                }
            )
    return options


# ============== API ==============

@app.route("/api/concepts/primary", methods=["GET"])
def api_primary_concepts():
    """返回所有一级概念（level=1）列表，用于编辑弹窗的「概念 1~5」下拉框。"""
    from db import list_primary_concepts
    items = list_primary_concepts()
    return jsonify({"ok": True, "items": items, "total": len(items)})


@app.route("/api/stocks/search_batch", methods=["POST"])
def api_stocks_search_batch():
    """
    批量搜索多只股票（页面 F）。
    请求 JSON: {"q": "关键词串"}，关键词按换行 / 逗号 / 顿号 / 空格分隔。
    对每只股票做全字段模糊匹配（名称/代码/概念/选项/三级行业），
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
    stock_ids = []
    for primary_name, primary in INDUSTRY_TREE.items():
        for secondary_name, secondary in primary["secondaries"].items():
            for tertiary_name, tertiary in secondary["tertiaries"].items():
                for st in tertiary["stocks"]:
                    matched = [kw for kw in keywords
                               if _stock_hit_keyword(st, tertiary_name, kw)]
                    if not matched:
                        continue
                    item = {
                        "name": st["name"],
                        "code": st["code"],
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
                    for field in STOCK_OPTION_FIELDS:
                        item[field] = st.get(field, "")
                    img_list = list(st.get("images") or [])
                    item["images"] = _images_payload(img_list)
                    item["image_path"] = img_list[0] if img_list else ""
                    item["image_url"] = (
                        f"/stock_images/{img_list[0]}" if img_list else ""
                    )
                    # 每只股票带 stock_id 用于批量取修改历史
                    if st.get("id") is not None:
                        stock_ids.append(st["id"])
                        item["id"] = st["id"]
                    items.append(item)
    # 名称命中优先
    items.sort(key=lambda r: 0 if r["nameHit"] else 1)
    # 一次性批量查询所有股票的修改日期（YYYY-MM-DD 字符串数组）
    mod_map = get_stock_modification_dates(stock_ids) if stock_ids else {}
    biz_map = get_businesses_for_stock_ids(stock_ids) if stock_ids else {}
    for item in items:
        sid = item.get("id")
        item["modification_dates"] = list(mod_map.get(sid, []))
        item["businesses"] = list(biz_map.get(sid, []))
    # 记录本次搜索历史（去重，hit_count 记本次命中的股票数）
    try:
        record_search_history_batch(keywords, hit_count=len(items))
    except Exception as e:
        app.logger.warning("record_search_history failed: %s", e)
    return jsonify(
        {"keywords": keywords, "items": items, "total": len(items)}
    )


@app.route("/api/search_history/recent", methods=["GET"])
def api_search_history_recent():
    """
    返回最近 30 天内的去重搜索历史。
    Query: ?limit=N（默认 20，最大 100）
    返回: {"items": [{"keyword", "searched_at", "hit_count"}], "retention_days": 1095}
    """
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    items = get_recent_search_history(limit=limit)
    return jsonify({
        "items": items,
        "total": len(items),
        "retention_days": SEARCH_HISTORY_RETENTION_DAYS,
    })


@app.route("/api/search_history/clear", methods=["POST"])
def api_search_history_clear():
    """清空全部搜索历史。"""
    deleted = clear_search_history()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/search_history/prune", methods=["POST"])
def api_search_history_prune():
    """手动触发清理 30 天前的旧记录。"""
    deleted = prune_search_history()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/search_history/list", methods=["GET"])
def api_search_history_list():
    """
    返回最近 N 天全部搜索记录（不去重），按时间倒序、按日期分组。
    Query:
      - days=N（默认 30，<=0 表示不限；上限 90）
      - limit=N（默认 1000，上限 5000）
    返回:
      {
        "items":       全部记录（每条 {id, keyword, searched_at, hit_count}）,
        "groups":      [{date: "YYYY-MM-DD", count: int, hits: int}],  按日期倒序,
        "total":       记录总数,
        "retention_days": 1095,
      }
    """
    try:
        days = int(request.args.get("days", SEARCH_HISTORY_RETENTION_DAYS))
    except (TypeError, ValueError):
        days = SEARCH_HISTORY_RETENTION_DAYS
    days = max(0, min(days, 3 * 365))
    try:
        limit = int(request.args.get("limit", 1000))
    except (TypeError, ValueError):
        limit = 1000
    limit = max(1, min(limit, 5000))

    items = list_search_history(days=days, limit=limit)
    # 按日期分组汇总（保持日期倒序）
    groups_map = {}
    for it in items:
        d = (it["searched_at"] or "")[:10]  # YYYY-MM-DD
        g = groups_map.setdefault(d, {"date": d, "count": 0, "hits": 0})
        g["count"] += 1
        g["hits"] += int(it.get("hit_count") or 0)
    groups = sorted(groups_map.values(), key=lambda g: g["date"], reverse=True)
    return jsonify({
        "items": items,
        "groups": groups,
        "total": len(items),
        "retention_days": SEARCH_HISTORY_RETENTION_DAYS,
    })


@app.route("/pageM")
def page_m():
    """页面 M：展示最近 30 天的搜索记录明细（按日期分组）。"""
    return render_template("pageM.html")


@app.route("/pageP")
def page_p():
    """页面 P：空白页（预留，等待后续需求填充）。"""
    return render_template("pageP.html")


def _stock_hit_keyword(st, tertiary_name, kw):
    """判断股票 st 在全字段上是否包含关键词 kw（均为小写比较）"""
    fields = [
        st["name"], st["code"],
        st["concept1"], st["concept2"], tertiary_name,
    ]
    for field in STOCK_OPTION_FIELDS:
        fields.append(st.get(field, ""))
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
    row 参数保留旧格式 [一级编码, ..., 三级行业, 股票名称, 股票代码, 概念, ...]。
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


@app.route("/api/pageC/add_tertiary", methods=["POST"])
def api_page_c_add_tertiary():
    """在指定二级行业下新增一个三级行业（如 有色 / 能源金属 下新增「镍」）。

    入参：{path: "一级||二级", name: "三级名称", code?: ""}
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()

    if not path or "||" not in path:
        return jsonify({"ok": False, "error": "请选择二级行业"}), 400
    if not name:
        return jsonify({"ok": False, "error": "请填写三级行业名称"}), 400

    parts = path.split("||")
    if len(parts) != 2:
        return jsonify({"ok": False, "error": "二级行业路径不合法"}), 400
    primary_name, secondary_name = parts

    from db import add_tertiary_industry

    try:
        new_id = add_tertiary_industry(primary_name, secondary_name, name, code=code)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        print(f"[错误] 新增三级行业失败: {e}")
        return jsonify({"ok": False, "error": f"写入数据库失败: {e}"}), 500

    # 同步内存树，避免重启才能看到
    primary = INDUSTRY_TREE.get(primary_name)
    if primary is not None:
        secondary = primary["secondaries"].get(secondary_name)
        if secondary is not None:
            secondary["tertiaries"][name] = {"name": name, "code": code, "stocks": []}
    _invalidate_caches()

    return jsonify(
        {
            "ok": True,
            "id": new_id,
            "tertiary": {
                "name": name,
                "code": code,
                "stockCount": 0,
            },
            "path": f"{primary_name} / {secondary_name} / {name}",
        }
    )


@app.route("/api/pageC/add_stock", methods=["POST"])
def api_page_c_add_stock():
    """为指定三级行业新增一只股票：追加一行到 CSV 文件 + 更新内存树"""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()

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
    _invalidate_caches(bump_added=True)

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


@app.route("/api/stock/upload_image", methods=["POST"])
def api_upload_stock_image():
    """
    给一只股票追加一张图片（一只股票可保存多张）。
    请求 form:
        path  = "一级||二级||三级"
        name  = 股票名
        file  = 图片文件
    """
    from db import find_tertiary_id, add_stock_image, list_stock_images

    path = (request.form.get("path") or "").strip()
    name = (request.form.get("name") or "").strip()
    if "||" not in path or path.count("||") != 2 or not name:
        return jsonify({"ok": False, "error": "参数不合法"}), 400

    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"ok": False, "error": "未收到文件"}), 400

    # 校验扩展名
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return jsonify({"ok": False, "error": f"不支持的图片类型: {ext}"}), 400

    parts = path.split("||")
    primary_name, secondary_name, tertiary_name = parts
    primary, secondary, tertiary, err = _find_tertiary(
        primary_name, secondary_name, tertiary_name
    )
    if err:
        return jsonify({"ok": False, "error": err}), 404
    target = next((s for s in tertiary["stocks"] if s["name"] == name), None)
    if target is None:
        return jsonify({"ok": False, "error": "未找到该股票"}), 404

    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return jsonify({"ok": False, "error": "SQLite 中未找到该三级行业"}), 404

    # 限制单只股票图片数量
    if len(list_stock_images(tid, name)) >= MAX_IMAGES_PER_STOCK:
        return jsonify({
            "ok": False,
            "error": f"最多上传 {MAX_IMAGES_PER_STOCK} 张图片",
        }), 400

    # 文件名：<stock_id>_<随机>.<ext>，多张互不覆盖
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM stocks WHERE tertiary_id=? AND name=?", (tid, name))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return jsonify({"ok": False, "error": "未找到该股票"}), 404
    stock_id = row["id"]

    new_filename = f"{stock_id}_{uuid.uuid4().hex[:10]}{ext}"
    new_abs = os.path.join(STOCK_IMAGES_DIR, new_filename)

    # 保存新文件
    try:
        f.save(new_abs)
    except Exception as e:
        return jsonify({"ok": False, "error": f"保存失败: {e}"}), 500

    # 写 DB（追加一条图片记录）
    try:
        images = add_stock_image(tid, name, new_filename)
    except LookupError as e:
        _safe_remove(new_abs)
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        _safe_remove(new_abs)
        return jsonify({"ok": False, "error": f"数据库写入失败: {e}"}), 500

    # 同步内存树
    target["images"] = images
    target["image_path"] = images[0] if images else ""
    _invalidate_caches()

    first = target["image_path"]
    return jsonify({
        "ok": True,
        "images": _images_payload(images),
        "image_path": first,
        "image_url": f"/stock_images/{first}" if first else "",
    })


def _safe_remove(abs_path):
    """尽力删除磁盘文件，失败忽略。"""
    try:
        if abs_path and os.path.exists(abs_path):
            os.remove(abs_path)
    except OSError:
        pass


@app.route("/api/stock/delete_image", methods=["POST"])
def api_delete_stock_image():
    """
    删除一只股票的图片。
    请求 JSON: {path, name, filename?}
        filename 指定则只删该张；不传则删除该股票全部图片。
    """
    from db import find_tertiary_id, delete_stock_image

    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    filename = (data.get("filename") or "").strip()
    if "||" not in path or path.count("||") != 2 or not name:
        return jsonify({"ok": False, "error": "参数不合法"}), 400

    parts = path.split("||")
    primary_name, secondary_name, tertiary_name = parts
    primary, secondary, tertiary, err = _find_tertiary(*parts)
    if err:
        return jsonify({"ok": False, "error": err}), 404
    target = next((s for s in tertiary["stocks"] if s["name"] == name), None)
    if target is None:
        return jsonify({"ok": False, "error": "未找到该股票"}), 404

    tid = find_tertiary_id(primary_name, secondary_name, tertiary_name)
    if tid is None:
        return jsonify({"ok": False, "error": "SQLite 中未找到该三级行业"}), 404

    # 待删除的物理文件（basename 防止路径穿越）
    if filename:
        to_remove = [os.path.basename(filename)]
    else:
        to_remove = [os.path.basename(fn) for fn in (target.get("images") or [])]

    try:
        images = delete_stock_image(tid, name, filename)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"数据库写入失败: {e}"}), 500

    for fn in to_remove:
        _safe_remove(os.path.join(STOCK_IMAGES_DIR, fn))

    target["images"] = images
    target["image_path"] = images[0] if images else ""
    _invalidate_caches()
    return jsonify({"ok": True, "images": _images_payload(images)})


@app.route("/stock_images/<path:filename>")
def serve_stock_image(filename):
    """提供股票图片的静态访问。"""
    safe_name = os.path.basename(filename)
    if safe_name != filename or "/" in filename or "\\" in filename:
        return jsonify({"ok": False, "error": "非法文件名"}), 400
    return send_from_directory(STOCK_IMAGES_DIR, safe_name)


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
        "name", "code",
        "concept1", "concept2", "concept3", "concept4", "concept5",
    }
    allowed.update(STOCK_OPTION_FIELDS)
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
                "concept1": target.get("concept1", ""),
                "concept2": target.get("concept2", ""),
                "concept3": target.get("concept3", ""),
                "concept4": target.get("concept4", ""),
                "concept5": target.get("concept5", ""),
                **{f: target.get(f, "") for f in STOCK_OPTION_FIELDS},
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

    stock_list = [{"name": n, "code": "", "concept": ""} for n in names]
    try:
        inserted = add_stocks_batch(tid, stock_list)
    except Exception as e:
        print(f"[错误] 批量写入 SQLite 失败: {e}")
        return jsonify({"ok": False, "error": f"写入数据库失败: {e}"}), 500

    # 更新内存树 + 记录
    records = []
    for n, s_data in zip(names, stock_list):
        new_stock = {"name": n, "code": ""}
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
    _invalidate_caches(bump_added=True)

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
    _invalidate_caches(bump_added=True)
    return jsonify({"ok": True, "removed": removed})


# ==================== 页面 K：概念表编辑 ====================

@app.route("/pageK")
def page_k():
    """页面 K：概念字典表编辑（增/改/删/导入，可选同步股票数据）"""
    from db import list_concepts, get_stock_counts
    concepts = list_concepts()
    stock_total, stock_with_concept = get_stock_counts()
    return render_template(
        "pageK.html",
        concepts=concepts,
        stock_total=stock_total,
        stock_with_concept=stock_with_concept,
    )


@app.route("/pageK/edit/<int:cid>")
def page_k_edit(cid):
    """页面 K 的概念编辑专用页。

    原先点击「编辑」是在 pageK 里弹窗操作，现改为跳转到本独立页面，
    页面自带「基本信息 / 隶属股票 / 下级概念」三块编辑区。
    """
    from db import list_concepts

    concepts = list_concepts()
    concept = None
    for c in concepts:
        if int(c["id"]) == int(cid):
            concept = c
            break
    if concept is None:
        abort(404)
    return render_template("pageKEdit.html", concept=concept, concepts=concepts)


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
    编辑概念 {"id": 1, "name": "新名称", "description": "...", "code": "GN001",
              "parent_id": 2, "sync_stocks": true}
    parent_id 可省略表示保持原上级；传 null 表示移到根（一级）。
    code 可省略表示不改编码；传空串表示清空编码。
    sync_stocks=true 时，把所有股票概念串中的旧名称替换为新名称。
    """
    from db import update_concept, sync_concept_in_stocks
    data = request.get_json(silent=True) or {}
    concept_id = data.get("id")
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    sync = bool(data.get("sync_stocks"))

    # code：缺省 → 保持；空串 → 清空；否则写入新编码
    code = data.get("code", "__keep__")
    if code != "__keep__":
        code = (code or "").strip()

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
            code=code,
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


@app.route("/api/pageK/update_concepts_batch", methods=["POST"])
def api_page_k_update_concepts_batch():
    """
    批量编辑概念（用于页面 K 一级概念弹窗一次性保存其所有下级分类）。
    {"items": [{"id": 1, "name": "新名", "description": "描述", "parent_id": 2}],
     "sync_stocks": true}
    parent_id 省略表示保持原上级；null/"" 表示移到根。
    返回 {"ok": true, "updated": n, "synced_stocks": n, "failed": [{"id", "error"}]}
    """
    from db import update_concept, sync_concept_in_stocks
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    sync = bool(data.get("sync_stocks"))

    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "items 必须是数组"}), 400

    updated = 0
    synced = 0
    failed = []

    for item in items:
        if not isinstance(item, dict):
            failed.append({"id": None, "error": "条目格式无效"})
            continue

        concept_id = item.get("id")
        name = (item.get("name") or "").strip()
        description = item.get("description")
        if description is not None:
            description = description.strip()

        if concept_id is None:
            failed.append({"id": None, "error": "缺少概念 id"})
            continue
        if not name:
            failed.append({"id": concept_id, "error": "概念名称不能为空"})
            continue

        parent_id = item.get("parent_id", "__keep__")
        if parent_id is None or parent_id == "":
            parent_id = None
        elif parent_id != "__keep__":
            try:
                parent_id = int(parent_id)
            except (TypeError, ValueError):
                failed.append({"id": concept_id, "error": "上级分类 id 无效"})
                continue

        try:
            old_name, new_name = update_concept(
                concept_id,
                name=name,
                description=description,
                parent_id=parent_id,
            )
        except (LookupError, ValueError) as e:
            failed.append({"id": concept_id, "error": str(e)})
            continue
        except Exception as e:
            failed.append({"id": concept_id, "error": f"数据库写入失败：{e}"})
            continue

        updated += 1
        if sync and new_name != old_name:
            try:
                synced += sync_concept_in_stocks(old_name, new_name)
            except Exception as e:
                failed.append(
                    {"id": concept_id, "error": f"已更新，但同步股票失败：{e}"}
                )

    if synced:
        reload_industry_tree()

    return jsonify({
        "ok": True,
        "updated": updated,
        "synced_stocks": synced,
        "failed": failed,
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


@app.route("/api/pageK/edit_table")
def api_page_k_edit_table():
    """
    页面 K 编辑页的概念层级大表（页面 H 风格）数据。
    ?concept_id=1 → 返回该概念（含其所有子孙）的层级列 + 股票 + 业务描述扁平行
    """
    from db import build_concept_edit_table

    raw_id = request.args.get("concept_id") or request.args.get("id") or ""
    try:
        cid = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "concept_id 无效"}), 400

    try:
        data = build_concept_edit_table(cid)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"查询失败：{e}"}), 500

    data["ok"] = True
    return jsonify(data)


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


@app.route("/api/pageK/stock_business", methods=["POST"])
def api_page_k_stock_business():
    """
    设置/清除「股票 × 概念」的业务描述。
    请求: {"concept": "硅氧负极", "stock": "璞泰来", "business": "人造石墨负极龙头"}
    - business 为空串时清除该条描述
    返回: {"ok": true, "business": "<最终文本>"}
    """
    from db import set_stock_concept_business
    data = request.get_json(silent=True) or {}
    concept = (data.get("concept") or "").strip()
    stock = (data.get("stock") or "").strip()
    business = (data.get("business") or "").strip()
    if not concept or not stock:
        return jsonify({"ok": False, "error": "缺少概念或股票名称"}), 400
    try:
        saved = set_stock_concept_business(stock, concept, business)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"保存失败：{e}"}), 500
    return jsonify({"ok": True, "business": saved})


@app.route("/api/pageK/concept_stocks_map", methods=["POST"])
def api_page_k_concept_stocks_map():
    """
    批量查询多个概念各自包含的股票名（供页面 K 编辑弹窗展示「概念→股票」逻辑图）。
    请求: {"names": ["固态电池", "材料", "正极"]}
    返回: {"ok": true, "map": {"固态电池": [...], "材料": [...]}}
    """
    from db import get_concept_stocks_map
    data = request.get_json(silent=True) or {}
    names = data.get("names")
    if not isinstance(names, list):
        return jsonify({"ok": False, "error": "names 必须是数组"}), 400
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names:
        return jsonify({"ok": True, "map": {}})
    if len(names) > 500:
        return jsonify({"ok": False, "error": "一次最多查询 500 个概念"}), 400
    try:
        mapping = get_concept_stocks_map(names)
    except Exception as e:
        return jsonify({"ok": False, "error": f"查询失败：{e}"}), 500
    return jsonify({"ok": True, "map": mapping})


@app.route("/api/pageK/concepts")
def api_page_k_concepts():
    """返回最新概念树（供页面 K 编辑弹窗新增/删除子级后局部刷新）"""
    from db import list_concepts
    try:
        concepts = list_concepts()
    except Exception as e:
        return jsonify({"ok": False, "error": f"查询失败：{e}"}), 500
    return jsonify({"ok": True, "concepts": concepts})


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
    请求: {"name": "正极", "stocks": ["股票A", "股票B"]}
    - 概念不存在于概念表时自动创建
    - 已拥有该概念的股票自动跳过
    - 写入格式：该概念的完整路径（一级/二级/.../name），
      如 "正极" → "固态电池/材料/正极"。
      一级概念会写成自身（"固态电池"）。
    返回 {"ok": true, "target": "固态电池/材料/正极", "added": [...], "skipped": [...], "not_found": [...]}
    """
    from db import (
        _split_concept_raw,
        add_concept,
        update_stock_concept,
        build_concept_path,
    )
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

    # 写入完整路径：一级概念=自身，子概念="一级/.../name"
    target_concept = build_concept_path(concept_name) or concept_name

    added, skipped, not_found = [], [], []
    for sn in stock_names:
        try:
            new_val = update_stock_concept(stock_name=sn, new_concept=target_concept)
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
        "target": target_concept,
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
