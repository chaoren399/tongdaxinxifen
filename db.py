"""
SQLite 数据访问层。

提供：
- DB_FILE：数据库路径
- get_conn()：获取 sqlite3 连接（统一 with check_same_thread=False）
- init_db_from_csv(csv_path, db_path=None)：从 CSV 一次性建库
- load_industry_tree(db_path=None)：从 SQLite 读出 INDUSTRY_TREE 数据结构
- update_stock_concept(tertiary_id, stock_name, new_concept)：写入股票概念
- find_stock_by_name(stock_name)：通过名称查 (tertiary_id, full_path)
"""
import csv
import os
import sqlite3
from collections import OrderedDict

DB_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data", "tongdaxin.db",
)
CSV_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data", "通达信一级二级三级行业汇总20261007 - 副本.csv",
)

# 股票表新增的 8 个可编辑「选项」列（选项A ~ 选项H）
STOCK_OPTION_FIELDS = [
    "option_a", "option_b", "option_c", "option_d",
    "option_e", "option_f", "option_g", "option_h",
]

# 最新 stocks 表结构（已废弃的 location / main_business / image_path 不再包含；
# 图片统一存于 stock_images 表）
STOCKS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS stocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tertiary_id   INTEGER NOT NULL,
    name          TEXT NOT NULL,
    code          TEXT,
    concept       TEXT DEFAULT '',
    option_a      TEXT DEFAULT '',
    option_b      TEXT DEFAULT '',
    option_c      TEXT DEFAULT '',
    option_d      TEXT DEFAULT '',
    option_e      TEXT DEFAULT '',
    option_f      TEXT DEFAULT '',
    option_g      TEXT DEFAULT '',
    option_h      TEXT DEFAULT '',
    FOREIGN KEY(tertiary_id) REFERENCES industries(id)
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS industries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    level        INTEGER NOT NULL,   -- 1 / 2 / 3
    code         TEXT,
    acode        TEXT,               -- 一级专用列 "一级编码"
    name         TEXT NOT NULL,
    parent_id    INTEGER,
    FOREIGN KEY(parent_id) REFERENCES industries(id)
);
""" + STOCKS_TABLE_DDL + """
-- 一只股票可挂多张图片；filename 为 data/stock_images 下的文件名
CREATE TABLE IF NOT EXISTS stock_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id   INTEGER NOT NULL,
    filename   TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT '',
    FOREIGN KEY(stock_id) REFERENCES stocks(id)
);
CREATE INDEX IF NOT EXISTS idx_stock_images_stock ON stock_images(stock_id);

CREATE TABLE IF NOT EXISTS concepts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    level       INTEGER NOT NULL DEFAULT 1,   -- 1~5 级概念分类
    parent_id   INTEGER,
    FOREIGN KEY(parent_id) REFERENCES concepts(id)
);

CREATE INDEX IF NOT EXISTS idx_industries_parent ON industries(parent_id);
CREATE INDEX IF NOT EXISTS idx_industries_lvl    ON industries(level);
CREATE INDEX IF NOT EXISTS idx_stocks_tertiary   ON stocks(tertiary_id);
CREATE INDEX IF NOT EXISTS idx_stocks_name       ON stocks(name);
CREATE INDEX IF NOT EXISTS idx_stocks_concept    ON stocks(concept);
CREATE INDEX IF NOT EXISTS idx_concepts_name     ON concepts(name);
"""


def ensure_concepts_table(db_path=None):
    """
    确保 concepts 表存在（对旧数据库的增量迁移，不动其他表数据）。
    旧库若无 level / parent_id 列，则用 ALTER TABLE 增量补齐。
    """
    conn = get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS concepts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            level       INTEGER NOT NULL DEFAULT 1,
            parent_id   INTEGER,
            FOREIGN KEY(parent_id) REFERENCES concepts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_concepts_name ON concepts(name);
    """)
    # 增量迁移：旧版本 concepts 表可能没有 level / parent_id 列
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(concepts)")
    cols = {row["name"] for row in cur.fetchall()}
    if "level" not in cols:
        conn.execute("ALTER TABLE concepts ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE concepts ADD COLUMN parent_id INTEGER")
    conn.commit()
    conn.close()


