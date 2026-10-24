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
import datetime
import os
import re
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

-- 概念名只要求「同一上级下不重名」：不同分支可以各有一个「其他」
CREATE TABLE IF NOT EXISTS concepts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
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
-- 同一上级下不允许同名；一级概念 parent_id 为 NULL，SQLite 唯一索引中 NULL 互不相等，
-- 故用 IFNULL(parent_id, 0) 兜底，保证一级概念同名也能拦住
CREATE UNIQUE INDEX IF NOT EXISTS idx_concepts_parent_name
    ON concepts(IFNULL(parent_id, 0), name);
"""


# 概念名只要求「同一上级下不重名」，故 name 不再带列级 UNIQUE
_CONCEPTS_DDL = """
CREATE TABLE concepts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    code        TEXT DEFAULT '',
    level       INTEGER NOT NULL DEFAULT 1,
    parent_id   INTEGER,
    FOREIGN KEY(parent_id) REFERENCES concepts(id)
)
"""


def _concepts_name_unique_is_global(cur):
    """判断 concepts 是否仍是旧结构（name 全局唯一）。
    列级 UNIQUE 在 SQLite 里表现为一个名为 sqlite_autoindex_* 的唯一索引。"""
    try:
        rows = cur.execute("PRAGMA index_list(concepts)").fetchall()
    except Exception:
        return False
    for r in rows:
        if not r["unique"]:
            continue
        try:
            cols = [c["name"] for c in cur.execute(f"PRAGMA index_info({r['name']})").fetchall()]
        except Exception:
            continue
        if cols == ["name"]:
            return True
    return False


def _rebuild_concepts_for_sibling_unique(conn):
    """
    旧库迁移：把 concepts 的「name 全局唯一」改成「同一上级下唯一」。
    SQLite 无法直接删列约束，只能建新表 → 搬数据 → 换名。
    """
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(f"""
        BEGIN;
        CREATE TABLE concepts__new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            code        TEXT DEFAULT '',
            level       INTEGER NOT NULL DEFAULT 1,
            parent_id   INTEGER,
            FOREIGN KEY(parent_id) REFERENCES concepts__new(id)
        );
        INSERT INTO concepts__new (id, name, description, code, level, parent_id)
            SELECT id, name, description, code, level, parent_id FROM concepts;
        DROP TABLE concepts;
        ALTER TABLE concepts__new RENAME TO concepts;
        COMMIT;
    """)
    # 修正自增序列，避免新概念复用已删除的 id
    conn.execute("""
        INSERT INTO sqlite_sequence(name, seq)
        SELECT 'concepts', COALESCE(MAX(id), 0) FROM concepts
        WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'concepts')
    """)
    conn.execute("""
        UPDATE sqlite_sequence
           SET seq = (SELECT COALESCE(MAX(id), 0) FROM concepts)
         WHERE name = 'concepts'
           AND COALESCE(seq, 0) < (SELECT COALESCE(MAX(id), 0) FROM concepts)
    """)
    # 重建过程可能留下同名重复行，保留 seq 最大的一条即可
    conn.execute("""
        DELETE FROM sqlite_sequence
         WHERE rowid NOT IN (
             SELECT MAX(rowid) FROM sqlite_sequence GROUP BY name
         )
    """)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


def ensure_concepts_table(db_path=None):
    """
    确保 concepts 表存在（对旧数据库的增量迁移，不动其他表数据）。
    旧库若无 level / parent_id 列，则用 ALTER TABLE 增量补齐；
    旧库若仍是「name 全局唯一」，则重建表改为「同一上级下唯一」。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='concepts'")
    if cur.fetchone() is None:
        conn.executescript(_CONCEPTS_DDL)
    elif _concepts_name_unique_is_global(cur):
        _rebuild_concepts_for_sibling_unique(conn)
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_concepts_name ON concepts(name);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_concepts_parent_name
            ON concepts(IFNULL(parent_id, 0), name);
    """)
    # 增量迁移：旧版本 concepts 表可能没有 level / parent_id / code 列
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(concepts)")
    cols = {row["name"] for row in cur.fetchall()}
    if "level" not in cols:
        conn.execute("ALTER TABLE concepts ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE concepts ADD COLUMN parent_id INTEGER")
    if "code" not in cols:
        conn.execute("ALTER TABLE concepts ADD COLUMN code TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def set_concept_code(concept_id, code, db_path=None):
    """
    单独设置概念编码（一级概念的手动编码）。
    code 为空串表示清空。id 不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM concepts WHERE id = ?", (concept_id,))
        if cur.fetchone() is None:
            raise LookupError(f"未找到概念 id={concept_id}")
        cur.execute(
            "UPDATE concepts SET code = ? WHERE id = ?",
            ((code or "").strip(), concept_id),
        )
        conn.commit()
    finally:
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


# ---------------------------------------------------------------------------
# 修改日志表：每只股票每日一条，去重
# ---------------------------------------------------------------------------
STOCK_MODIFICATION_LOG_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS stock_modification_log (
    stock_id      INTEGER NOT NULL,
    modified_date TEXT NOT NULL,
    modified_at   TEXT NOT NULL,
    PRIMARY KEY (stock_id, modified_date),
    FOREIGN KEY(stock_id) REFERENCES stocks(id)
);
CREATE INDEX IF NOT EXISTS idx_stock_mod_log_stock
    ON stock_modification_log(stock_id, modified_date DESC);
"""


# ---------------------------------------------------------------------------
# 搜索历史表：保存最近 30 天的搜索关键词
# ---------------------------------------------------------------------------
SEARCH_HISTORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS search_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    searched_at TEXT NOT NULL,
    hit_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_search_history_time
    ON search_history(searched_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_history_keyword
    ON search_history(keyword, searched_at DESC);
"""

# 搜索历史保留天数（3 年）
SEARCH_HISTORY_RETENTION_DAYS = 3 * 365


def ensure_search_history_table(db_path=None):
    """
    确保 search_history 表存在。幂等。
    同时清理超过 SEARCH_HISTORY_RETENTION_DAYS 天的旧记录。
    """
    conn = get_conn(db_path)
    try:
        conn.executescript(SEARCH_HISTORY_TABLE_DDL)
        conn.commit()
    finally:
        conn.close()
    # 启动时顺手清理一次 30 天前的旧记录
    prune_search_history(days=SEARCH_HISTORY_RETENTION_DAYS, db_path=db_path)


def record_search_history(keyword, hit_count=0, conn=None):
    """
    记录一次搜索关键词。空字符串会被忽略。
    hit_count 写入时直接覆盖。
    若 conn 已传入则参与外部事务；否则自管连接。
    """
    kw = (keyword or "").strip()
    if not kw:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO search_history (keyword, searched_at, hit_count) "
            "VALUES (?, ?, ?)",
            (kw, _now_datetime_str(), int(hit_count or 0)),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def record_search_history_batch(keywords, hit_count=0, db_path=None):
    """
    一次请求里多个关键词一起写入（按 keyword 去重；同一个搜索里的
    重复词只写一次，hit_count 相同）。
    """
    seen = set()
    items = []
    for kw in keywords or []:
        n = (kw or "").strip()
        if not n:
            continue
        lk = n.lower()
        if lk in seen:
            continue
        seen.add(lk)
        items.append(n)
    if not items:
        return
    conn = get_conn(db_path)
    try:
        now = _now_datetime_str()
        conn.executemany(
            "INSERT INTO search_history (keyword, searched_at, hit_count) "
            "VALUES (?, ?, ?)",
            [(kw, now, int(hit_count or 0)) for kw in items],
        )
        conn.commit()
    finally:
        conn.close()


