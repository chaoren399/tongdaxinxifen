# -*- coding: utf-8 -*-
"""
补全 SQLite stocks 表中 code 为空的记录（按 name 匹配 全部Ａ股 CSV）。

用法：
    python tool/fill_stock_codes_sqlite.py [db路径] [CSV路径]

默认使用：
    db   = ../data/tongdaxin.db
    csv  = ../data/全部Ａ股20261009.csv

执行前会先备份数据库（备份名 <db>(已备份).db），可放心运行。
名称匹配做了归一化：全角转半角、去空格、统一大小写，兼容
"万 科Ａ" / "万科A" / "万科Ａ" 等不同写法。
"""
import csv
import os
import shutil
import sqlite3
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {
    "db":  os.path.join(HERE, "..", "data", "tongdaxin.db"),
    "csv": os.path.join(HERE, "..", "data", "全部Ａ股20261009.csv"),
}


def norm(s):
    """名称归一化：全角转半角、去空格、统一大写。"""
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(s.split()).upper()


def read_csv_auto(path):
    """自动识别编码读取 CSV，返回 (rows, encoding)。"""
    for enc in ("utf-8-sig", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.reader(f)), enc
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"无法识别文件编码：{path}")


def build_name2codes(rows):
    """从映射 CSV 建立 名称 -> [代码...]，同名多码时保留全部（取首个用于补全）。"""
    header = rows[0]
    i_code = next(i for i, h in enumerate(header) if "代码" in h)
    i_name = next(i for i, h in enumerate(header) if "名称" in h)
    name2codes = {}
    for r in rows[1:]:
        if len(r) <= max(i_code, i_name):
            continue
        code = r[i_code].strip()
        name = r[i_name].strip()
        if not code or not name:
            continue
        name2codes.setdefault(name, []).append(code)
    return name2codes


def main():
    db_path  = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULTS["db"])
    csv_path = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else DEFAULTS["csv"])

    if not os.path.isfile(csv_path):
        raise SystemExit(f"找不到映射 CSV: {csv_path}")
    if not os.path.isfile(db_path):
        raise SystemExit(f"找不到数据库: {db_path}")

    # 1) 备份
    backup = os.path.splitext(db_path)[0] + "(已备份).db"
    if not os.path.isfile(backup):
        shutil.copy2(db_path, backup)
        print(f"已备份数据库 -> {backup}")
    else:
        print(f"备份已存在，跳过: {backup}")

    # 2) 读取映射
    rows, enc = read_csv_auto(csv_path)
    name2codes = build_name2codes(rows)
    norm2codes = {norm(k): v for k, v in name2codes.items()}
    print(f"读取 CSV 成功，编码={enc}，共 {len(name2codes)} 个不同名称")

    # 3) 查询待补全记录
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, code FROM stocks "
        "WHERE code IS NULL OR TRIM(code) = '' ORDER BY id"
    )
    blanks = cur.fetchall()
    print(f"数据库中 code 为空的股票: {len(blanks)} 条")

    # 4) 逐条补全
    filled = missing = ambiguous = 0
    missing_names = []
    for row in blanks:
        sid, name = row["id"], row["name"]
        if not name:
            continue
        candidates = name2codes.get(name) or norm2codes.get(norm(name))
        if not candidates:
            missing += 1
            missing_names.append(name)
            continue
        code = candidates[0]
        cur.execute("UPDATE stocks SET code = ? WHERE id = ?", (code, sid))
        if len(candidates) > 1:
            ambiguous += 1
        filled += 1

    conn.commit()
    conn.close()

    # 5) 汇总
    print(f"\n已补全: {filled} 条")
    print(f"未匹配: {missing} 条")
    print(f"同名多码（已取首个）: {ambiguous} 条")
    if missing_names:
        print("\n未匹配到代码的股票名称（前 50 个）：")
        for n in sorted(set(missing_names))[:50]:
            print("    " + n)


if __name__ == "__main__":
    main()