def ensure_stocks_columns(db_path=None):
    """
    确保 stocks 表存在所有可编辑列（选项A~H），对旧库做增量迁移。
    返回本次新增的列名列表。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(stocks)")
    cols = {row["name"] for row in cur.fetchall()}
    extra = list(STOCK_OPTION_FIELDS)
    added = []
    for field in extra:
        if field not in cols:
            conn.execute(f"ALTER TABLE stocks ADD COLUMN {field} TEXT DEFAULT ''")
            added.append(field)
    conn.commit()
    conn.close()
    return added


def ensure_stock_images_table(db_path=None):
    """
    确保 stock_images 表存在；若旧库仍有单图字段 stocks.image_path，
    则把它迁移为 stock_images 中的一条记录（幂等）。返回本次迁移的图片条数。
    """
    conn = get_conn(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock_images (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id   INTEGER NOT NULL,
                filename   TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT '',
                FOREIGN KEY(stock_id) REFERENCES stocks(id)
            );
            CREATE INDEX IF NOT EXISTS idx_stock_images_stock
                ON stock_images(stock_id);
            """
        )
        cur = conn.cursor()
        # 只有在旧库仍存在 image_path 列时才迁移（新库该列已删除）
        cur.execute("PRAGMA table_info(stocks)")
        stock_cols = {r["name"] for r in cur.fetchall()}
        migrated = 0
        if "image_path" in stock_cols:
            cur.execute("SELECT DISTINCT stock_id FROM stock_images")
            have = {r["stock_id"] for r in cur.fetchall()}
            cur.execute(
                "SELECT id, image_path FROM stocks "
                "WHERE COALESCE(image_path, '') <> ''"
            )
            for r in cur.fetchall():
                if r["id"] in have:
                    continue
                conn.execute(
                    "INSERT INTO stock_images "
                    "(stock_id, filename, sort_order, created_at) "
                    "VALUES (?, ?, 0, '')",
                    (r["id"], r["image_path"]),
                )
                migrated += 1
        conn.commit()
        return migrated
    finally:
        conn.close()


def _split_concept_raw(raw):
    """按 - , ， 、 空格 ; 等分隔符切分概念串。"""
    if not raw:
        return []
    norm = (
        raw.replace("-", ",")
           .replace("，", ",")
           .replace("、", ",")
           .replace(" ", ",")
           .replace(";", ",")
    )
    return [p.strip() for p in norm.split(",") if p.strip()]


def get_conn(db_path=None):
    """获取一个 sqlite3 连接（check_same_thread=False 以便 Flask 多线程访问）。"""
    path = db_path or DB_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db_from_csv(csv_path=None, db_path=None):
    """
    从 CSV 一次性导入到 SQLite。
    若数据库已有数据会先清空再导入。
    """
    csv_path = csv_path or CSV_FILE
    db_path = db_path or DB_FILE

    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    cur.execute("DELETE FROM stocks;")
    cur.execute("DELETE FROM industries;")

    # 索引: (level, name) -> industry_id
    ind_index = {}

    def upsert_industry(level, code, acode, name, parent_id):
        key = (level, name)
        if key in ind_index:
            return ind_index[key]
        cur.execute(
            "INSERT INTO industries (level, code, acode, name, parent_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (level, code, acode, name, parent_id),
        )
        ind_index[key] = cur.lastrowid
        return ind_index[key]

    stock_rows = 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            p_name = (row.get("一级行业(30)") or "").strip()
            if not p_name:
                continue
            pri_id = upsert_industry(
                1,
                (row.get("一级代码") or "").strip() or None,
                (row.get("一级编码") or "").strip() or None,
                p_name,
                None,
            )
            s_name = (row.get("二级行业(128)") or "").strip()
            sec_id = None
            if s_name:
                sec_id = upsert_industry(
                    2,
                    (row.get("二级代码") or "").strip() or None,
                    None,
                    s_name,
                    pri_id,
                )
            t_name = (row.get("三级行业(345)") or "").strip()
            ter_id = None
            if t_name:
                ter_id = upsert_industry(
                    3,
                    (row.get("三级代码") or "").strip() or None,
                    None,
                    t_name,
                    sec_id,
                )
            stock_name = (row.get("股票名称") or "").strip()
            if stock_name and ter_id is not None:
                cur.execute(
                    "INSERT INTO stocks (tertiary_id, name, code, concept) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        ter_id,
                        stock_name,
                        (row.get("股票代码") or "").strip() or None,
                        (row.get("概念") or "").strip(),
                    ),
                )
                stock_rows += 1

    conn.commit()
    conn.close()
    return {
        "industries": len(ind_index),
        "stocks": stock_rows,
        "db_path": db_path,
    }