def prune_search_history(days=SEARCH_HISTORY_RETENTION_DAYS, db_path=None):
    """
    删除 searched_at 早于 (今天 - days) 的旧记录。
    返回删除的条数。
    """
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = SEARCH_HISTORY_RETENTION_DAYS
    cutoff = (datetime.date.today() - datetime.timedelta(days=d)).isoformat()
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM search_history WHERE substr(searched_at, 1, 10) < ?",
            (cutoff,),
        )
        deleted = cur.rowcount or 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def list_search_history(days=SEARCH_HISTORY_RETENTION_DAYS, limit=1000, db_path=None):
    """
    返回最近 N 天内**全部**搜索记录（不去重），按时间倒序。
    每条返回 {"id": int, "keyword": str, "searched_at": str, "hit_count": int}。
    用于页面 M：完整展示 30 天内的搜索明细。
    limit 防止单次拉取过多，默认 1000。
    """
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = SEARCH_HISTORY_RETENTION_DAYS
    cutoff = (datetime.date.today() - datetime.timedelta(days=d)).isoformat()
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id, keyword, searched_at, hit_count
            FROM search_history
            WHERE substr(searched_at, 1, 10) >= ?
            ORDER BY searched_at DESC
            LIMIT ?
            """,
            (cutoff, int(limit or 1000)),
        )
        return [
            {
                "id": row["id"],
                "keyword": row["keyword"],
                "searched_at": row["searched_at"],
                "hit_count": row["hit_count"] or 0,
            }
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


def get_recent_search_history(limit=20, days=SEARCH_HISTORY_RETENTION_DAYS, db_path=None):
    """
    返回最近一次搜索的关键词（去重），按最新搜索时间倒序。
    每条返回 {"keyword": str, "searched_at": str, "hit_count": int}。
    limit 控制返回条数。
    """
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = SEARCH_HISTORY_RETENTION_DAYS
    cutoff = (datetime.date.today() - datetime.timedelta(days=d)).isoformat()
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            """
            SELECT keyword, MAX(searched_at) AS last_at,
                   (SELECT hit_count FROM search_history sh2
                    WHERE sh2.keyword = sh1.keyword
                    ORDER BY sh2.searched_at DESC LIMIT 1) AS hit_count
            FROM search_history sh1
            WHERE substr(searched_at, 1, 10) >= ?
            GROUP BY keyword
            ORDER BY last_at DESC
            LIMIT ?
            """,
            (cutoff, int(limit or 20)),
        )
        return [
            {
                "keyword": row["keyword"],
                "searched_at": row["last_at"],
                "hit_count": row["hit_count"] or 0,
            }
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


def clear_search_history(db_path=None):
    """清空全部搜索历史。返回删除条数。"""
    conn = get_conn(db_path)
    try:
        cur = conn.execute("DELETE FROM search_history")
        deleted = cur.rowcount or 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def ensure_stock_modification_log_table(db_path=None):
    """
    确保 stock_modification_log 表存在。幂等。
    """
    conn = get_conn(db_path)
    try:
        conn.executescript(STOCK_MODIFICATION_LOG_TABLE_DDL)
        conn.commit()
    finally:
        conn.close()


def _now_date_str():
    return datetime.date.today().isoformat()


def _now_datetime_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _record_modification_in_conn(conn_or_cur, stock_id):
    """
    在同一事务（外部 conn）内写入一条"今日修改"记录。
    由于 PRIMARY KEY (stock_id, modified_date) 约束，同一只股票
    同一天多次调用只会保留一行（保留最早 modified_at）。
    stock_id 非法则跳过。
    """
    if not stock_id:
        return
    today = _now_date_str()
    now = _now_datetime_str()
    conn_or_cur.execute(
        "INSERT OR IGNORE INTO stock_modification_log "
        "(stock_id, modified_date, modified_at) "
        "VALUES (?, ?, ?)",
        (stock_id, today, now),
    )


def get_stock_modification_dates(stock_ids, db_path=None):
    """
    批量查询多只股票的修改日期列表（YYYY-MM-DD 字符串数组），
    按日期倒序，相同 stock_id 一起返回。
    返回 {stock_id: [date1, date2, ...]}；没有记录的股票不会出现在结果中。
    """
    ids = [i for i in (stock_ids or []) if i]
    if not ids:
        return {}
    conn = get_conn(db_path)
    try:
        placeholders = ",".join("?" * len(ids))
        cur = conn.execute(
            f"SELECT stock_id, modified_date FROM stock_modification_log "
            f"WHERE stock_id IN ({placeholders}) "
            f"ORDER BY stock_id, modified_date DESC",
            ids,
        )
        result = {}
        for r in cur.fetchall():
            result.setdefault(r["stock_id"], []).append(r["modified_date"])
        return result
    finally:
        conn.close()


def delete_stock_modification_log(stock_id, conn=None):
    """
    删除指定股票的全部修改日志。股票被删除时同步清理。
    若 conn 已传入，则参与外部事务（不 commit）；否则自己开/关。
    """
    if not stock_id:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM stock_modification_log WHERE stock_id = ?",
            (stock_id,),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def get_concept_stocks_map(names=None, db_path=None):
    """
    批量返回 {概念名: [股票名, ...]}。
    names 为 None 时返回全量；否则只返回这些概念（无股票的也返回空列表）。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name AS stock_name, concept FROM stocks")
    wanted = set(names) if names else None
    result = {}
    if wanted is not None:
        for n in wanted:
            result[n] = []
    for row in cur.fetchall():
        # 用「展开后的各级概念名」匹配：挂在子级路径下的股票，
        # 其路径上的每一级概念都能命中
        segs = _split_concept_segments(row["concept"])
        for seg in segs:
            if wanted is not None and seg not in wanted:
                continue
            result.setdefault(seg, []).append(row["stock_name"])
    for k in result:
        result[k] = sorted(set(result[k]))
    conn.close()
    return result


# 概念完整路径的内部层级分隔符，如 "固态电池/材料/负极/硅氧负极"。
# 注意：概念之间仍用 '-' 等分隔（见 _split_concept_raw），
# '/' 只出现在「一条概念 = 多级路径」的内部，两者不冲突。
CONCEPT_PATH_SEP = "/"


def _split_concept_raw(raw):
    """按 - , ， 、 空格 ; 等分隔符切分概念串。

    每个片段可能是一个概念名，也可能是多级概念的完整路径
    （如 "固态电池/材料/负极/硅氧负极"，路径内部用 '/' 连接）。
    """
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


def _split_concept_segments(raw):
    """把概念串展开成所有层级的概念名（含完整路径内部的各级）。

    例："固态电池/材料/负极-机器人" → ["固态电池", "材料", "负极", "机器人"]
    用于概念匹配 / 使用次数统计：挂在 5 级概念下的股票，
    其 1~5 级各级概念都能命中。
    """
    segs = []
    seen = set()
    for item in _split_concept_raw(raw):
        for seg in item.split(CONCEPT_PATH_SEP):
            seg = seg.strip()
            if seg and seg not in seen:
                seen.add(seg)
                segs.append(seg)
    return segs


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

    # 股票表被重建，关联表按新数据重刷一遍（失败不影响建库结果）
    try:
        rebuild_stock_concepts(db_path)
    except Exception:
        pass

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
            "id": s["id"],   # 供搜索接口批量取修改历史等使用
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


def find_secondary_id(primary_name, secondary_name, db_path=None):
    """通过「一级 / 二级」行业名查询二级行业的 id，未找到返回 None。"""
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id
        FROM industries s
        JOIN industries p ON s.parent_id = p.id
        WHERE p.name = ? AND s.name = ? AND s.level = 2
        """,
        (primary_name, secondary_name),
    )
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None


def add_tertiary_industry(primary_name, secondary_name, name, code="", db_path=None):
    """
    在指定二级行业下新增一个三级行业（如 有色 / 能源金属 下新增「镍」）。

    返回新三级行业的 id。
    同名三级行业已存在时抛 ValueError；二级行业不存在时抛 LookupError。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("三级行业名称不能为空")
    if "/" in name:
        raise ValueError("三级行业名称不能包含「/」")

    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT t.id
            FROM industries t
            JOIN industries s ON t.parent_id = s.id
            JOIN industries p ON s.parent_id = p.id
            WHERE p.name = ? AND s.name = ? AND t.name = ? AND t.level = 3
            """,
            (primary_name, secondary_name, name),
        )
        if cur.fetchone():
            raise ValueError(f"三级行业「{name}」在 {primary_name} / {secondary_name} 下已存在")

        cur.execute(
            """
            SELECT s.id
            FROM industries s
            JOIN industries p ON s.parent_id = p.id
            WHERE p.name = ? AND s.name = ? AND s.level = 2
            """,
            (primary_name, secondary_name),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"未找到二级行业：{primary_name} / {secondary_name}")

        cur.execute(
            "INSERT INTO industries (level, code, acode, name, parent_id) "
            "VALUES (3, ?, '', ?, ?)",
            (code or "", name, row["id"]),
        )
        new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def delete_tertiary_industry(tertiary_id, db_path=None):
    """删除一个三级行业（须为空，不含股票），返回 True。"""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) AS c FROM stocks WHERE tertiary_id = ?", (tertiary_id,)
        )
        if cur.fetchone()["c"] > 0:
            raise ValueError("该三级行业下还有股票，不能删除")
        cur.execute("DELETE FROM industries WHERE id = ? AND level = 3", (tertiary_id,))
        conn.commit()
        return True
    finally:
        conn.close()


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
        new_id = cur.lastrowid
        # 新增即视为首次修改，记入日志
        _record_modification_in_conn(cur, new_id)
        conn.commit()
        return new_id
    finally:
        conn.close()


def add_stocks_batch(tertiary_id, stock_list, db_path=None):
    """
    批量新增股票到指定三级行业。
    stock_list: [{"name": "...", "code": "...", ...}, ...]
    返回插入的行数。
    """
    if not stock_list:
        return 0
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
        # 取回此次插入的所有 stock_id（按 name 反查）
        names = [s.get("name", "") for s in stock_list if s.get("name")]
        if names:
            ph = ",".join("?" * len(names))
            cur.execute(
                f"SELECT id, name FROM stocks "
                f"WHERE tertiary_id = ? AND name IN ({ph})",
                [tertiary_id] + names,
            )
            for r in cur.fetchall():
                _record_modification_in_conn(cur, r["id"])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_stock(stock_name, tertiary_id=None, db_path=None):
    """
    删除股票（撤销新增时用）。
    - tertiary_id 可选，提供时精确匹配
    - 返回删除的行数（同步清理该股票的修改日志）
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        # 先取出要删除的 stock_id（可能多条同名跨三级行业）
        if tertiary_id is not None:
            cur.execute(
                "SELECT id FROM stocks WHERE name = ? AND tertiary_id = ?",
                (stock_name, tertiary_id),
            )
        else:
            cur.execute("SELECT id FROM stocks WHERE name = ?", (stock_name,))
        stock_ids = [r["id"] for r in cur.fetchall()]
        for sid in stock_ids:
            cur.execute(
                "DELETE FROM stock_modification_log WHERE stock_id = ?",
                (sid,),
            )
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


