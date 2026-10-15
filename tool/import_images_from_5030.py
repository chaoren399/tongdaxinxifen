# -*- coding: utf-8 -*-
"""
从 10.10.10.3:5030 的 /api/public/images 接口拉取「所有带图片的股票」，
把图片下载到本地 data/stock_images/ 并写入 SQLite stock_images 表。

用法：
    # 1) 只读预览：只统计匹配情况，不下载、不写库
    python tool/import_images_from_5030.py

    # 2) 真正导入：下载图片并写库（执行前自动备份 db）
    python tool/import_images_from_5030.py --write

参数：
    --base           远程服务地址，默认 http://10.10.10.3:5030
    --per-page       接口每页条数，默认 50
    --workers        并发下载数，默认 8
    --max-per-stock  单只股票最多导入图片数，默认 20（与 app 的 MAX_IMAGES_PER_STOCK 一致）
    --limit N        仅处理前 N 只股票（调试用）
    --alias          额外/覆盖别名映射，格式「远程名=本地名,远程名2=本地名2」
    --write          真正下载并写库（默认只读预览）
    --db             自定义 DB 路径

匹配键：远程记录的 question 字段 vs 本地 stocks.name。
    依次尝试：精确匹配 -> 别名映射(--alias) -> 去后缀/注释/尾号 -> 唯一前缀匹配。
    别名格式：远程名=本地名（多个用逗号分隔），用于远程与本地改名不一致的股票。
    内置默认别名（可用 --alias 覆盖）：
        三房巷=*ST三房, 会畅通讯=会畅科技, 帝欧家居=帝欧水华,
        豆神教育=ST豆神, 迪瑞医疗=ST迪瑞, 海荣冷链=海容冷链
文件名：{stock_id}_{远程记录id}{ext}，保证重复执行幂等（不会重复下载/插入）。
"""
import argparse
import json
import math
import os
import re
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
IMG_DIR_DEFAULT = os.path.normpath(os.path.join(HERE, "..", "data", "stock_images"))

BASE_DEFAULT = "http://10.10.10.3:5030"
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 远程股票名 -> 本地股票名（远程与本地改名不一致时使用），可用 --alias 覆盖
DEFAULT_ALIASES = {
    "三房巷": "*ST三房",
    "会畅通讯": "会畅科技",
    "帝欧家居": "帝欧水华",
    "豆神教育": "ST豆神",
    "迪瑞医疗": "ST迪瑞",
    "海荣冷链": "海容冷链",
}