def drop_obsolete_stock_columns(db_path=None):
    """
    物理删除 stocks 表中已废弃的 location / main_business / image_path 三列。
    SQLite < 3.35 不支持 ALTER TABLE DROP COLUMN，因此统一采用「重建表」方式：
    新建最新结构 -> 复制数据 -> 删旧表 -> 重命名。
    幂等：这些列都已不存在时返回 False。
    """
    conn = get_conn(db_path)
    try:
        conn.isolation_level = None  # 自动提交，确保 PRAGMA / DDL 立即生效
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(stocks)")
        cols = [r["name"] for r in cur.fetchall()]
        drop_cols = [
            c for c in ("location", "main_business", "image_path") if c in cols
        ]
        if not drop_cols:
            return False

        keep_cols = [c for c in cols if c not in drop_cols]
        col_list = ", ".join(keep_cols)

        # 重建期间关闭外键约束（stock_images 引用 stocks.id）
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE IF EXISTS stocks_new")
        conn.executescript(STOCKS_TABLE_DDL.replace("stocks", "stocks_new"))
        conn.execute(
            f"INSERT INTO stocks_new ({col_list}) SELECT {col_list} FROM stocks"
        )
        conn.execute("DROP TABLE stocks")
        conn.execute("ALTER TABLE stocks_new RENAME TO stocks")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_stocks_tertiary ON stocks(tertiary_id);
            CREATE INDEX IF NOT EXISTS idx_stocks_name     ON stocks(name);
            CREATE INDEX IF NOT EXISTS idx_stocks_concept  ON stocks(concept);
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")
        return True
    finally:
        conn.close()


def load_industry_tree(db_path=None):
    """
    从 SQLite 加载 INDUSTRY_TREE（保持原 csv 加载版本的返回结构）：

    {
        primary_name: {
            "name": primary_name,
            "code": "A1",
            "secondaries": {
                secondary_name: {
                    "name": secondary_name,
                    "tertiaries": {
                        tertiary_name: {
                            "name": tertiary_name,
                            "stocks": [
                                {"name": "...", "code": "...", "concept1": "...", ...},
                            ],
                        }
                    },
                }
            },
        }
    }
    """
    conn = get_conn(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, level, code, name, parent_id FROM industries")
    rows = cur.fetchall()

    primary_by_id = {}     # primary_id -> primary_node
    secondary_by_id = {}   # secondary_id -> secondary_node
    tertiary_by_id = {}    # tertiary_id -> tertiary_node

    # 三次遍历：先 level=1，再 2，再 3（确保父级先构建）
    for level in (1, 2, 3):
        for r in rows:
            if r["level"] != level:
                continue
            if level == 1:
                primary_by_id[r["id"]] = {
                    "name": r["name"],
                    "code": r["code"] or "",
                    "secondaries": {},
                }
            elif level == 2:
                parent_primary = primary_by_id.get(r["parent_id"])
                if parent_primary is None:
                    continue
                sec_node = {
                    "name": r["name"],
                    "code": r["code"] or "",
                    "tertiaries": {},
                }
                parent_primary["secondaries"][r["name"]] = sec_node
                secondary_by_id[r["id"]] = sec_node
            else:  # level == 3
                parent_secondary = secondary_by_id.get(r["parent_id"])
                if parent_secondary is None:
                    continue
                ter_node = {
                    "name": r["name"],
                    "code": r["code"] or "",
                    "stocks": [],
                }
                parent_secondary["tertiaries"][r["name"]] = ter_node
                tertiary_by_id[r["id"]] = ter_node

    # 加载每只股票的多张图片（stock_images 表，按 sort_order 排序）
    images_by_stock = {}
    try:
        cur.execute(
            "SELECT stock_id, filename FROM stock_images "
            "ORDER BY stock_id, sort_order, id"
        )
        for r in cur.fetchall():
            images_by_stock.setdefault(r["stock_id"], []).append(r["filename"])
    except sqlite3.OperationalError:
        # 旧库尚未建 stock_images 表时忽略（启动迁移会补建）
        images_by_stock = {}

    # 加载股票
    option_cols = ", ".join(STOCK_OPTION_FIELDS)
    cur.execute(
        "SELECT id, tertiary_id, name, code, concept, "
        f"{option_cols} FROM stocks ORDER BY name"
    )
    for s in cur.fetchall():
        t_node = tertiary_by_id.get(s["tertiary_id"])
        if t_node is None:
            continue
        stock = {
            "name": s["name"],
            "code": s["code"] or "",
            "concept1": s["concept"] or "",
            "concept2": "",
            "concept3": "",
            "concept4": "",
            "concept5": "",
        }
        for field in STOCK_OPTION_FIELDS:
            stock[field] = s[field] or ""
        images = list(images_by_stock.get(s["id"], []))
        stock["images"] = images
        stock["image_path"] = images[0] if images else ""
        t_node["stocks"].append(stock)

    # 一级行业按 id 排序（与原 csv 加载行为一致）
    primary_by_name = OrderedDict()
    for k in sorted(primary_by_id.keys()):
        node = primary_by_id[k]
        primary_by_name[node["name"]] = node

    conn.close()
    return primary_by_name


def find_tertiary_id(primary_name, secondary_name, tertiary_name, db_path=None):
    """
    通过 行业路径 查询三级行业的 SQLite id。
    返回 int 或 None（未找到时）。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.id
        FROM industries t
        JOIN industries s ON t.parent_id = s.id
        JOIN industries p ON s.parent_id = p.id
        WHERE p.name = ? AND s.name = ? AND t.name = ?
        """,
        (primary_name, secondary_name, tertiary_name),
    )
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None


