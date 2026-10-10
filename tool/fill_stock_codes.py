# -*- coding: utf-8 -*-
"""
补全行业汇总 CSV 中"有股票名称、缺股票代码"的行。

用法：
    python fill_stock_codes.py [行业汇总文件] [股票代码映射文件] [输出文件]
    （三个参数均可省略，省略时使用下方 DEFAULTS 默认值）

映射文件格式：两列 CSV，表头含"股票代码"和"股票名称"（如通达信导出的"全部Ａ股"文件）。
名称匹配时会做归一化：去空格、全角字母/数字转半角、忽略大小写，以兼容"万 科Ａ"这类写法。
"""
import csv
import os
import sys
import unicodedata

# 默认路径（可用命令行参数覆盖）
DEFAULTS = {
    "src": r"D:\Personal\Temp\codebuddy-dropped-files\c34332e9-fca4-466a-97fe-d8d83b611cf2\通达信一级二级三级行业汇总20261007 - 副本.csv",
    "map": r"D:\Personal\Temp\codebuddy-dropped-files\9e7ae0bc-4474-4d1d-ade8-c6fb5937e34d\全部Ａ股20261009.csv",
    "dst": "",  # 为空时自动在源文件名后加"(已补全)"
}


def norm(s):
    """名称归一化：全角转半角、去空格、统一大写"""
    s = unicodedata.normalize("NFKC", s)
    return "".join(s.split()).upper()


def read_csv_auto(path):
    """自动识别编码读取 CSV，返回 (rows, encoding)"""
    for enc in ("utf-8-sig", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.reader(f)), enc
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"无法识别文件编码：{path}")


def build_name2code(map_rows):
    """从映射文件建立 归一化名称 -> [代码...] ；返回 (映射, 重复名称)"""
    header = map_rows[0]
    i_code = next(i for i, h in enumerate(header) if "代码" in h)
    i_name = next(i for i, h in enumerate(header) if "名称" in h)
    name2codes, dup = {}, {}
    for r in map_rows[1:]:
        if len(r) <= max(i_code, i_name):
            continue
        code, name = r[i_code].strip(), r[i_name].strip()
        if not code or not name:
            continue
        key = norm(name)
        if key in name2codes and name2codes[key][0] != code:
            dup.setdefault(key, [name2codes[key][0]]).append(code)
        else:
            name2codes.setdefault(key, [code])
    return name2codes, dup


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULTS["src"]
    map_file = sys.argv[2] if len(sys.argv) > 2 else DEFAULTS["map"]
    dst = sys.argv[3] if len(sys.argv) > 3 else DEFAULTS["dst"]
    if not dst:
        root, ext = os.path.splitext(src)
        dst = f"{root}(已补全){ext}"

    rows, enc = read_csv_auto(src)
    print(f"读取行业文件成功，编码={enc}，共 {len(rows)} 行（含表头）")

    map_rows, map_enc = read_csv_auto(map_file)
    name2codes, dup = build_name2code(map_rows)
    print(f"读取映射文件成功，编码={map_enc}，共 {len(name2codes)} 个不同股票名称")

    header = rows[0]
    i_name = header.index("股票名称")
    i_code = header.index("股票代码")

    filled, missing, ambiguous = [], set(), {}
    for r in rows[1:]:
        if len(r) <= max(i_name, i_code):
            continue
        name, code = r[i_name].strip(), r[i_code].strip()
        if name and not code:
            codes = name2codes.get(norm(name))
            if not codes:
                missing.add(name)
            else:
                r[i_code] = codes[0]
                filled.append(name)
                if len(codes) > 1:
                    ambiguous[name] = codes

    with open(dst, "w", encoding=enc, newline="") as f:
        csv.writer(f).writerows(rows)

    print(f"\n已补全 {len(filled)} 行 -> {dst}")
    if missing:
        print(f"\n[!] 未匹配到代码的名称（{len(missing)} 个）：")
        for n in sorted(missing):
            print("    " + n)
    if ambiguous:
        print(f"\n[!] 名称对应多个代码（{len(ambiguous)} 个，已取第一个，请人工核对）：")
        for n, cs in ambiguous.items():
            print(f"    {n}: {cs}")
    if dup:
        print(f"\n[!] 映射文件本身存在同名不同码（{len(dup)} 个）：")
        for k, cs in dup.items():
            print(f"    {k}: {cs}")
    if not missing and not ambiguous and not dup:
        print("全部匹配成功，无异常。")


if __name__ == "__main__":
    main()
