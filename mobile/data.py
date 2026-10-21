"""手机端只读数据访问层。

复用根目录的 ``db.py`` 连接同一份 SQLite（``data/tongdaxin.db``），
但不依赖 ``app.py`` 中的内存变量，手机端与主程序互不干扰。

所有函数均为**只读**，保证手机端浏览不会污染数据。
新增的下钻查询统一走 SQL，避免不同来源的数据不一致。
"""
from db import STOCK_OPTION_FIELDS, get_conn


# ---------------------------------------------------------------- 基础工具
def _rows(sql, params=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _one(sql, params=()):
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _first_image_map(conn, stock_ids):
    """批量取 {stock_id: 首图文件名}。"""
    if not stock_ids:
        return {}
    marks = ",".join("?" * len(stock_ids))
    cur = conn.execute(
        f"SELECT stock_id, filename FROM stock_images "
        f"WHERE stock_id IN ({marks}) ORDER BY stock_id, sort_order, id",
        list(stock_ids),
    )
    out = {}
    for r in cur.fetchall():
        out.setdefault(r["stock_id"], r["filename"])
    return out


# ---------------------------------------------------------------- 概览统计
def overview_stats():
    """首页概览：各级行业数量、股票数、图片数、含概念股票数。"""
    level_counts = {
        r["level"]: r["c"]
        for r in _rows("SELECT level, COUNT(*) AS c FROM industries GROUP BY level")
    }
    stock_row = _one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN TRIM(COALESCE(concept,'')) <> '' THEN 1 ELSE 0 END) AS with_concept "
        "FROM stocks"
    ) or {"total": 0, "with_concept": 0}

    try:
        img_count = _one("SELECT COUNT(*) AS c FROM stock_images")["c"]
    except Exception:
        img_count = 0

    return {
        "primary": level_counts.get(1, 0),
        "secondary": level_counts.get(2, 0),
        "tertiary": level_counts.get(3, 0),
        "stocks": stock_row["total"] or 0,
        "with_concept": stock_row["with_concept"] or 0,
        "images": img_count,
    }


# ---------------------------------------------------------------- 行业树
def list_primaries():
    """一级行业列表（带二级/三级/股票数量）。"""
    sql = """
        SELECT p.id, p.code, p.name,
               (SELECT COUNT(*) FROM industries s
                 WHERE s.parent_id = p.id)                        AS secondary_count,
               (SELECT COUNT(*) FROM industries t
                  JOIN industries s2 ON t.parent_id = s2.id
                 WHERE s2.parent_id = p.id)                       AS tertiary_count,
               (SELECT COUNT(*) FROM stocks st
                  JOIN industries t2 ON st.tertiary_id = t2.id
                  JOIN industries s3 ON t2.parent_id = s3.id
                 WHERE s3.parent_id = p.id)                       AS stock_count
        FROM industries p
        WHERE p.level = 1
        ORDER BY p.id
    """
    return _rows(sql)


def get_industry(industry_id):
    """按 id 取单个行业节点。"""
    return _one(
        "SELECT id, level, code, name, parent_id FROM industries WHERE id = ?",
        (industry_id,),
    )


def list_children(parent_id):
    """某行业节点的直接子行业（带三级/股票数量）。"""
    sql = """
        SELECT c.id, c.code, c.name, c.level, c.parent_id,
               (SELECT COUNT(*) FROM industries t
                 WHERE t.parent_id = c.id)                        AS tertiary_count,
               (SELECT COUNT(*) FROM stocks st
                  JOIN industries t2 ON st.tertiary_id = t2.id
                 WHERE t2.parent_id = c.id)                       AS stock_count,
               (SELECT COUNT(*) FROM industries s2
                 WHERE s2.parent_id = c.id)                       AS secondary_count
        FROM industries c
        WHERE c.parent_id = ?
        ORDER BY c.id
    """
    return _rows(sql, (parent_id,))


def industry_path(industry_id):
    """返回 [一级, 二级, 三级] 的面包屑节点列表。"""
    path = []
    cur_id = industry_id
    for _ in range(3):
        node = get_industry(cur_id)
        if node is None:
            break
        path.insert(0, node)
        if node["parent_id"] is None:
            break
        cur_id = node["parent_id"]
    return path


# ---------------------------------------------------------------- 股票
_STOCK_COLS = (
    "st.id, st.name, st.code, st.concept, st.tertiary_id, "
    + ", ".join(f"st.{f}" for f in STOCK_OPTION_FIELDS)
)


def list_tertiary_stocks(tertiary_id):
    """某三级行业下的全部股票（含首图），按名称排序。"""
    conn = get_conn()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT {_STOCK_COLS} FROM stocks st "
                "WHERE st.tertiary_id = ? ORDER BY st.name",
                (tertiary_id,),
            ).fetchall()
        ]
        img_map = _first_image_map(conn, [r["id"] for r in rows])
    finally:
        conn.close()

    for r in rows:
        r["image"] = img_map.get(r["id"], "")
    return rows


def get_stock(stock_id):
    """股票详情（含全部图片、所属行业路径）。"""
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT {_STOCK_COLS}, t.name AS tertiary_name, "
            "s.name AS secondary_name, p.name AS primary_name "
            "FROM stocks st "
            "JOIN industries t ON st.tertiary_id = t.id "
            "JOIN industries s ON t.parent_id = s.id "
            "JOIN industries p ON s.parent_id = p.id "
            "WHERE st.id = ?",
            (stock_id,),
        ).fetchone()
        if row is None:
            return None

        stock = dict(row)
        stock["images"] = [
            dict(r)
            for r in conn.execute(
                "SELECT filename FROM stock_images WHERE stock_id = ? "
                "ORDER BY sort_order, id",
                (stock_id,),
            ).fetchall()
        ]
        stock["images"] = [i["filename"] for i in stock["images"]]
    finally:
        conn.close()
    return stock


def search_stocks(keyword, limit=200):
    """按 名称 / 代码 / 概念 模糊搜索股票。"""
    kw = (keyword or "").strip()
    if not kw:
        return []

    like = f"%{kw}%"
    sql = f"""
        SELECT {_STOCK_COLS},
               t.name AS tertiary_name,
               s.name AS secondary_name,
               p.name AS primary_name
        FROM stocks st
        JOIN industries t ON st.tertiary_id = t.id
        JOIN industries s ON t.parent_id = s.id
        JOIN industries p ON s.parent_id = p.id
        WHERE st.name LIKE ? OR st.code LIKE ? OR st.concept LIKE ?
        ORDER BY st.name
        LIMIT ?
    """
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute(sql, (like, like, like, limit)).fetchall()]
        img_map = _first_image_map(conn, [r["id"] for r in rows])
    finally:
        conn.close()

    for r in rows:
        r["image"] = img_map.get(r["id"], "")
    return rows