def add_stock(
    tertiary_id,
    name,
    code="",
    concept="",
    options=None,
    db_path=None,
):
    """
    新增一只股票到指定三级行业。
    options: 可选 dict，键为 option_a ~ option_h（选项A ~ 选项H）。
    返回新插入股票的 id。
    """
    options = options or {}
    cols = ["tertiary_id", "name", "code", "concept"]
    cols += STOCK_OPTION_FIELDS
    vals = [tertiary_id, name, code, concept]
    vals += [(options.get(f) or "") for f in STOCK_OPTION_FIELDS]
    placeholders = ", ".join("?" * len(cols))
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO stocks ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def add_stocks_batch(tertiary_id, stock_list, db_path=None):
    """
    批量新增股票到指定三级行业。
    stock_list: [{"name": "...", "code": "...", ...}, ...]
    返回插入的行数。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        data = [
            tuple(
                [tertiary_id, s.get("name", ""), s.get("code", ""), s.get("concept", "")]
                + [s.get(f, "") or "" for f in STOCK_OPTION_FIELDS]
            )
            for s in stock_list
        ]
        cols = ["tertiary_id", "name", "code", "concept"]
        cols += STOCK_OPTION_FIELDS
        placeholders = ", ".join("?" * len(cols))
        cur.executemany(
            f"INSERT INTO stocks ({', '.join(cols)}) VALUES ({placeholders})",
            data,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_stock(stock_name, tertiary_id=None, db_path=None):
    """
    删除股票（撤销新增时用）。
    - tertiary_id 可选，提供时精确匹配
    - 返回删除的行数
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if tertiary_id is not None:
            cur.execute(
                "DELETE FROM stocks WHERE name = ? AND tertiary_id = ?",
                (stock_name, tertiary_id),
            )
        else:
            cur.execute("DELETE FROM stocks WHERE name = ?", (stock_name,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_stock(tertiary_id, name, fields, db_path=None):
    """
    更新一只股票的可编辑字段。
    fields 可包含: name/code/concept1~5/option_a~h。
    - concept1~5 会合并（用 '-' 连接非空项）后写入 concept 列
    - 若修改了 name，会返回新名字
    返回 (updated_count, new_name)。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM stocks WHERE tertiary_id = ? AND name = ?",
            (tertiary_id, name),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"未找到股票：{name}")

        sets = []
        params = []
        new_name = name

        if "name" in fields and (fields["name"] or "").strip():
            new_name = fields["name"].strip()
            sets.append("name = ?")
            params.append(new_name)
        if "code" in fields:
            sets.append("code = ?")
            params.append((fields["code"] or "").strip())

        # 选项A~选项H
        for field in STOCK_OPTION_FIELDS:
            if field in fields:
                sets.append(f"{field} = ?")
                params.append((fields[field] or "").strip())

        # concept1~5 合并为一个 concept 列
        concept_parts = []
        for k in ("concept1", "concept2", "concept3", "concept4", "concept5"):
            if k in fields:
                v = (fields[k] or "").strip()
                if v:
                    concept_parts.append(v)
        if concept_parts:
            sets.append("concept = ?")
            params.append("-".join(concept_parts))

        if not sets:
            return 0, name

        params.append(row["id"])
        cur.execute(
            f"UPDATE stocks SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
        return cur.rowcount, new_name
    finally:
        conn.close()


def update_stock_concept(
    stock_name,
    new_concept,
    tertiary_id=None,
    db_path=None,
):
    """
    更新指定股票的概念字段（用 '-' 串接）。
    - tertiary_id 可选：提供时用 (tertiary_id, stock_name) 查找，提高准确度
    - 若未提供，则按 stock_name 全表查找（同名第一只）
    - 若概念已包含该值，会抛 ValueError
    - 写入成功返回新概念字符串
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if tertiary_id is not None:
            cur.execute(
                "SELECT id, concept FROM stocks WHERE tertiary_id = ? AND name = ?",
                (tertiary_id, stock_name),
            )
        else:
            cur.execute(
                "SELECT id, concept FROM stocks WHERE name = ? LIMIT 1",
                (stock_name,),
            )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"未找到股票：{stock_name}")

        existing = row["concept"] or ""
        normalized = (
            existing.replace("-", ",")
                   .replace("，", ",")
                   .replace("、", ",")
                   .replace(" ", ",")
                   .replace(";", ",")
        )
        pieces = {p.strip() for p in normalized.split(",") if p.strip()}
        if new_concept in pieces:
            raise ValueError("该股票已有此概念")

        new_full = f"{existing}-{new_concept}" if existing else new_concept
        cur.execute(
            "UPDATE stocks SET concept = ? WHERE id = ?",
            (new_full, row["id"]),
        )
        conn.commit()
        return new_full
    finally:
        conn.close()


def find_stock_by_name(stock_name, db_path=None):
    """通过股票名称查（tertiary_id, 父级链），供 API 校验使用。"""
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT s.tertiary_id, i.name AS t_name, i.parent_id AS t_parent "
        "FROM stocks s JOIN industries i ON s.tertiary_id = i.id "
        "WHERE s.name = ?",
        (stock_name,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ==================== 概念表（concepts）CRUD ====================

MAX_CONCEPT_LEVEL = 5  # 概念最多五级分类


def _get_descendant_ids(concept_id, db_path=None):
    """返回 concept_id 的所有子孙 id（不含自身）。用于级联删除与防环。"""
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, parent_id FROM concepts")
    children = {}
    for r in cur.fetchall():
        children.setdefault(r["parent_id"], []).append(r["id"])
    conn.close()

    result = []
    stack = [concept_id]
    seen = set()
    while stack:
        pid = stack.pop()
        for cid in children.get(pid, []):
            if cid not in seen:
                seen.add(cid)
                result.append(cid)
                stack.append(cid)
    return result


def _build_concept_tree_order(concepts, usage):
    """
    把概念按「一级 → 其二级 → 其三级」的树形深度优先排序，
    并给每条记录补齐 level / parent_id / path / usage 字段。
    返回扁平有序列表。
    """
    by_id = {c["id"]: c for c in concepts}
    children = {}
    roots = []
    for c in concepts:
        c["level"] = c.get("level") or 1
        c["parent_id"] = c.get("parent_id")
        c["usage"] = usage.get(c["name"], 0)
        c["path"] = c["name"]
        pid = c["parent_id"]
        if pid and pid in by_id:
            children.setdefault(pid, []).append(c)
        else:
            c["parent_id"] = None  # 指向无效父级时视为根
            roots.append(c)

    ordered = []
    visited = set()

    def walk(node, prefix):
        if node["id"] in visited:  # 防脏数据成环
            return
        visited.add(node["id"])
        node["path"] = prefix
        ordered.append(node)
        for child in sorted(children.get(node["id"], []), key=lambda x: x["name"]):
            walk(child, f"{prefix} / {child['name']}")

    for root in sorted(roots, key=lambda x: x["name"]):
        walk(root, root["name"])
    return ordered


def list_concepts(db_path=None):
    """
    返回所有概念（按树形层级排序），每条含：
    {id, name, description, level, parent_id, usage, path}
    path 例如 "固态电池 / 材料 / 正极"。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, description, level, parent_id FROM concepts"
    )
    concepts = [dict(r) for r in cur.fetchall()]

    # 统计每个概念在股票中的使用次数
    usage = {}
    cur.execute(
        "SELECT concept FROM stocks "
        "WHERE concept IS NOT NULL AND concept != ''"
    )
    for row in cur.fetchall():
        for piece in _split_concept_raw(row["concept"]):
            usage[piece] = usage.get(piece, 0) + 1
    conn.close()

    return _build_concept_tree_order(concepts, usage)


def list_primary_concepts(db_path=None):
    """
    返回所有一级概念（level=1），按名称排序。
    每条：{name, description, usage}
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT name, description FROM concepts "
        "WHERE level = 1 ORDER BY name"
    )
    items = [
        {"name": r["name"], "description": r["description"] or ""}
        for r in cur.fetchall()
    ]
    # 统计每条概念在股票中的使用次数
    usage = {}
    cur.execute(
        "SELECT concept FROM stocks "
        "WHERE concept IS NOT NULL AND concept != ''"
    )
    for row in cur.fetchall():
        for piece in _split_concept_raw(row["concept"]):
            usage[piece] = usage.get(piece, 0) + 1
    conn.close()
    for it in items:
        it["usage"] = usage.get(it["name"], 0)
    return items


def add_concept(name, description="", level=None, parent_id=None, db_path=None):
    """
    新增概念（支持最多五级分类）。
    - parent_id 提供时：自动推断 level = 父级 level + 1（最多五级）
    - parent_id 为空时：level = 1（一级概念）
    返回新概念 id。重名/越级抛 ValueError，父级不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if parent_id is not None:
            cur.execute("SELECT level FROM concepts WHERE id = ?", (parent_id,))
            p = cur.fetchone()
            if p is None:
                raise LookupError(f"上级概念 id={parent_id} 不存在")
            inferred = (p["level"] or 1) + 1
            if inferred > MAX_CONCEPT_LEVEL:
                raise ValueError(f"概念最多 {MAX_CONCEPT_LEVEL} 级分类，不能超过五级")
            level = inferred
        else:
            level = 1
        cur.execute(
            "INSERT INTO concepts (name, description, level, parent_id) "
            "VALUES (?, ?, ?, ?)",
            (name, description or "", level, parent_id),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"概念「{name}」已存在")
    finally:
        conn.close()


def update_concept(
    concept_id,
    name=None,
    description=None,
    parent_id="__keep__",
    db_path=None,
):
    """
    编辑概念（名称/描述/上级分类）。
    - parent_id 传 "__keep__" 表示保持原上级不变
    - parent_id 传 None/"" 表示移动到根（一级）
    - parent_id 传整数表示移动到该上级（自动重算 level）
    返回 (old_name, new_name)。
    重名/越级/成环时抛 ValueError，id 不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, description, level, parent_id FROM concepts WHERE id = ?",
            (concept_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"未找到概念 id={concept_id}")

        old_name = row["name"]
        new_name = (name or "").strip() or old_name
        new_desc = (
            description if description is not None else (row["description"] or "")
        )
        new_level = row["level"] or 1
        new_parent = row["parent_id"]

        if parent_id != "__keep__":
            if parent_id is None or parent_id == "":
                new_parent = None
                new_level = 1
            else:
                pid = int(parent_id)
                if pid == concept_id:
                    raise ValueError("上级分类不能是自身")
                if pid in _get_descendant_ids(concept_id, db_path):
                    raise ValueError("上级分类不能是其子孙分类")
                cur.execute("SELECT level FROM concepts WHERE id = ?", (pid,))
                p = cur.fetchone()
                if p is None:
                    raise LookupError(f"上级分类 id={pid} 不存在")
                new_level = (p["level"] or 1) + 1
                if new_level > MAX_CONCEPT_LEVEL:
                    raise ValueError(f"概念最多 {MAX_CONCEPT_LEVEL} 级分类，不能超过五级")
                new_parent = pid

        cur.execute(
            "UPDATE concepts SET name = ?, description = ?, level = ?, parent_id = ? "
            "WHERE id = ?",
            (new_name, new_desc, new_level, new_parent, concept_id),
        )
        conn.commit()
        return old_name, new_name
    except sqlite3.IntegrityError:
        raise ValueError(f"概念「{new_name}」已存在")
    finally:
        conn.close()


def delete_concept(concept_id, db_path=None):
    """
    删除概念（级联删除其所有子孙分类）。
    返回被删除的概念名列表（含自身与子孙），供同步股票概念串。
    id 不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM concepts WHERE id = ?", (concept_id,))
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"未找到概念 id={concept_id}")

        desc_ids = _get_descendant_ids(concept_id, db_path)
        all_ids = [concept_id] + desc_ids

        names = []
        for cid in all_ids:
            cur.execute("SELECT name FROM concepts WHERE id = ?", (cid,))
            r = cur.fetchone()
            if r:
                names.append(r["name"])

        placeholders = ",".join("?" * len(all_ids))
        cur.execute(f"DELETE FROM concepts WHERE id IN ({placeholders})", all_ids)
        conn.commit()
        return names
    finally:
        conn.close()


def sync_concept_in_stocks(old_name, new_name=None, db_path=None):
    """
    同步股票表中某概念：
    - new_name 提供时：把所有股票概念串中的 old_name 替换为 new_name（重命名）
    - new_name 为 None 时：把 old_name 从所有股票概念串中移除（删除）
    返回受影响的股票行数。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, concept FROM stocks WHERE concept LIKE ?",
            (f"%{old_name}%",),
        )
        changed = 0
        for row in cur.fetchall():
            pieces = _split_concept_raw(row["concept"])
            if new_name is not None:
                new_pieces = [new_name if p == old_name else p for p in pieces]
            else:
                new_pieces = [p for p in pieces if p != old_name]
            new_val = "-".join(new_pieces)
            if new_val != (row["concept"] or ""):
                cur.execute(
                    "UPDATE stocks SET concept = ? WHERE id = ?",
                    (new_val, row["id"]),
                )
                changed += 1
        conn.commit()
        return changed
    finally:
        conn.close()


