# -*- coding: utf-8 -*-
"""
从 10.10.10.3:5030 的 /api/export-stock 接口拉取「选项A~H」并写回 SQLite stocks 表。

用法：
    # 1) 只读预览
    python tool/import_options_from_5030.py
    python tool/import_options_from_5030.py --all

    # 2) 真正写入（执行前会自动备份 db）
    python tool/import_options_from_5030.py --all --write
    python tool/import_options_from_5030.py --names "中国平安,贵州茅台" --write

参数：
    --base      远程服务地址，默认 http://10.10.10.3:5030
    --names     逗号分隔的股票名列表；不传则取 DB 有 code 的股票
    --all       拉取 DB 中所有有 code 的股票（默认仅前 5 只）
    --limit N   当未传 --names 且未传 --all 时，取前 N 只
    --workers   并发数，默认 8
    --write     写入 DB（默认只读预览）
    --db        自定义 DB 路径

匹配键：股票名称（精确匹配，name 完全一致）。
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
DB_DEFAULT = os.path.normpath(os.path.join(HERE, "..", "data", "tongdaxin.db"))

BASE_DEFAULT = "http://10.10.10.3:5030"
OPTION_KEYS = ["option_a", "option_b", "option_c", "option_d",
               "option_e", "option_f", "option_g", "option_h"]


def http_get_json(url, timeout=10):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "curl/8", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_options(base, name):
    """调 /api/export-stock?name=<name>，返回 (data dict 或 None, 错误信息)"""
    url = base.rstrip("/") + "/api/export-stock?" + urllib.parse.urlencode({"name": name})
    try:
        resp = http_get_json(url)
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return None, f"网络错误: {e}"
    if not resp.get("success"):
        return None, f"接口 success=false: {resp.get('message') or resp}"
    data = resp.get("data")
    if isinstance(data, list):
        if not data:
            return None, "接口 data 为空列表"
        data = data[0]
    if not isinstance(data, dict):
        return None, f"接口 data 格式异常: {type(data).__name__}"
    return data, None


def pick_names_from_db(db_path, limit, all_rows=False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if all_rows:
        cur.execute("SELECT name, code FROM stocks WHERE code != '' ORDER BY code")
    else:
        cur.execute("SELECT name, code FROM stocks WHERE code != '' ORDER BY code LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return [(r["name"], r["code"]) for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_DEFAULT)
    ap.add_argument("--names", default="", help="逗号分隔的股票名列表")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--all", action="store_true", help="拉取 DB 中所有有 code 的股票")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--write", action="store_true", help="真正写入 DB（默认只读预览）")
    ap.add_argument("--db", default=DB_DEFAULT)
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"找不到数据库: {db_path}")
        sys.exit(1)

    if args.names.strip():
        pairs = [(n.strip(), "") for n in args.names.split(",") if n.strip()]
    elif args.all:
        pairs = pick_names_from_db(db_path, 0, all_rows=True)
    else:
        pairs = pick_names_from_db(db_path, args.limit, all_rows=False)

    if not pairs:
        print("没有可处理的股票")
        return

    # 读取 DB 中所有 name -> 当前选项 的映射（一次性加载到内存）
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, name, " + ", ".join(OPTION_KEYS) + " FROM stocks")
    stock_by_name = {}
    for r in cur.fetchall():
        stock_by_name[r["name"]] = {
            "id": r["id"],
            "options": {k: (r[k] or "") for k in OPTION_KEYS},
        }
    print(f"远程服务: {args.base}")
    print(f"DB 路径  : {db_path}")
    print(f"模式     : {'写入' if args.write else '只读预览'}")
    print(f"并发数   : {args.workers}")
    print(f"目标股票 : {len(pairs)} 只")
    print("-" * 78)

    lock = threading.Lock()
    counters = {"ok": 0, "skip_same": 0, "skip_empty": 0, "skip_nodb": 0, "fail": 0}
    detailed_fail = []
    updated_rows = []  # (name, fetched_dict)

    def worker(name_code):
        name, _ = name_code
        # 已存在的「所有选项都和 DB 一样」也仍要拉（用于首次导入），不能提前 skip。
        data, err = fetch_options(args.base, name)
        return name, data, err

    start = time.time()
    total = len(pairs)
    done = 0
    last_print = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker, p): p[0] for p in pairs}
        for fut in as_completed(futs):
            done += 1
            name, data, err = fut.result()
            if err:
                with lock:
                    counters["fail"] += 1
                    detailed_fail.append((name, err))
            elif data is None:
                with lock:
                    counters["fail"] += 1
                    detailed_fail.append((name, "接口 data 缺失"))
            else:
                fetched = {k: (str(data.get(k) or "").strip()) for k in OPTION_KEYS}
                non_empty = {k: v for k, v in fetched.items() if v}
                stock = stock_by_name.get(name)
                if stock is None:
                    with lock:
                        counters["skip_nodb"] += 1
                else:
                    old = stock["options"]
                    if not non_empty:
                        with lock:
                            counters["skip_empty"] += 1
                    elif all(fetched[k] == (old[k] or "") for k in OPTION_KEYS):
                        with lock:
                            counters["skip_same"] += 1
                    else:
                        with lock:
                            counters["ok"] += 1
                        if args.write:
                            updated_rows.append((name, fetched))

            # 进度输出：每 25 只或完成时
            if done - last_print >= 25 or done == total:
                last_print = done
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                pct = done * 100 / total
                with lock:
                    c = dict(counters)
                print(f"\r  进度 {done}/{total} ({pct:5.1f}%)  "
                      f"rate={rate:4.1f}/s  eta={eta:5.0f}s  "
                      f"ok={c['ok']:4d} same={c['skip_same']:4d} empty={c['skip_empty']:4d} "
                      f"nodb={c['skip_nodb']:4d} fail={c['fail']:4d}",
                      end="", flush=True)
    print()  # 换行

    # 写入（单线程批写）
    if args.write and updated_rows:
        print(f"\n开始写入 {len(updated_rows)} 条...")
        cur = conn.cursor()
        for i, (name, fetched) in enumerate(updated_rows, 1):
            sets = ", ".join(f"{k} = ?" for k in OPTION_KEYS)
            params = [fetched[k] for k in OPTION_KEYS] + [name]
            cur.execute(f"UPDATE stocks SET {sets} WHERE name = ?", params)
            if i % 100 == 0 or i == len(updated_rows):
                conn.commit()
                print(f"\r  写入进度 {i}/{len(updated_rows)}", end="", flush=True)
        conn.commit()
        print()

    if args.write and (counters["ok"] or counters["fail"]):
        bak = db_path + "(已备份).db"
        try:
            shutil.copy2(db_path, bak)
            print(f"数据库已备份到: {bak}")
        except Exception as e:
            print(f"[警告] 备份失败: {e}")
    conn.close()

    elapsed = time.time() - start
    print("-" * 78)
    print(f"汇总:  ok={counters['ok']:4d}  same={counters['skip_same']:4d}  "
          f"empty={counters['skip_empty']:4d}  nodb={counters['skip_nodb']:4d}  "
          f"fail={counters['fail']:4d}  耗时={elapsed:.1f}s")

    if detailed_fail:
        # 最多显示前 20 条失败
        print(f"\n失败明细（{len(detailed_fail)} 条，前 20 条）:")
        for name, err in detailed_fail[:20]:
            print(f"  - {name}: {err}")

    if not args.write:
        print("\n提示: 以上为只读预览，确无问题后加 --write 真正写入")


if __name__ == "__main__":
    main()