def _merge_concept_parts(concept_parts, old_raw):
    """把提交的概念（可能只是一级名）与旧值合并，尽量保留已有的深层路径。

    页面 E/F 的概念下拉只有一级概念，若用户提交「固态电池」而旧值是
    「固态电池/材料/负极/硅氧负极」，直接覆盖会丢掉 4 级归属，
    因此这里保留更细的那条路径。
    """
    # 旧值里按「一级概念名」索引最细的一条（含路径的优先于裸一级名）
    old_by_root = {}
    for item in _split_concept_raw(old_raw):
        segs = [s.strip() for s in item.split(CONCEPT_PATH_SEP) if s.strip()]
        if not segs:
            continue
        root = segs[0]
        prev = old_by_root.get(root)
        if prev is None or len(segs) > len(prev):
            old_by_root[root] = segs

    final = []
    for p in concept_parts:
        segs = [s.strip() for s in p.split(CONCEPT_PATH_SEP) if s.strip()]
        if not segs:
            continue
        old = old_by_root.get(segs[0])
        # 提交的是一级概念，但股票已挂了该一级下更细的路径 → 保留路径
        if len(segs) == 1 and old and len(old) > 1:
            final.append(CONCEPT_PATH_SEP.join(old))
        else:
            final.append(CONCEPT_PATH_SEP.join(segs))

    deduped = []
    for f in final:
        if f not in deduped:
            deduped.append(f)
    return "-".join(deduped)


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

        new_concept_text = None
        # concept1~5 合并为一个 concept 列
        concept_parts = []
        for k in ("concept1", "concept2", "concept3", "concept4", "concept5"):
            if k in fields:
                v = (fields[k] or "").strip()
                if v:
                    concept_parts.append(v)
        if concept_parts:
            cur.execute("SELECT concept FROM stocks WHERE id = ?", (row["id"],))
            prow = cur.fetchone()
            old_raw = (prow["concept"] if prow else "") or ""
            new_concept_text = _merge_concept_parts(concept_parts, old_raw)
            sets.append("concept = ?")
            params.append(new_concept_text)

        if not sets:
            return 0, name

        params.append(row["id"])
        cur.execute(
            f"UPDATE stocks SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        if new_concept_text is not None:
            _sync_stock_concept_rows(cur, row["id"], new_concept_text)
        # 编辑就视为修改（即使内容实际相同，也计一次）
        _record_modification_in_conn(cur, row["id"])
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
    更新指定股票的概念字段（多个概念用 '-' 串接）。
    - new_concept 可以是概念名，也可以是完整路径（如 "固态电池/材料/负极"）
    - tertiary_id 可选：提供时用 (tertiary_id, stock_name) 查找，提高准确度
    - 若未提供，则按 stock_name 全表查找（同名第一只）
    - 若概念已包含该值（或已包含其末级概念），会抛 ValueError
    - 若旧值中已存在本次路径的更粗粒度前缀（如仅 "固态电池"），会被更细的路径替换
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

        new_segs = [
            s.strip() for s in new_concept.split(CONCEPT_PATH_SEP) if s.strip()
        ]
        new_norm = CONCEPT_PATH_SEP.join(new_segs) if new_segs else new_concept
        new_leaf = new_segs[-1] if new_segs else new_concept

        kept = []
        for item in _split_concept_raw(row["concept"]):
            segs = [s.strip() for s in item.split(CONCEPT_PATH_SEP) if s.strip()]
            if not segs:
                continue
            item_norm = CONCEPT_PATH_SEP.join(segs)
            if new_norm == item_norm:
                raise ValueError("该股票已有此概念")
            # 新概念是这条旧值的更细版本（旧值是其前缀）→ 用更细的覆盖粗的
            if new_norm.startswith(item_norm + CONCEPT_PATH_SEP):
                continue
            # 旧值已经是本次概念的更细版本 → 保留更细的那条
            if item_norm.startswith(new_norm + CONCEPT_PATH_SEP):
                kept.append(item_norm)
                continue
            # 概念名允许在不同分支下重复（如「硫化物/其他」与「隔膜/其他」），
            # 路径不同即视为不同概念，不再按末级名字判重
            kept.append(item_norm)

        new_full = "-".join(kept + [new_norm]) if kept else new_norm
        cur.execute(
            "UPDATE stocks SET concept = ? WHERE id = ?",
            (new_full, row["id"]),
        )
        # 同步「股票 × 概念」关联表（新概念按当前时间记录）
        _sync_stock_concept_rows(cur, row["id"], new_full)
        _record_modification_in_conn(cur, row["id"])
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

    # 一级概念按「新增时间」倒序（id 越大越晚创建），最新加的排最前
    for root in sorted(roots, key=lambda x: x["id"], reverse=True):
        walk(root, root["name"])
    return ordered


def get_stock_counts(db_path=None):
    """返回股票统计：(总股票数, 含概念的股票数)。

    - 总股票数：stocks 表行数
    - 含概念的股票数：stocks.concept 去空后非空的数量
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS c FROM stocks")
        total = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM stocks "
            "WHERE TRIM(COALESCE(concept, '')) <> ''"
        )
        with_concept = cur.fetchone()["c"]
        return total, with_concept
    finally:
        conn.close()


def get_stock_option_b(name, db_path=None):
    """按股票名称查询「选项B」(option_b) 与「选项D」(option_d) 字段。

    返回 dict {"option_b": ..., "option_d": ...}；
    股票不存在时返回 None。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT option_b, option_d FROM stocks WHERE name = ?", (name,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "option_b": row["option_b"] or "",
            "option_d": row["option_d"] or "",
        }
    finally:
        conn.close()


def get_stock_industry_path(name, db_path=None):
    """按股票名称查询其所属行业路径（一级/二级/三级行业名）。

    返回 dict {"primary": .., "secondary": .., "tertiary": ..}（名称，可能为空串）；
    股票不存在时返回 None。

    同名股票可能存在多个三级行业下，这里只取第一条匹配。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.name AS primary_name,
                   s.name AS secondary_name,
                   t.name AS tertiary_name
            FROM stocks st
            JOIN industries t ON st.tertiary_id = t.id
            JOIN industries s ON t.parent_id = s.id
            JOIN industries p ON s.parent_id = p.id
            WHERE st.name = ?
            LIMIT 1
            """,
            (name,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "primary": row["primary_name"] or "",
            "secondary": row["secondary_name"] or "",
            "tertiary": row["tertiary_name"] or "",
        }
    finally:
        conn.close()


def build_concept_path(name, db_path=None):
    """
    返回概念 name 的完整路径串（一级/二级/.../name），用于写入股票 concept 列。
    - name 是一级概念时返回 name 本身
    - name 是子概念时返回 "固态电池/材料/负极/硅氧负极" 这样的完整路径
    - name 不在概念表时返回 None
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, name, parent_id FROM concepts")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    by_id = {r["id"]: r for r in rows}
    node = None
    for r in rows:
        if r["name"] == name:
            node = r
            break
    if node is None:
        return None

    names = []
    seen = set()
    while node is not None and node["id"] not in seen:
        seen.add(node["id"])
        names.append(node["name"])
        pid = node["parent_id"]
        node = by_id.get(pid) if pid is not None else None
    names.reverse()
    return CONCEPT_PATH_SEP.join(names)


def _path_of_id(rows, concept_id):
    """在已取出的概念行里按 id 拼出完整路径串。"""
    by_id = {r["id"]: r for r in rows}
    node = by_id.get(concept_id)
    if node is None:
        return None
    names = []
    seen = set()
    while node is not None and node["id"] not in seen:
        seen.add(node["id"])
        names.append(node["name"])
        pid = node["parent_id"]
        node = by_id.get(pid) if pid is not None else None
    names.reverse()
    return CONCEPT_PATH_SEP.join(names)


def build_concept_path_by_id(concept_id, db_path=None):
    """
    按 id 返回概念的完整路径串（一级/二级/.../name）。
    概念名允许在不同分支下重复，故定位概念应优先用 id 而非 name。
    id 不存在时返回 None。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, name, parent_id FROM concepts")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return _path_of_id(rows, concept_id)


def find_sibling_concept(cur, name, parent_id, exclude_id=None):
    """查找同一上级下是否已存在同名概念，返回其 id 或 None。"""
    if parent_id is None:
        sql = "SELECT id FROM concepts WHERE name = ? AND parent_id IS NULL"
        args = [name]
    else:
        sql = "SELECT id FROM concepts WHERE name = ? AND parent_id = ?"
        args = [name, parent_id]
    if exclude_id is not None:
        sql += " AND id != ?"
        args.append(exclude_id)
    cur.execute(sql, args)
    row = cur.fetchone()
    return row["id"] if row else None