def import_concepts_from_stocks(db_path=None):
    """
    把 stocks.concept 中出现的所有概念导入 concepts 表（去重，已存在跳过）。
    返回新增的概念数。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT concept FROM stocks "
            "WHERE concept IS NOT NULL AND concept != ''"
        )
        seen = set()
        for row in cur.fetchall():
            for piece in _split_concept_raw(row["concept"]):
                seen.add(piece)

        cur.execute("SELECT name FROM concepts")
        existing = {r["name"] for r in cur.fetchall()}

        added = 0
        for name in sorted(seen):
            if name not in existing:
                cur.execute(
                    "INSERT INTO concepts (name, description) VALUES (?, '')",
                    (name,),
                )
                added += 1
        conn.commit()
        return added
    finally:
        conn.close()


def get_concept_stocks(concept_name, db_path=None):
    """
    查询使用了某概念的所有股票。
    返回 [{"name", "code", "concept", "primary", "secondary", "tertiary"}]。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.name, s.code, s.concept,
               p.name AS primary_name, sc.name AS secondary_name, t.name AS tertiary_name
        FROM stocks s
        JOIN industries t ON s.tertiary_id = t.id
        JOIN industries sc ON t.parent_id = sc.id
        JOIN industries p ON sc.parent_id = p.id
        WHERE s.concept IS NOT NULL AND s.concept != ''
        """
    )
    result = []
    for row in cur.fetchall():
        if concept_name in _split_concept_raw(row["concept"]):
            result.append({
                "name": row["name"],
                "code": row["code"] or "",
                "concept": row["concept"],
                "primary": row["primary_name"],
                "secondary": row["secondary_name"],
                "tertiary": row["tertiary_name"],
            })
    conn.close()
    return result


def remove_concept_from_stock(stock_name, concept_name, db_path=None):
    """
    从指定股票的概念串中移除某概念（其余概念保留，'-' 重组）。
    返回 (new_concept, changed: bool)。股票不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, concept FROM stocks WHERE name = ?",
            (stock_name,),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"未找到股票：{stock_name}")

        pieces = _split_concept_raw(row["concept"])
        if concept_name not in pieces:
            return row["concept"] or "", False
        new_pieces = [p for p in pieces if p != concept_name]
        new_val = "-".join(new_pieces)
        cur.execute(
            "UPDATE stocks SET concept = ? WHERE id = ?",
            (new_val, row["id"]),
        )
        conn.commit()
        return new_val, True
    finally:
        conn.close()


