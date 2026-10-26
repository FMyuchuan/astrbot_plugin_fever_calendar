#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把仓库里指代厂商的隐晦词统一换掉。

用法：
    python scripts/rebrand.py 发烧                 # 从当前词换成新词
    python scripts/rebrand.py 发烧 --from 发烧    # 明确指定旧词
    python scripts/rebrand.py 发烧 --dry-run       # 只统计，不改文件

只动文档与文案；域名（*.xiaomi.com/*.xiaomi.net）、UA、协议参数
（sid=xiaomiio、uniqueKey）这些功能性字符串一律跳过。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", "data"}
EXTS = {".py", ".md", ".json", ".yaml", ".yml"}
# 这些行属于协议细节，品牌词替换不能碰
PROTECTED_MARKS = ("xiaomi.com", "xiaomi.net", "XiaoMi/", "xiaomiio", "_xiaomi", "api.io.mi.com")


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.suffix not in EXTS:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="统一替换厂商隐晦词")
    ap.add_argument("to", help="新的隐晦词，例如 发烧")
    ap.add_argument("--from", dest="frm", default="发烧", help="当前使用的词，默认 发烧")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    old, new = args.frm.strip(), args.to.strip()
    if not new or " " in new or "\t" in new:
        print("新词不能为空，也不能带空白字符")
        return 2
    if old == new:
        print("新旧词一样，没什么可换的")
        return 0

    files, lines, skipped = 0, 0, 0
    for p in iter_files():
        text = p.read_text(encoding="utf-8")
        if old not in text:
            continue
        out, hit, skip = [], 0, 0
        for line in text.splitlines(keepends=True):
            if old in line and not any(m in line for m in PROTECTED_MARKS):
                out.append(line.replace(old, new))
                hit += 1
            else:
                if old in line:
                    skip += 1
                out.append(line)
        if hit:
            files += 1
            lines += hit
            skipped += skip
            print(f"  {p.relative_to(ROOT)}: {hit} 行")
            if not args.dry_run:
                p.write_text("".join(out), encoding="utf-8")

    verb = "将替换" if args.dry_run else "已替换"
    print(f"\n{verb} {lines} 行 / {files} 个文件（跳过协议标识 {skipped} 行）")
    if args.dry_run:
        print("加上 --dry-run 之外的参数再跑一次才会真正写入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