def resolve_primary_concept(name, db_path=None):
    """
    返回概念 name 所属的一级概念名。
    - name 本身是一级概念时返回自身
    - name 是子概念时返回其最顶层祖先（一级）的名称
    - name 不存在于概念表时返回 None
    用于：给子概念挂股票时，股票详情只记录对应的一级概念。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, name, level, parent_id FROM concepts")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    by_id = {r["id"]: r for r in rows}
    node = None
    for r in rows:
        if r["name"] == name:
            node = r
            break
    if node is None:
        return None

    seen = set()
    while node is not None and node["id"] not in seen:
        if (node["level"] or 1) == 1:
            return node["name"]
        seen.add(node["id"])
        pid = node["parent_id"]
        if pid is None:
            return node["name"]
        node = by_id.get(pid)
    return None


def list_concepts(db_path=None):
    """
    返回所有概念（按树形层级排序），每条含：
    {id, name, description, code, level, parent_id, usage, path}
    path 例如 "固态电池 / 材料 / 正极"。
    一级概念按新增时间倒序（最新在前），子级保持原有顺序。
    """
    ensure_concepts_table(db_path)   # 保证旧库也有 code 列
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, description, code, level, parent_id FROM concepts"
    )
    concepts = [dict(r) for r in cur.fetchall()]
    for c in concepts:
        c["code"] = c.get("code") or ""

    # 统计每个概念在股票中的使用次数
    # 股票挂在子级概念（完整路径）时，其路径上每一级都计一次
    usage = {}
    cur.execute(
        "SELECT concept FROM stocks "
        "WHERE concept IS NOT NULL AND concept != ''"
    )
    for row in cur.fetchall():
        for piece in _split_concept_segments(row["concept"]):
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
    # 统计每条概念在股票中的使用次数（子级路径会向上计入所属一级概念）
    usage = {}
    cur.execute(
        "SELECT concept FROM stocks "
        "WHERE concept IS NOT NULL AND concept != ''"
    )
    for row in cur.fetchall():
        for piece in _split_concept_segments(row["concept"]):
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
    返回新概念 id。同一上级下重名/越级抛 ValueError，父级不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if CONCEPT_PATH_SEP in (name or ""):
            # 名字里不允许带路径分隔符，否则会生成
            # "固态电池/材料/正极/富锂锰基" 这种整段路径的假概念；
            # 应逐级创建，或用 parent_id 挂到正确的父级下
            raise ValueError(
                f"概念名称不能包含「{CONCEPT_PATH_SEP}」，"
                "请逐级创建（先建一级，再在编辑页里添加子级）"
            )
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
        # 重名只在「同一上级下」禁止：不同分支可以各有一个「其他」
        if find_sibling_concept(cur, name, parent_id) is not None:
            cur.execute("SELECT id, name, parent_id FROM concepts")
            rows = [dict(r) for r in cur.fetchall()]
            where = _path_of_id(rows, parent_id) or "一级概念"
            raise ValueError(
                f"「{where}」下已存在同名概念「{name}」，请换一个名称"
            )
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
    code="__keep__",
    db_path=None,
):
    """
    编辑概念（名称/描述/概念编码/上级分类）。
    - parent_id 传 "__keep__" 表示保持原上级不变
    - parent_id 传 None/"" 表示移动到根（一级）
    - parent_id 传整数表示移动到该上级（自动重算 level）
    - code 传 "__keep__" 表示不改编码；传空串表示清空；否则写入新编码
    返回 (old_name, new_name)。
    重名/越级/成环时抛 ValueError，id 不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, description, code, level, parent_id FROM concepts WHERE id = ?",
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
        new_code = (
            row["code"] or "" if code == "__keep__" else (code or "").strip()
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

        # 改名/移动后不能与「同一上级下」的兄弟概念重名
        if find_sibling_concept(cur, new_name, new_parent, exclude_id=concept_id) is not None:
            cur.execute("SELECT id, name, parent_id FROM concepts")
            rows = [dict(r) for r in cur.fetchall()]
            where = _path_of_id(rows, new_parent) or "一级概念"
            raise ValueError(
                f"「{where}」下已存在同名概念「{new_name}」，请换一个名称"
            )
        cur.execute(
            "UPDATE concepts SET name = ?, description = ?, code = ?, "
            "level = ?, parent_id = ? "
            "WHERE id = ?",
            (new_name, new_desc, new_code, new_level, new_parent, concept_id),
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
        # 先清理这些概念下的业务描述关联
        _ensure_business_table(cur)
        cur.execute(
            f"DELETE FROM stock_concept_businesses WHERE concept_id IN ({placeholders})",
            all_ids,
        )
        # 清理「股票 × 概念」关联行
        _ensure_stock_concepts_table(cur)
        cur.execute(
            f"DELETE FROM stock_concepts WHERE concept_id IN ({placeholders})",
            all_ids,
        )
        cur.execute(f"DELETE FROM concepts WHERE id IN ({placeholders})", all_ids)
        conn.commit()
        return names
    finally:
        conn.close()


def sync_concept_in_stocks(old_name, new_name=None, db_path=None):
    """
    同步股票表中某概念：
    概念串中的一项可能是完整路径（如 "固态电池/材料/负极"），因此按「层级段」处理：
    - new_name 提供时：把路径中等于 old_name 的那一级替换为 new_name（重命名）
    - new_name 为 None 时：丢弃所有包含 old_name 的路径项（删除及级联）
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
            new_pieces = []
            touched = False
            for p in pieces:
                segs = [
                    s.strip() for s in p.split(CONCEPT_PATH_SEP) if s.strip()
                ]
                if old_name not in segs:
                    new_pieces.append(p)
                    continue
                touched = True
                if new_name is None:
                    continue  # 删除：整条路径一起去掉
                segs = [new_name if s == old_name else s for s in segs]
                new_pieces.append(CONCEPT_PATH_SEP.join(segs))
            if not touched:
                continue
            new_val = "-".join(new_pieces)
            if new_val != (row["concept"] or ""):
                cur.execute(
                    "UPDATE stocks SET concept = ? WHERE id = ?",
                    (new_val, row["id"]),
                )
                # 关联表跟着文本走：删概念会去掉关系，改名不影响 concept_id
                _sync_stock_concept_rows(cur, row["id"], new_val)
                changed += 1
        if new_name is None:
            # 概念被删除：清理所有指向它的关联行
            _ensure_stock_concepts_table(cur)
            cur.execute(
                "DELETE FROM stock_concepts WHERE concept_id IN "
                "(SELECT id FROM concepts WHERE name = ?)",
                (old_name,),
            )
        conn.commit()
        return changed
    finally:
        conn.close()


def collect_concept_paths(concept_id, db_path=None):
    """收集概念自身及其所有子孙的完整路径串列表。
    删除概念前调用，用于按路径精确清理股票概念串（概念名可能重名）。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, name, parent_id FROM concepts")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    ids = [concept_id] + _get_descendant_ids(concept_id, db_path)
    return [p for p in (_path_of_id(rows, i) for i in ids) if p]


def sync_concept_path_in_stocks(old_path, new_path=None, db_path=None):
    """
    按「完整路径」同步股票 concept 串中的概念（概念名可重名，按名字会误伤
    其它分支下的同名概念，故改名 / 删除概念应走路径而不是名字）。
    - new_path 提供时：把等于 old_path 或以 old_path/ 开头的项改成 new_path 开头（重命名/移动）
    - new_path 为 None 时：丢弃这些项（删除及级联）
    返回受影响的股票行数。
    """
    if not old_path:
        return 0
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, concept FROM stocks WHERE concept LIKE ?",
            (f"%{old_path}%",),
        )
        prefix = old_path + CONCEPT_PATH_SEP
        changed = 0
        for row in cur.fetchall():
            new_pieces = []
            touched = False
            for p in _split_concept_raw(row["concept"]):
                segs = [s.strip() for s in p.split(CONCEPT_PATH_SEP) if s.strip()]
                item = CONCEPT_PATH_SEP.join(segs)
                if item != old_path and not item.startswith(prefix):
                    new_pieces.append(p)
                    continue
                touched = True
                if new_path is None:
                    continue  # 删除：整条路径一起去掉
                new_pieces.append(new_path + item[len(old_path):])
            if not touched:
                continue
            new_val = "-".join(new_pieces)
            if new_val != (row["concept"] or ""):
                cur.execute(
                    "UPDATE stocks SET concept = ? WHERE id = ?",
                    (new_val, row["id"]),
                )
                # 关联表跟着文本走：删概念会去掉关系，改名不影响 concept_id
                _sync_stock_concept_rows(cur, row["id"], new_val)
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
            for piece in _split_concept_segments(row["concept"]):
                seen.add(piece)

        # 导入的都是一级概念，只需与已有「一级概念」判重（概念名可跨分支重复）
        cur.execute("SELECT name FROM concepts WHERE parent_id IS NULL")
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
    stock_rows = cur.fetchall()  # 先取完股票结果，避免后续查询覆盖游标
    # 业务描述按「匹配路径的末级概念」关联，一次性取出备用
    _ensure_business_table(cur)
    biz_rows = cur.execute(
        """
        SELECT s.name AS stock_name, c.name AS concept_name, b.business
        FROM stock_concept_businesses b
        JOIN concepts c ON c.id = b.concept_id
        JOIN stocks s ON s.id = b.stock_id
        """
    ).fetchall()
    biz_map: Dict[tuple, str] = {}
    for br in biz_rows:
        biz_map[(br["stock_name"], br["concept_name"])] = br["business"]
    for row in stock_rows:
        # 路径上任一级等于该概念即命中（挂在子级的股票也算属于父级概念）
        if concept_name in _split_concept_segments(row["concept"]):
            # 命中那条路径的末级概念 = 业务描述的挂载点
            leaf = ""
            for item in _split_concept_raw(row["concept"]):
                segs = [
                    s.strip() for s in item.split(CONCEPT_PATH_SEP) if s.strip()
                ]
                if concept_name in segs:
                    leaf = segs[-1]
                    break
            result.append({
                "name": row["name"],
                "code": row["code"] or "",
                "concept": row["concept"],
                "primary": row["primary_name"],
                "secondary": row["secondary_name"],
                "tertiary": row["tertiary_name"],
                "business": biz_map.get((row["name"], leaf), ""),
            })
    conn.close()
    return result


# ============== 股票 × 概念 业务描述 ==============
# 设计：独立关联表，一条记录 = 「某只股票在某个概念下的一条业务描述」。
# 同一概念下不同股票各写各的（如「锰矿」下湘潭电化、红星发展描述不同），
# 同一股票在不同概念下也可以有不同的描述。
# 用 id 关联（而非概念名文本），概念改名后描述自动跟随，不受路径格式影响。
#
# CREATE TABLE IF NOT EXISTS stock_concept_businesses (
#     id         INTEGER PRIMARY KEY AUTOINCREMENT,
#     stock_id   INTEGER NOT NULL,   -- -> stocks.id
#     concept_id INTEGER NOT NULL,   -- -> concepts.id
#     business   TEXT NOT NULL DEFAULT '',
#     updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
#     UNIQUE(stock_id, concept_id)
# )


def _ensure_business_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_concept_businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            concept_id INTEGER NOT NULL,
            business TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(stock_id, concept_id)
        )
        """
    )


def ensure_stock_concept_businesses_table():
    """确保 stock_concept_businesses 表存在（应用启动时调用）。"""
    conn = get_conn()
    cur = conn.cursor()
    _ensure_business_table(cur)
    conn.commit()
    conn.close()


# ===== 股票 × 概念 关联表：记录「加入时间」，用于按添加时间排序 =====
# stocks.concept 只是一段文本（"固态电池/材料/正极-机器人"），没有时间信息，
# 因此单独建一张关联表记录每只股票加入每个概念的时间。
#
# CREATE TABLE IF NOT EXISTS stock_concepts (
#     id         INTEGER PRIMARY KEY AUTOINCREMENT,
#     stock_id   INTEGER NOT NULL,
#     concept_id INTEGER NOT NULL,
#     created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
#     UNIQUE(stock_id, concept_id)
# )


def _ensure_stock_concepts_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            concept_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(stock_id, concept_id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_concepts_concept "
        "ON stock_concepts(concept_id, created_at, id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_concepts_stock "
        "ON stock_concepts(stock_id)"
    )


def ensure_stock_concepts_table(db_path=None):
    """确保 stock_concepts 表存在（应用启动时调用）。

    首次创建时用 stocks.concept 现有文本全量回填一次；回填按 stocks.id
    升序写入，保证老数据的展示顺序与改造前一致。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='stock_concepts'"
        )
        exists = cur.fetchone() is not None
        _ensure_stock_concepts_table(cur)
        conn.commit()
    finally:
        conn.close()
    if not exists:
        backfill_stock_concepts(db_path)