def _stock_row_id(cur, tertiary_id, stock_name):
    cur.execute(
        "SELECT id FROM stocks WHERE tertiary_id = ? AND name = ?",
        (tertiary_id, stock_name),
    )
    return cur.fetchone()


def _list_images_conn(conn, stock_id):
    cur = conn.cursor()
    cur.execute(
        "SELECT filename FROM stock_images WHERE stock_id = ? "
        "ORDER BY sort_order, id",
        (stock_id,),
    )
    return [r["filename"] for r in cur.fetchall()]


def list_stock_images(tertiary_id, stock_name, db_path=None):
    """返回该股票的图片文件名列表（按顺序）；股票不存在返回 []。"""
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        row = _stock_row_id(cur, tertiary_id, stock_name)
        if row is None:
            return []
        return _list_images_conn(conn, row["id"])
    finally:
        conn.close()


def add_stock_image(tertiary_id, stock_name, filename, db_path=None):
    """
    追加一张图片，并把 stocks.image_path 同步为第一张（兼容旧逻辑）。
    返回更新后的文件名列表；股票不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        row = _stock_row_id(cur, tertiary_id, stock_name)
        if row is None:
            raise LookupError(f"未找到股票：{stock_name}")
        stock_id = row["id"]
        cur.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order "
            "FROM stock_images WHERE stock_id = ?",
            (stock_id,),
        )
        next_order = cur.fetchone()["next_order"]
        conn.execute(
            "INSERT INTO stock_images (stock_id, filename, sort_order, created_at) "
            "VALUES (?, ?, ?, '')",
            (stock_id, filename, next_order),
        )
        images = _list_images_conn(conn, stock_id)
        conn.commit()
        return images
    finally:
        conn.close()


def delete_stock_image(tertiary_id, stock_name, filename="", db_path=None):
    """
    删除该股票的一张图片（filename 为空则删除全部）。
    返回删除后剩余的文件名列表；股票不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        row = _stock_row_id(cur, tertiary_id, stock_name)
        if row is None:
            raise LookupError(f"未找到股票：{stock_name}")
        stock_id = row["id"]
        if filename:
            conn.execute(
                "DELETE FROM stock_images WHERE stock_id = ? AND filename = ?",
                (stock_id, filename),
            )
        else:
            conn.execute(
                "DELETE FROM stock_images WHERE stock_id = ?", (stock_id,)
            )
        images = _list_images_conn(conn, stock_id)
        conn.commit()
        return images
    finally:
        conn.close()


