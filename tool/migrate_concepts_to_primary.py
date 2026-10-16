# -*- coding: utf-8 -*-
"""
把 stocks.concept 里的「子概念」统一替换为对应的「一级概念」。

背景：
    股票详情页的编辑弹窗只认一级概念（非一级片段不展示也不保存），
    但历史数据中可能残留子概念名（如「锰矿」「负极」「硅基负极」）。
    本脚本把这些子概念统一替换为其所属的一级概念（如「固态电池」），
    让写入侧与展示侧保持一致。

规则：
    - 片段本身是一级概念 → 保持不变
    - 片段是子概念 → 替换为其最顶层的一级概念名
    - 片段不在 concepts 表中（游离概念）→ 保持不变
    - 替换后去重（保序），用 '-' 重新拼接

用法：
    python tool/migrate_concepts_to_primary.py --dry-run   # 只预览，不写库
    python tool/migrate_concepts_to_primary.py             # 执行迁移（自动备份）

可选：
    --limit-print 50    预览时最多打印多少条明细（默认 50）
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import DB_FILE, _split_concept_raw  # noqa: E402

JOINER = "-"


def build_child_to_primary(conn):
    """
    返回 {子概念名: 一级概念名}。
    只包含 level != 1 的概念；一级概念自身不出现在映射里（无需替换）。
    """
    cur = conn.cursor()
    cur.execute("SELECT id, name, level, parent_id FROM concepts")
    rows = [dict(r) for r in cur.fetchall()]
    by_id = {r["id"]: r for r in rows}

    def top_name(node):
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

    mapping = {}
    for r in rows:
        if (r["level"] or 1) == 1:
            continue
        primary = top_name(r)
        if primary:
            mapping[r["name"]] = primary
    return mapping


def rebuild(raw, mapping):
    """
    把概念串中的子概念替换为一级概念。
    返回 (新串, 是否发生变化, 被替换的片段集合)
    """
    pieces = _split_concept_raw(raw)
    if not pieces:
        return raw or "", False, set()

    changed = set()
    result = []
    for p in pieces:
        if p in mapping:
            new_p = mapping[p]
            if new_p != p:
                changed.add(p)
            p = new_p
        if p not in result:      # 去重，保序
            result.append(p)

    new_raw = JOINER.join(result)
    return new_raw, (new_raw != (raw or "")), changed


def backup(db_file):
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = "%s.bak_%s" % (db_file, ts)
    shutil.copy2(db_file, dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument("--limit-print", type=int, default=50, help="预览明细最大条数")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    mapping = build_child_to_primary(conn)

    print("子概念 → 一级概念 映射（共 %d 项）：" % len(mapping))
    for k, v in sorted(mapping.items()):
        print("    %s → %s" % (k, v))

    cur = conn.cursor()
    cur.execute("SELECT id, name, concept FROM stocks")
    rows = cur.fetchall()

    changes = []
    replaced_stat = {}
    for r in rows:
        old = r["concept"] or ""
        new, is_changed, changed = rebuild(old, mapping)
        if not is_changed:
            continue
        changes.append((r["id"], r["name"], old, new))
        for c in changed:
            replaced_stat[c] = replaced_stat.get(c, 0) + 1

    print("\n股票总数：%d" % len(rows))
    print("需要变更：%d 只" % len(changes))

    if replaced_stat:
        print("\n被替换的片段统计：")
        for k, v in sorted(replaced_stat.items(), key=lambda x: -x[1]):
            print("    %s：%d 只股票（→ %s）" % (k, v, mapping[k]))

    if changes:
        print("\n明细（最多 %d 条）：" % args.limit_print)
        for cid, name, old, new in changes[:args.limit_print]:
            print("    [%d] %s：%s  →  %s" % (cid, name, old, new))
        if len(changes) > args.limit_print:
            print("    ... 其余 %d 条略" % (len(changes) - args.limit_print))

    if args.dry_run:
        print("\n[DRY-RUN] 未写入数据库。")
        conn.close()
        return

    if not changes:
        print("\n没有需要变更的数据，无需备份与写入。")
        conn.close()
        return

    bak = backup(DB_FILE)
    print("\n已备份数据库 → %s" % bak)

    up = conn.cursor()
    for cid, name, old, new in changes:
        up.execute("UPDATE stocks SET concept = ? WHERE id = ?", (new, cid))
    conn.commit()
    conn.close()

    print("已更新 %d 只股票的概念。" % len(changes))


if __name__ == "__main__":
    main()