def _leaf_concept_ids(cur, concept_text):
    """把概念文本（可含多条路径）解析成「末级概念 id」集合。

    "固态电池/材料/正极" 归到末级「正极」；未登记在 concepts 表里的名字忽略。
    """
    ids = set()
    for item in _split_concept_raw(concept_text):
        segs = [s.strip() for s in item.split(CONCEPT_PATH_SEP) if s.strip()]
        if not segs:
            continue
        cur.execute("SELECT id FROM concepts WHERE name = ?", (segs[-1],))
        r = cur.fetchone()
        if r:
            ids.add(r["id"])
    return ids


def _sync_stock_concept_rows(cur, stock_id, concept_text):
    """按 stocks.concept 文本，把某只股票的关联行对齐。

    新关系插入（记当前时间），文本里已无的关系删除，已有关系保留原时间。
    """
    if not stock_id:
        return
    _ensure_stock_concepts_table(cur)
    want = _leaf_concept_ids(cur, concept_text)
    cur.execute(
        "SELECT concept_id FROM stock_concepts WHERE stock_id = ?", (stock_id,)
    )
    have = {r["concept_id"] for r in cur.fetchall()}
    for cid in sorted(have - want):
        cur.execute(
            "DELETE FROM stock_concepts WHERE stock_id = ? AND concept_id = ?",
            (stock_id, cid),
        )
    now = _now_datetime_str()
    for cid in sorted(want - have):
        cur.execute(
            "INSERT OR IGNORE INTO stock_concepts "
            "(stock_id, concept_id, created_at) VALUES (?, ?, ?)",
            (stock_id, cid, now),
        )


def backfill_stock_concepts(db_path=None):
    """全量回填 stock_concepts（按 stocks.id 升序）。返回写入行数。"""
    conn = get_conn(db_path)
    cur = conn.cursor()
    written = 0
    try:
        _ensure_stock_concepts_table(cur)
        cur.execute("SELECT id, name FROM concepts")
        name_to_id = {r["name"]: r["id"] for r in cur.fetchall()}
        now = _now_datetime_str()
        cur.execute("SELECT id, concept FROM stocks ORDER BY id")
        for r in cur.fetchall():
            for item in _split_concept_raw(r["concept"]):
                segs = [s.strip() for s in item.split(CONCEPT_PATH_SEP) if s.strip()]
                if not segs:
                    continue
                cid = name_to_id.get(segs[-1])
                if cid is None:
                    continue
                cur.execute(
                    "INSERT OR IGNORE INTO stock_concepts "
                    "(stock_id, concept_id, created_at) VALUES (?, ?, ?)",
                    (r["id"], cid, now),
                )
                written += cur.rowcount or 0
        conn.commit()
        return written
    finally:
        conn.close()


def rebuild_stock_concepts(db_path=None):
    """清空并按 stocks.concept 重建全部关联行（CSV 重建库后使用）。"""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        _ensure_stock_concepts_table(cur)
        cur.execute("DELETE FROM stock_concepts")
        conn.commit()
    finally:
        conn.close()
    return backfill_stock_concepts(db_path)


def set_stock_concept_business(stock_name, concept_name, business, db_path=None):
    """
    设置「股票 × 概念」的业务描述（upsert）。
    - business 为空串时删除该条记录
    - 股票或概念不存在抛 LookupError
    返回最终生效的 business 文本。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        _ensure_business_table(cur)
        cur.execute("SELECT id FROM stocks WHERE name = ?", (stock_name,))
        srow = cur.fetchone()
        if srow is None:
            raise LookupError(f"未找到股票：{stock_name}")
        cur.execute("SELECT id FROM concepts WHERE name = ?", (concept_name,))
        crow = cur.fetchone()
        if crow is None:
            raise LookupError(f"未找到概念：{concept_name}")
        text = (business or "").strip()
        if text:
            cur.execute(
                """
                INSERT INTO stock_concept_businesses (stock_id, concept_id, business)
                VALUES (?, ?, ?)
                ON CONFLICT(stock_id, concept_id)
                DO UPDATE SET business = excluded.business,
                              updated_at = datetime('now','localtime')
                """,
                (srow["id"], crow["id"], text),
            )
        else:
            cur.execute(
                "DELETE FROM stock_concept_businesses "
                "WHERE stock_id = ? AND concept_id = ?",
                (srow["id"], crow["id"]),
            )
        conn.commit()
        return text
    finally:
        conn.close()


def get_businesses_by_concept_names(concept_names, db_path=None):
    """
    批量查询多个概念下各股票的业务描述。
    返回 {概念名: {股票名: 业务描述}}（只含有描述的条目）。
    """
    names = [str(n).strip() for n in (concept_names or []) if str(n).strip()]
    if not names:
        return {}
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        _ensure_business_table(cur)
        placeholders = ",".join("?" * len(names))
        cur.execute(
            """
            SELECT c.name AS concept_name, s.name AS stock_name, b.business
            FROM stock_concept_businesses b
            JOIN concepts c ON c.id = b.concept_id
            JOIN stocks s ON s.id = b.stock_id
            WHERE c.name IN (%s)
            """
            % placeholders,
            names,
        )
        result: Dict[str, Dict[str, str]] = {}
        for row in cur.fetchall():
            result.setdefault(row["concept_name"], {})[row["stock_name"]] = (
                row["business"]
            )
        return result
    finally:
        conn.close()


def get_stock_businesses(stock_name, db_path=None):
    """
    查询某只股票在所有概念下的业务描述。
    返回 {概念名: 业务描述}。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        _ensure_business_table(cur)
        cur.execute("SELECT id FROM stocks WHERE name = ?", (stock_name,))
        srow = cur.fetchone()
        if srow is None:
            return {}
        cur.execute(
            """
            SELECT c.name AS concept_name, b.business
            FROM stock_concept_businesses b
            JOIN concepts c ON c.id = b.concept_id
            WHERE b.stock_id = ?
            ORDER BY c.level, c.name
            """,
            (srow["id"],),
        )
        return {row["concept_name"]: row["business"] for row in cur.fetchall()}
    finally:
        conn.close()