def get_stock_image(tertiary_id, stock_name, db_path=None):
    """返回该股票的第一张图片文件名（不存在则返回 ''），用于路由读取。"""
    imgs = list_stock_images(tertiary_id, stock_name, db_path)
    return imgs[0] if imgs else ""


def set_stock_image(tertiary_id, stock_name, image_path, db_path=None):
    """
    （兼容旧接口）把该股票的图片列表整体替换为单张 image_path（空则清空）。
    股票不存在抛 LookupError。不会删除磁盘文件（删除请走路由层）。
    """
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        row = _stock_row_id(cur, tertiary_id, stock_name)
        if row is None:
            raise LookupError(f"未找到股票：{stock_name}")
        stock_id = row["id"]
        conn.execute("DELETE FROM stock_images WHERE stock_id = ?", (stock_id,))
        if image_path:
            conn.execute(
                "INSERT INTO stock_images (stock_id, filename, sort_order, created_at) "
                "VALUES (?, ?, 0, '')",
                (stock_id, image_path),
            )
        conn.commit()
        return image_path or ""
    finally:
        conn.close()


def get_all_stocks_flat(db_path=None):
    """
    返回所有股票的扁平列表（供 datalist 自动补全）。
    [{"name", "code", "primary", "secondary", "tertiary"}]
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.name, s.code,
               p.name AS primary_name, sc.name AS secondary_name, t.name AS tertiary_name
        FROM stocks s
        JOIN industries t ON s.tertiary_id = t.id
        JOIN industries sc ON t.parent_id = sc.id
        JOIN industries p ON sc.parent_id = p.id
        ORDER BY s.name
        """
    )
    result = [
        {
            "name": r["name"],
            "code": r["code"] or "",
            "primary": r["primary_name"],
            "secondary": r["secondary_name"],
            "tertiary": r["tertiary_name"],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return result