def parse_aliases(text):
    """解析「远程名=本地名,远程名2=本地名2」为 dict；忽略空项与无 '=' 的项。"""
    out = {}
    if not text:
        return out
    for part in re.split(r"[,，]", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if k and v:
            out[k] = v
    return out


def http_get_json(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "curl/8", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_all_records(base, per_page, timeout=20):
    """分页拉取 /api/public/images 的全部记录。"""
    records = []
    page = 1
    total_pages = None
    while True:
        url = base.rstrip("/") + "/api/public/images?" + urllib.parse.urlencode(
            {"page": page, "per_page": per_page}
        )
        try:
            resp = http_get_json(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code} {e.reason} -> {url}")
        except Exception as e:
            raise RuntimeError(f"网络错误: {e} -> {url}")
        if not resp.get("success"):
            raise RuntimeError(f"接口 success=false: {resp.get('message') or resp}")
        data = resp.get("data") or []
        total = resp.get("total") or 0
        total_pages = resp.get("total_pages") or (math.ceil(total / per_page) if total else 1)
        records.extend(data)
        print(f"  第 {page}/{total_pages} 页：+{len(data):3d} 条（累计 {len(records)}）")
        if not data or page >= total_pages or len(records) >= total:
            break
        page += 1
    return records


def guess_ext(url):
    """从图片 URL 推断扩展名；无法识别时默认 .png。"""
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in ALLOWED_IMAGE_EXTS else ".png"


_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def sniff_image_ext(raw):
    """按文件头判断真实图片类型，非图片返回 ''。"""
    for magic, ext in _MAGIC:
        if raw.startswith(magic):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return ""


def download_image(url, timeout=30):
    """下载图片并校验；返回 (bytes, ext) 或抛异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if not raw:
        raise RuntimeError("空响应")
    ext = sniff_image_ext(raw)
    if not ext:
        raise RuntimeError("返回内容不是图片")
    return raw, ext


def resolve_stock_id(name, name_to_id, prefix_index, aliases=None):
    """
    把远程 question 名称解析为本地 stock_id。远程名称常带后缀/注释，例如：
      「天瑞仪器-300」「浙江东日-农产品加工」「宇邦新材-300 （焊带龙头）」
      「苏大维格300」（无连字符）「云从科技-688」（本地叫「云从科技-UW」）
    依次尝试：精确 -> 别名映射 -> 去后缀/注释/尾号 -> 唯一前缀匹配。
    返回 (stock_id 或 None, 匹配方式: exact/alias/suffix/prefix/none)。
    """
    aliases = aliases or {}
    sid = name_to_id.get(name)
    if sid is not None:
        return sid, "exact"
    base = name
    for sep in ("-", "－", "（", "("):
        base = base.split(sep)[0]
    base = base.strip()
    # 去掉末尾连着的数字/字母（如 苏大维格300）
    stripped = re.sub(r"[0-9A-Za-z]+$", "", base).strip()
    for cand in (base, stripped):
        if not cand or cand == name:
            continue
        # 先查别名映射（远程改名与本地不一致，如 三房巷 -> *ST三房）
        target = aliases.get(cand)
        if target:
            sid = name_to_id.get(target)
            if sid is not None:
                return sid, "alias"
        sid = name_to_id.get(cand)
        if sid is not None:
            return sid, "suffix"
    # 整名直接命中别名（无后缀场景，如「三房巷」）
    target = aliases.get(name)
    if target:
        sid = name_to_id.get(target)
        if sid is not None:
            return sid, "alias"
    # 唯一前缀匹配（如 云从科技 -> 云从科技-UW），仅在候选唯一时采用，避免误配
    for cand in (stripped, base):
        if len(cand) >= 3:
            hits = prefix_index.get(cand)
            if hits and len(hits) == 1:
                return hits[0], "prefix"
    return None, "none"


def load_stock_map(db_path):
    """返回 (name -> stock_id)、(stock_id -> 已有文件名集合)、(前缀 -> stock_id 列表)。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM stocks")
    name_to_id = {}
    prefix_index = {}
    for r in cur.fetchall():
        nm = r["name"]
        # 同名股票只取第一条
        sid = name_to_id.setdefault(nm, r["id"])
        for i in range(3, len(nm) + 1):
            prefix_index.setdefault(nm[:i], [])
            if sid not in prefix_index[nm[:i]]:
                prefix_index[nm[:i]].append(sid)
    id_to_files = {}
    cur.execute("SELECT stock_id, filename FROM stock_images")
    for r in cur.fetchall():
        id_to_files.setdefault(r["stock_id"], set()).add(r["filename"])
    conn.close()
    return name_to_id, id_to_files, prefix_index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_DEFAULT)
    ap.add_argument("--per-page", type=int, default=50)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-per-stock", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 只股票（调试用）")
    ap.add_argument("--alias", default="",
                    help="别名映射「远程名=本地名」，多个用逗号分隔；会覆盖同名内置默认别名")
    ap.add_argument("--write", action="store_true", help="真正下载并写入 DB（默认只读预览）")
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--img-dir", default=IMG_DIR_DEFAULT)
    args = ap.parse_args()

    aliases = dict(DEFAULT_ALIASES)
    aliases.update(parse_aliases(args.alias))

    db_path = os.path.abspath(args.db)
    img_dir = os.path.abspath(args.img_dir)
    if not os.path.exists(db_path):
        print(f"找不到数据库: {db_path}")
        sys.exit(1)
    os.makedirs(img_dir, exist_ok=True)

    print(f"远程服务: {args.base}/api/public/images")
    print(f"DB 路径  : {db_path}")
    print(f"图片目录 : {img_dir}")
    print(f"模式     : {'写入（下载+入库）' if args.write else '只读预览'}")
    if aliases:
        print("别名映射 : " + "、".join(f"{k}->{v}" for k, v in aliases.items()))
    print("-" * 78)

    print("拉取远程图片清单...")
    records = fetch_all_records(args.base, args.per_page)
    print(f"共获取 {len(records)} 条远程记录")

    name_to_id, id_to_files, prefix_index = load_stock_map(db_path)
    id_to_name = {}
    for nm, sid in name_to_id.items():
        id_to_name.setdefault(sid, nm)

    # 按股票名分组（同一只股票可能有多条记录/多张图），并去重同一 url
    by_name = {}
    for rec in records:
        name = (rec.get("question") or "").strip()
        url = (rec.get("image_url") or "").strip()
        if not name or not url:
            continue
        bucket = by_name.setdefault(name, {"urls": [], "seen": set()})
        if url in bucket["seen"]:
            continue
        bucket["seen"].add(url)
        bucket["urls"].append({"url": url, "id": rec.get("id")})

    resolved = []  # (remote_name, stock_id, how)
    unmatched = []
    for n in sorted(by_name):
        sid, how = resolve_stock_id(n, name_to_id, prefix_index, aliases)
        if sid is None:
            unmatched.append(n)
        else:
            resolved.append((n, sid, how))
    if args.limit:
        resolved = resolved[: args.limit]

    exact_cnt = sum(1 for _, _, how in resolved if how == "exact")
    alias_cnt = sum(1 for _, _, how in resolved if how == "alias")
    suffix_cnt = sum(1 for _, _, how in resolved if how == "suffix")
    prefix_cnt = sum(1 for _, _, how in resolved if how == "prefix")
    print(f"远程涉及股票 {len(by_name)} 只；匹配成功 {len(resolved)} 只"
          f"（精确 {exact_cnt} / 别名 {alias_cnt} / 去后缀 {suffix_cnt} / 前缀 {prefix_cnt}），"
          f"未匹配 {len(unmatched)} 只")
    if unmatched:
        print(f"未匹配股票（前 20）：{'、'.join(unmatched[:20])}")
    fuzzy = []
    for n, sid, how in resolved:
        if how == "exact":
            continue
        local = id_to_name.get(sid, "?")
        fuzzy.append(f"{n}{'->' + local if how == 'alias' else ''}({how})")
    if fuzzy:
        print(f"模糊匹配（共 {len(fuzzy)}）：{'、'.join(fuzzy[:100])}")

    # 计划任务
    tasks = []  # (stock_id, remote_id, url, ext, filename)
    plan_skip_exist = 0
    plan_skip_cap = 0
    for name, stock_id, _how in resolved:
        # 注意：多个远程名称（如“天瑞仪器”与“天瑞仪器-300”）可能指向同一只股票，
        # 因此用 id_to_files 作为共享状态，避免同一张图片被重复加入。
        have = id_to_files.setdefault(stock_id, set())
        urls = by_name[name]["urls"]
        slot = args.max_per_stock - len(have)
        if slot <= 0:
            plan_skip_cap += len(urls)
            continue
        for item in urls:
            ext = guess_ext(item["url"])
            remote_id = item.get("id")
            tag = f"r{remote_id}" if remote_id is not None else f"h{abs(hash(item['url'])) % 10**10}"
            filename = f"{stock_id}_{tag}{ext}"
            if filename in have:
                plan_skip_exist += 1
                continue
            if slot <= 0:
                plan_skip_cap += 1
                continue
            slot -= 1
            have.add(filename)
            tasks.append((stock_id, remote_id, item["url"], ext, filename))

    print("-" * 78)
    print(f"待下载 {len(tasks)} 张；已存在跳过 {plan_skip_exist} 张；超上限跳过 {plan_skip_cap} 张")

    if not args.write:
        print("\n提示: 以上为只读预览，确认无误后加 --write 真正下载并写入")
        return

    if not tasks:
        print("没有需要下载的图片。")
        return

    lock = threading.Lock()
    counters = {"ok": 0, "fail": 0, "bytes": 0}
    failures = []
    pending_inserts = []  # (stock_id, filename)

    def worker(task):
        stock_id, remote_id, url, ext, filename = task
        raw, real_ext = download_image(url)
        if real_ext != ext:
            # 以真实类型为准，修正文件名（仅当没有 .jpg/.jpeg 等别名冲突时）
            filename = f"{stock_id}_{('r%s' % remote_id) if remote_id is not None else 'h'}{real_ext}"
        return task, filename, raw

    start = time.time()
    done = 0
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker, t): t for t in tasks}
        for fut in as_completed(futs):
            done += 1
            try:
                task, filename, raw = fut.result()
            except Exception as e:
                with lock:
                    counters["fail"] += 1
                    failures.append((futs[fut][2], str(e)))
                continue
            stock_id, remote_id, url, ext, _ = task
            abs_path = os.path.join(img_dir, filename)
            try:
                tmp = abs_path + ".part"
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, abs_path)
            except Exception as e:
                with lock:
                    counters["fail"] += 1
                    failures.append((url, f"写文件失败: {e}"))
                continue
            with lock:
                counters["ok"] += 1
                counters["bytes"] += len(raw)
                pending_inserts.append((stock_id, filename))

            if done % 20 == 0 or done == total:
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                with lock:
                    c = dict(counters)
                print(f"\r  进度 {done}/{total}  ok={c['ok']} fail={c['fail']} "
                      f"rate={rate:4.1f}/s eta={eta:5.0f}s", end="", flush=True)
    print()

    # 写库（单线程批写）
    print(f"写入 stock_images 表（{len(pending_inserts)} 条）...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT stock_id, COALESCE(MAX(sort_order), -1) + 1 FROM stock_images GROUP BY stock_id")
    next_order = {r[0]: r[1] for r in cur.fetchall()}
    for i, (stock_id, filename) in enumerate(pending_inserts, 1):
        order = next_order.get(stock_id, 0)
        next_order[stock_id] = order + 1
        cur.execute(
            "INSERT INTO stock_images (stock_id, filename, sort_order, created_at) "
            "VALUES (?, ?, ?, ?)",
            (stock_id, filename, order, now),
        )
        if i % 100 == 0:
            conn.commit()
            print(f"\r  入库进度 {i}/{len(pending_inserts)}", end="", flush=True)
    conn.commit()
    conn.close()
    print()

    bak = db_path + "(已备份).db"
    try:
        shutil.copy2(db_path, bak)
        print(f"数据库已备份到: {bak}")
    except Exception as e:
        print(f"[警告] 备份失败: {e}")

    elapsed = time.time() - start
    print("-" * 78)
    print(f"汇总: 成功 {counters['ok']} 张（{counters['bytes'] / 1024 / 1024:.1f} MB）"
          f"  失败 {counters['fail']} 张  耗时 {elapsed:.1f}s")
    if failures:
        print(f"\n失败明细（{len(failures)} 条，前 20 条）:")
        for url, err in failures[:20]:
            print(f"  - {url}: {err}")


if __name__ == "__main__":
    main()
