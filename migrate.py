"""
数据迁移脚本：从 CSV 一次性导入到 SQLite。
可重复运行（会清空旧数据后重新导入）。

用法：
    python migrate.py            # 默认从 data/通达信一级二级三级行业汇总20261007 - 副本.csv 导入
    python migrate.py path.csv   # 指定 CSV 路径
"""
import os
import sys

from db import init_db_from_csv, CSV_FILE, DB_FILE


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else CSV_FILE
    if not os.path.exists(csv_path):
        print(f"CSV 文件不存在：{csv_path}")
        sys.exit(1)
    print(f"正在从 {csv_path} 导入到 {DB_FILE} ...")
    result = init_db_from_csv(csv_path)
    print(f"导入完成：行业 {result['industries']} 个，股票 {result['stocks']} 只。")
    print(f"数据库文件：{result['db_path']}")


if __name__ == "__main__":
    main()