def get_businesses_for_stock_ids(stock_ids, db_path=None):
    """
    批量查询多只股票的业务描述（页面 F 搜索批量用）。
    返回 {stock_id: [{"concept": 概念名, "path": 完整路径, "business": 描述}]}。
    path 形如 "固态电池/材料/负极/硅基负极/硅氧负极"，供前端按一级概念归属展示。
    """
    ids = [int(i) for i in (stock_ids or []) if i is not None]
    if not ids:
        return {}
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        _ensure_business_table(cur)
        # 概念 id -> 完整路径
        path_by_id: Dict[int, str] = {}
        crows = cur.execute(
            "SELECT id, name, parent_id FROM concepts"
        ).fetchall()
        by_id = {r["id"]: r for r in crows}
        for r in crows:
            names = []
            node = r
            seen = set()
            while node is not None and node["id"] not in seen:
                seen.add(node["id"])
                names.append(node["name"])
                pid = node["parent_id"]
                node = by_id.get(pid) if pid is not None else None
            path_by_id[r["id"]] = CONCEPT_PATH_SEP.join(reversed(names))

        placeholders = ",".join("?" * len(ids))
        cur.execute(
            """
            SELECT b.stock_id, b.concept_id, c.name AS concept_name, b.business
            FROM stock_concept_businesses b
            JOIN concepts c ON c.id = b.concept_id
            WHERE b.stock_id IN (%s)
            ORDER BY c.level, c.name
            """
            % placeholders,
            ids,
        )
        result: Dict[int, list] = {}
        for row in cur.fetchall():
            result.setdefault(row["stock_id"], []).append({
                "concept": row["concept_name"],
                "path": path_by_id.get(row["concept_id"], row["concept_name"]),
                "business": row["business"],
            })
        return result
    finally:
        conn.close()


def remove_concept_from_stock(stock_name, concept_name, db_path=None, concept_id=None):
    """
    从指定股票的概念串中移除某概念（其余概念保留，'-' 重组）。
    概念串中的一项可能是完整路径（"一级/二级/..."），若任一级等于
    concept_name，则丢弃该整条路径。
    concept_id 提供时按「完整路径」精确匹配（概念名可重名，按名字会误删
    其它分支下的同名概念），只丢弃该分支及其子孙路径。
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

        # 按 id 定位：拿到该概念的完整路径，连子孙路径一起移除
        target_path = None
        removed_ids = set()
        if concept_id is not None:
            target_path = build_concept_path_by_id(concept_id, db_path)
            if target_path:
                removed_ids = {concept_id} | set(_get_descendant_ids(concept_id, db_path))

        kept = []
        removed = False
        removed_segs = set()
        prefix = (target_path + CONCEPT_PATH_SEP) if target_path else None
        for item in _split_concept_raw(row["concept"]):
            segs = [s.strip() for s in item.split(CONCEPT_PATH_SEP) if s.strip()]
            if target_path is not None:
                item_norm = CONCEPT_PATH_SEP.join(segs)
                hit = item_norm == target_path or (
                    prefix is not None and item_norm.startswith(prefix)
                )
            else:
                hit = concept_name in segs
            if hit:
                removed = True
                removed_segs.update(segs)
                continue
            kept.append(item)
        if not removed:
            return row["concept"] or "", False
        new_val = "-".join(kept)
        cur.execute(
            "UPDATE stocks SET concept = ? WHERE id = ?",
            (new_val, row["id"]),
        )
        # 同步「股票 × 概念」关联表（被移除的概念关系一并删掉）
        _sync_stock_concept_rows(cur, row["id"], new_val)
        # 同步清理这些概念下的业务描述
        _ensure_business_table(cur)
        if removed_ids:
            # 按 id 精确清理（含子孙概念）
            placeholders = ",".join("?" * len(removed_ids))
            cur.execute(
                f"""
                DELETE FROM stock_concept_businesses
                WHERE stock_id = ? AND concept_id IN ({placeholders})
                """,
                [row["id"], *removed_ids],
            )
        elif removed_segs:
            placeholders = ",".join("?" * len(removed_segs))
            cur.execute(
                f"""
                DELETE FROM stock_concept_businesses
                WHERE stock_id = ? AND concept_id IN (
                    SELECT id FROM concepts WHERE name IN ({placeholders})
                )
                """,
                [row["id"], *removed_segs],
            )
        # 概念被移除也算修改（保持可见的"修改历史"语义）
        _record_modification_in_conn(cur, row["id"])
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
    追加一张图片到指定股票。
    返回更新后的文件名列表；股票不存在抛 LookupError。
    图片操作也视为对该股票的修改，自动写入修改日志。
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
        _record_modification_in_conn(conn, stock_id)
        images = _list_images_conn(conn, stock_id)
        conn.commit()
        return images
    finally:
        conn.close()


def delete_stock_image(tertiary_id, stock_name, filename="", db_path=None):
    """
    删除该股票的一张图片（filename 为空则删除全部）。
    返回删除后剩余的文件名列表；股票不存在抛 LookupError。
    图片操作也视为对该股票的修改，自动写入修改日志。
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
        _record_modification_in_conn(conn, stock_id)
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
    [{"name", "code", "primary", "secondary", "tertiary", "option_b", "option_d"}]
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.name, s.code, s.option_b, s.option_d,
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
            "option_b": r["option_b"] or "",
            "option_d": r["option_d"] or "",
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return result


def get_st_stocks(db_path=None):
    """返回所有名称中带「ST」标记的股票（ST、*ST、S*ST、SST 等）。

    供页面 O 使用，按行业层级 + 名称排序。
    [{"name", "primary", "secondary", "tertiary"}]
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.name,
               p.name AS primary_name,
               sc.name AS secondary_name,
               t.name AS tertiary_name
        FROM stocks s
        JOIN industries t ON s.tertiary_id = t.id
        JOIN industries sc ON t.parent_id = sc.id
        JOIN industries p ON sc.parent_id = p.id
        WHERE UPPER(s.name) LIKE '%ST%'
        ORDER BY p.name, sc.name, t.name, s.name
        """
    )
    result = [
        {
            "name": r["name"],
            "primary": r["primary_name"],
            "secondary": r["secondary_name"],
            "tertiary": r["tertiary_name"],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return result


def build_concept_edit_table(concept_id, db_path=None):
    """
    构造「页面 H 风格」的概念层级大表，用于页面 K 的一级概念编辑页。

    以 concept_id 为根，展开其所有子孙概念，按层级列（1 级 / 2 级 / ... / N 级）+ 股票 + 业务描述
    的形式扁平化，并对概念层级单元格计算 rowspan（与页面 H 的展示口径一致）。

    返回 {
        "root": {"id", "name", "path", "level"},
        "max_depth": 层级列数量（>=1）,
        "rows": [
            {
                "cells": [None | {"id", "name", "level", "span"}],   # 长度 = max_depth
                "stock": {"name", "code", "primary", "secondary", "tertiary",
                          "concept_id", "concept_name", "concept_path", "business"},
            }
        ],
        "stock_count": 去重后的股票数,
        "concept_count": 子树概念数（含根）,
    }
    概念不存在抛 LookupError。
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, level, parent_id FROM concepts")
        all_concepts = cur.fetchall()

        by_id = {r["id"]: r for r in all_concepts}
        root = by_id.get(concept_id)
        if root is None:
            raise LookupError(f"未找到概念：{concept_id}")

        # 同级概念按「添加顺序」排列（id 升序），而不是名称
        children: Dict[int, list] = {}
        for r in all_concepts:
            pid = r["parent_id"]
            if pid is not None:
                children.setdefault(pid, []).append(r["id"])
        for lst in children.values():
            lst.sort()

        # 根概念的完整路径（一级概念/二级概念/...）
        root_segs = []
        node = root
        seen = set()
        while node is not None and node["id"] not in seen:
            seen.add(node["id"])
            root_segs.append(node["name"])
            pid = node["parent_id"]
            node = by_id.get(pid) if pid is not None else None
        root_segs.reverse()
        root_path = CONCEPT_PATH_SEP.join(root_segs)

        # 子树：{id: {"id","name","level","children":[...ids],"path":...}}
        subtree: Dict[int, dict] = {}
        stack = [(root["id"], root_path)]
        while stack:
            cid_, path_ = stack.pop()
            node = by_id[cid_]
            subtree[cid_] = {
                "id": cid_,
                "name": node["name"],
                "level": node["level"],
                "path": path_,
                "children": list(children.get(cid_, [])),
            }
            for ch in children.get(cid_, []):
                stack.append((ch, path_ + CONCEPT_PATH_SEP + by_id[ch]["name"]))

        # 每个概念的「自身 + 全部子孙」id，用于查找股票的关联时间
        desc_map: Dict[int, list] = {}

        def _collect_desc(cid_):
            out = []
            for ch in subtree.get(cid_, {}).get("children", []):
                out.append(ch)
                out.extend(_collect_desc(ch))
            return out

        for cid_ in list(subtree.keys()):
            desc_map[cid_] = _collect_desc(cid_)

        # 股票加入各概念的时间：{(stock_id, concept_id): (created_at, 行id)}
        _ensure_stock_concepts_table(cur)
        cur.execute(
            "SELECT stock_id, concept_id, created_at, id FROM stock_concepts"
        )
        join_time = {}
        for r in cur.fetchall():
            k = (r["stock_id"], r["concept_id"])
            v = (r["created_at"] or "", r["id"] or 0)
            if k not in join_time or v < join_time[k]:
                join_time[k] = v

        # 业务描述：{(股票名, 概念 id): 描述}
        # 概念名可重复（不同分支），故按 id 关联，避免同名概念的业务描述互相串
        cur.execute(
            """
            SELECT s.name AS stock_name, b.concept_id, b.business
            FROM stock_concept_businesses b
            JOIN stocks s ON s.id = b.stock_id
            """
        )
        biz_map = {}
        for r in cur.fetchall():
            biz_map[(r["stock_name"], r["concept_id"])] = r["business"]

        # 股票 → 子树上的挂载点（叶概念）
        # 按 stocks.id 升序取，保证同一概念内的股票按「添加顺序」排列
        cur.execute(
            """
            SELECT s.id AS stock_id, s.name, s.code, s.concept,
                   p.name AS primary_name, sc.name AS secondary_name, t.name AS tertiary_name
            FROM stocks s
            JOIN industries t ON s.tertiary_id = t.id
            JOIN industries sc ON t.parent_id = sc.id
            JOIN industries p ON sc.parent_id = p.id
            ORDER BY s.id
            """
        )
        # {concept_id: [stock dict]}
        mounted: Dict[int, list] = {}
        for r in cur.fetchall():
            for item in _split_concept_raw(r["concept"]):
                segs = [s.strip() for s in item.split(CONCEPT_PATH_SEP) if s.strip()]
                if not segs or segs[0] != root["name"]:
                    continue
                # 沿子树逐层下钻（找不到对应子概念时挂在当前最深的已匹配节点）
                cur_id = root["id"]
                for seg in segs[1:]:
                    nxt = None
                    for ch in subtree.get(cur_id, {}).get("children", []):
                        if subtree[ch]["name"] == seg:
                            nxt = ch
                            break
                    if nxt is None:
                        break
                    cur_id = nxt
                node = subtree[cur_id]
                stock = {
                    "id": r["stock_id"],
                    "name": r["name"],
                    "code": r["code"] or "",
                    "primary": r["primary_name"],
                    "secondary": r["secondary_name"],
                    "tertiary": r["tertiary_name"],
                    "concept_id": node["id"],
                    "concept_name": node["name"],
                    "concept_path": node["path"],
                    "business": biz_map.get((r["name"], node["id"]), ""),
                }
                bucket = mounted.setdefault(cur_id, [])
                if not any(x["name"] == stock["name"] for x in bucket):
                    bucket.append(stock)

        # 扁平化（DFS，概念名排序），与页面 H 口径一致。
        # 编辑页口径：
        #   1) 一个概念节点只占一行，该概念下的多只股票在同一行的「股票」列里并列展示；
        #   2) 暂时没有股票的末端概念也占一行（stocks=[]），否则它不会出现在表里，
        #      也就无法继续给它加下级 / 加股票。
        flat_rows = []

        def _node_info(cid_, chain_names):
            return {
                "id": cid_,
                "name": subtree[cid_]["name"],
                "path": CONCEPT_PATH_SEP.join(chain_names),
                "level": subtree[cid_]["level"],
            }

        def _dfs(cid_, chain_names, chain_ids):
            node = subtree[cid_]

            def _order_key(st):
                # 优先用「加入该概念的时间」排序（早的靠前）；
                # 自身没记录时看子孙概念的记录；都没有则兜底按 stocks.id
                best = None
                for cid2 in [cid_] + desc_map.get(cid_, []):
                    v = join_time.get((st["id"], cid2))
                    if v is not None and (best is None or v < best):
                        best = v
                if best is None:
                    return (1, "", st["id"] or 0)
                return (0, best[0], best[1])

            stocks = sorted(mounted.get(cid_, []), key=_order_key)
            if stocks or not node["children"]:
                flat_rows.append((
                    chain_ids[:],
                    chain_names[:],
                    stocks,
                    _node_info(cid_, chain_names),
                ))
            for ch in node["children"]:
                _dfs(ch, chain_names[:] + [subtree[ch]["name"]], chain_ids[:] + [ch])

        _dfs(root["id"], [root["name"]], [root["id"]])

        max_depth = max([len(names) for _, names, _, _ in flat_rows] or [1])

        # 计算 rowspan：相邻行前缀链（前 d+1 级）相同则合并。
        # 语义：
        #   None            → 该列已被上方已渲染单元格的 rowspan 覆盖，不输出 <td>
        #   {"empty": True} → 该行概念路径不足此层级，输出空 <td> 占位（保证列对齐）
        #   其它            → 输出概念单元格，rowspan = span
        # 用 active[d]（第 d 列剩余被覆盖的行数）严格推进，避免股票挂在不同深度时列错位。
        table_rows = []
        active = [0] * max_depth
        for idx, (ids, names, stocks, cpt) in enumerate(flat_rows):
            cells = []
            for d in range(max_depth):
                if active[d] > 0:
                    active[d] -= 1
                    cells.append(None)
                    continue
                if d >= len(names):
                    cells.append({
                        "id": None,
                        "name": "",
                        "level": root["level"] + d,
                        "path": CONCEPT_PATH_SEP.join(names),
                        "span": 1,
                        "empty": True,
                    })
                    continue
                prefix = names[: d + 1]
                span = 1
                j = idx + 1
                while j < len(flat_rows) and flat_rows[j][1][: d + 1] == prefix:
                    span += 1
                    j += 1
                cells.append({
                    "id": ids[d],
                    "name": names[d],
                    "level": root["level"] + d,
                    "path": CONCEPT_PATH_SEP.join(names[:d + 1]),
                    "span": span,
                    "empty": False,
                })
                active[d] = span - 1
            table_rows.append({"cells": cells, "stocks": stocks, "concept": cpt})

        stock_names = set()
        for _, _, stocks, _ in flat_rows:
            for st in stocks:
                stock_names.add(st["name"])

        return {
            "root": {
                "id": root["id"],
                "name": root["name"],
                "path": root_path,
                "level": root["level"],
            },
            "max_depth": max_depth,
            "rows": table_rows,
            "stock_count": len(stock_names),
            "concept_count": len(subtree),
        }
    finally:
        conn.close()


# ==================== 周期图（页面 R）====================
# 数据表设计：
#   zq_days  —— 交易日维度表（一列 = 一个交易日）
#       date_label 显示日期（如 7/13）；week_no 第几周；week_day 周几(1~5)；sort_order 列顺序
#   zq_rows  —— 行维度表（一行 = 一个高度层级或备注行）
#       section 分区（main 主图 / sub 副图）；height_label 行首高度标签（如 10、7-100%、''）；
#       kind 行类型（'' 高度行 / 'note' 备注行）；row_order 行顺序
#   zq_cells —— 格子表（一格 = 某行某天的内容）
#       (row_id, day_id) 唯一；content 格内文本；style 样式（'red' 主升红块 / '' 普通）

ZQ_CSV_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data", "zhouqi", "早盘竞价记录2.csv",
)

ZQ_SCHEMA = """
CREATE TABLE IF NOT EXISTS zq_days (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date_label TEXT NOT NULL,
    week_no    INTEGER NOT NULL,
    week_day   INTEGER NOT NULL,
    sort_order INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zq_days_order ON zq_days(sort_order);
CREATE TABLE IF NOT EXISTS zq_rows (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    section      TEXT NOT NULL DEFAULT 'main',
    height_label TEXT DEFAULT '',
    kind         TEXT DEFAULT '',
    row_order    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zq_rows_order ON zq_rows(section, row_order);
CREATE TABLE IF NOT EXISTS zq_cells (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id  INTEGER NOT NULL REFERENCES zq_rows(id) ON DELETE CASCADE,
    day_id  INTEGER NOT NULL REFERENCES zq_days(id) ON DELETE CASCADE,
    content TEXT DEFAULT '',
    style   TEXT DEFAULT '',
    UNIQUE(row_id, day_id)
);
"""

# 备注关键词：命中则不算「主升红块」
_ZQ_NOTE_PAT = re.compile(
    r"(情绪|退潮|回暖|冰点|指数|监控|异动|最高|停牌|涨停\d*|跌停\d*|PK|ICU|KTV|第\d+天|，|,)"
)


def _zq_is_stock_text(text):
    """判断格子文本是否属于「股票链条」内容（渲染为红块）。"""
    t = (text or "").strip()
    if not t or len(t) > 12:
        return False
    if _ZQ_NOTE_PAT.search(t):
        return False
    # 名字+数字：新华传媒11 / 立新能源2 / 凡拓数创1
    if re.search(r"[\u4e00-\u9fa5A-Za-z]-?\d{1,3}$", t):
        return True
    # 名字-状态：新华-跌停 / 爱丽-断 / 爱丽-8%
    if re.search(r"[\u4e00-\u9fa5A-Za-z]+-(?:断|新高|止跌|反包|被关|开板|弱转强|跌停|\d+%?)$", t):
        return True
    # 名字+状态：新华断 / 爱丽反包 / 爱丽被关 / 新华止跌
    if t.endswith(("断", "新高", "止跌", "反包", "被关", "开板", "弱转强", "跌停")):
        return True
    return False


def ensure_zq_tables(db_path=None):
    conn = get_conn(db_path)
    try:
        conn.executescript(ZQ_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _zq_read_csv_rows(csv_path):
    """读 CSV，自动尝试 utf-8 / gbk 编码，返回二维列表。"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(csv_path, "r", encoding=enc, newline="") as f:
                return list(csv.reader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别 CSV 编码：{csv_path}")


def import_zq_csv(csv_path=None, db_path=None):
    """
    从「早盘竞价记录」CSV 导入周期图数据（全量重建）。
    CSV 结构：
        第 1 行：高度\\n日期 + 周标签（周1~周5 循环）
        第 2 行：对应日期（7/13 ...）
        之后每行：行首高度标签（10/9/.../1/''）+ 各交易日格子内容
    """
    csv_path = csv_path or ZQ_CSV_FILE
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"周期图 CSV 不存在：{csv_path}")
    raw = _zq_read_csv_rows(csv_path)
    if len(raw) < 3:
        raise ValueError("CSV 内容为空或格式不正确")

    ensure_zq_tables(db_path)
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM zq_cells")
        cur.execute("DELETE FROM zq_rows")
        cur.execute("DELETE FROM zq_days")

        # ---- 1. 解析列（交易日）----
        week_labels = raw[0]
        date_labels = raw[1]
        days = []            # [{sort_order, date_label, week_no, week_day}]
        day_cols = []        # 有日期的 CSV 列索引（与 days 一一对应）
        week_no = 0
        prev_week = None
        for col in range(1, max(len(week_labels), len(date_labels))):
            week = (week_labels[col] if col < len(week_labels) else "").strip()
            date = (date_labels[col] if col < len(date_labels) else "").strip()
            if not date:
                continue
            if week and week != prev_week:
                week_no += 1
                prev_week = week
            m = re.search(r"\d+", week)
            week_day = int(m.group()) if m else 0
            days.append({
                "sort_order": len(days) + 1,
                "date_label": date,
                "week_no": week_no,
                "week_day": week_day,
            })
            day_cols.append(col)

        day_at_col = {}      # CSV 列索引 -> zq_days.id
        for col, d in zip(day_cols, days):
            cur.execute(
                "INSERT INTO zq_days (id, date_label, week_no, week_day, sort_order) "
                "VALUES (NULL, ?, ?, ?, ?)",
                (d["date_label"], d["week_no"], d["week_day"], d["sort_order"]),
            )
            day_at_col[col] = cur.lastrowid

        # ---- 2. 解析数据行 ----
        section = "main"
        seen_height_one = False
        row_order = 0
        for row in raw[2:]:
            col0 = (row[0] if row else "").strip()
            cells = []
            has_content = False
            for col, day_order in day_at_col.items():
                text = (row[col] if col < len(row) else "").strip()
                if text:
                    has_content = True
                cells.append((day_order, text))

            if not has_content:
                continue  # 整行为空，跳过

            # 分区切换：主图走完（出现过高度 1）后再次出现高度 4 → 进入副图
            if section == "main" and seen_height_one and col0 == "4":
                section = "sub"

            height_label = col0
            kind = "" if height_label else "note"
            if height_label == "1":
                seen_height_one = True

            row_order += 1
            cur.execute(
                "INSERT INTO zq_rows (id, section, height_label, kind, row_order) "
                "VALUES (NULL, ?, ?, ?, ?)",
                (section, height_label, kind, row_order),
            )
            row_id = cur.lastrowid
            for day_order, text in cells:
                if not text:
                    continue
                style = "red" if (kind == "" and _zq_is_stock_text(text)) else ""
                cur.execute(
                    "INSERT INTO zq_cells (row_id, day_id, content, style) "
                    "VALUES (?, ?, ?, ?)",
                    (row_id, day_order, text, style),
                )
        conn.commit()
        return {"days": len(days), "rows": row_order}
    finally:
        conn.close()


def zq_has_data(db_path=None):
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM zq_days")
        return cur.fetchone()["c"] > 0
    finally:
        conn.close()


def load_zq_board(db_path=None):
    """
    读取周期图全部数据，供页面 R 渲染。
    返回 {
        "days":   [{"sortOrder", "date", "weekNo", "weekDay"}],
        "sections": [{"name", "rows": [{"id", "height", "kind",
                     "cells": [None | {"content", "style"} * len(days)]}]}],
    }
    """
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, date_label, week_no, week_day, sort_order "
            "FROM zq_days ORDER BY sort_order"
        )
        days = [
            {
                "id": r["id"],
                "sortOrder": r["sort_order"],
                "date": r["date_label"],
                "weekNo": r["week_no"],
                "weekDay": r["week_day"],
            }
            for r in cur.fetchall()
        ]
        day_ids = [r["id"] for r in cur.execute(
            "SELECT id FROM zq_days ORDER BY sort_order"
        ).fetchall()]
        pos_of = {day_id: i for i, day_id in enumerate(day_ids)}

        cur.execute(
            "SELECT id, section, height_label, kind, row_order "
            "FROM zq_rows ORDER BY row_order"
        )
        rows = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT row_id, day_id, content, style FROM zq_cells")
        cell_map = {}
        for r in cur.fetchall():
            cell_map.setdefault(r["row_id"], {})[r["day_id"]] = {
                "content": r["content"] or "",
                "style": r["style"] or "",
            }

        sections = []
        for row in rows:
            if not sections or sections[-1]["name"] != row["section"]:
                sections.append({"name": row["section"], "rows": []})
            cells = []
            grid = cell_map.get(row["id"], {})
            for day_id in day_ids:
                cells.append(grid.get(day_id))
            sections[-1]["rows"].append({
                "id": row["id"],
                "height": row["height_label"] or "",
                "kind": row["kind"] or "",
                "cells": cells,
            })
        return {"days": days, "sections": sections}
    finally:
        conn.close()


def _zq_parse_date_label(date_label, today=None):
    """把 "10/29" 解析为最接近今天的 datetime.date（自动处理跨年）。"""
    m = re.match(r"(\d{1,2})/(\d{1,2})$", (date_label or "").strip())
    if not m:
        return None
    today = today or datetime.date.today()
    month, day = int(m.group(1)), int(m.group(2))
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(datetime.date(year, month, day))
        except ValueError:
            continue
    return min(candidates, key=lambda d: abs((d - today).days))


def add_zq_day(db_path=None):
    """
    在最后新增一个交易日（跳过周末），返回新交易日 {"id","date","weekNo","weekDay","sortOrder"}。
    """
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, date_label, week_no, sort_order FROM zq_days "
            "ORDER BY sort_order DESC LIMIT 1"
        )
        last = cur.fetchone()
        if last is None:
            raise ValueError("暂无交易日数据，请先导入 CSV")

        last_date = _zq_parse_date_label(last["date_label"])
        if last_date is None:
            raise ValueError(f"无法解析最后交易日：{last['date_label']}")
        nxt = last_date
        while True:
            nxt += datetime.timedelta(days=1)
            if nxt.weekday() < 5:  # 跳过周末
                break

        week_no = last["week_no"]
        if nxt.isocalendar()[1] != last_date.isocalendar()[1]:
            week_no += 1  # 跨周
        cur.execute(
            "INSERT INTO zq_days (id, date_label, week_no, week_day, sort_order) "
            "VALUES (NULL, ?, ?, ?, ?)",
            (f"{nxt.month}/{nxt.day}", week_no, nxt.isoweekday(), last["sort_order"] + 1),
        )
        new_id = cur.lastrowid
        conn.commit()
        return {
            "id": new_id,
            "date": f"{nxt.month}/{nxt.day}",
            "weekNo": week_no,
            "weekDay": nxt.isoweekday(),
            "sortOrder": last["sort_order"] + 1,
        }
    finally:
        conn.close()


def set_zq_cell(row_id, day_id, content, db_path=None):
    """
    设置 / 清除某个格子内容（content 为空则删除该格）。
    style 自动按内容判断（备注行始终为普通样式）。
    """
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT kind FROM zq_rows WHERE id = ?", (row_id,))
        zrow = cur.fetchone()
        if zrow is None:
            raise LookupError(f"未找到行：{row_id}")
        cur.execute("SELECT id FROM zq_days WHERE id = ?", (day_id,))
        if cur.fetchone() is None:
            raise LookupError(f"未找到交易日：{day_id}")

        content = (content or "").strip()
        if not content:
            cur.execute(
                "DELETE FROM zq_cells WHERE row_id = ? AND day_id = ?",
                (row_id, day_id),
            )
            conn.commit()
            return {"content": "", "style": ""}

        style = "red" if (zrow["kind"] != "note" and _zq_is_stock_text(content)) else ""
        cur.execute(
            """
            INSERT INTO zq_cells (row_id, day_id, content, style)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(row_id, day_id)
            DO UPDATE SET content = excluded.content, style = excluded.style
            """,
            (row_id, day_id, content, style),
        )
        conn.commit()
        return {"content": content, "style": style}
    finally:
        conn.close()


def set_zq_cells_style(items, style, db_path=None):
    """
    批量设置一组格子的样式（不影响内容）。
    items: [(row_id, day_id), ...]；style: 'red'/'green'/'blue'/'yellow'/''（''=普通）。
    空内容 + 普通样式 -> 删除该格；其余情况保留（或新建）格子并写入样式。
    """
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        for row_id, day_id in items:
            cur.execute(
                "SELECT content FROM zq_cells WHERE row_id = ? AND day_id = ?",
                (row_id, day_id),
            )
            row = cur.fetchone()
            content = row["content"] if row else ""
            if not style:
                if content == "":
                    cur.execute(
                        "DELETE FROM zq_cells WHERE row_id = ? AND day_id = ?",
                        (row_id, day_id),
                    )
                else:
                    cur.execute(
                        "UPDATE zq_cells SET style = '' WHERE row_id = ? AND day_id = ?",
                        (row_id, day_id),
                    )
            else:
                cur.execute(
                    """
                    INSERT INTO zq_cells (row_id, day_id, content, style)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(row_id, day_id)
                    DO UPDATE SET style = excluded.style, content = excluded.content
                    """,
                    (row_id, day_id, content, style),
                )
        conn.commit()
        return len(items)
    finally:
        conn.close()