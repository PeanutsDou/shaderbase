#!/usr/bin/env python3
# coding: utf-8
"""
g66-shader-grammar/scripts/coverage.py

跑 tree-sitter parse 全库，统计覆盖率。
用法:
  python scripts/coverage.py <shader_source_root> [--tree-sitter <path>]
"""
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


def find_shader_files(root):
    """找所有 .nsf/.hlsl/.fxh，跳过 no_source/pipeline_output/bin/.git"""
    skip_dirs = {"no_source", "no_source_pc", "pipeline_output", "bin", ".git"}
    exts = {".nsf", ".hlsl", ".fxh"}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext in exts:
                files.append(os.path.join(dirpath, f))
    return files


def parse_file(ts_exe, path):
    """跑 tree-sitter parse --stat，返回 (ok, error_count, parse_ms)"""
    try:
        result = subprocess.run(
            [ts_exe, "parse", "--stat", path],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        # 找 ERROR 节点数
        error_count = len(re.findall(r"\(ERROR\b", output))
        # 找 stat 行: <path>  X ms  Y bytes/ms  (...)
        stat_match = re.search(
            r"(\d+\.?\d*)\s*ms\s+(\d+)\s*bytes/ms", output
        )
        parse_ms = float(stat_match.group(1)) if stat_match else 0.0
        ok = error_count == 0
        return ok, error_count, parse_ms
    except subprocess.TimeoutExpired:
        return False, -1, 0.0
    except Exception as e:
        return False, -2, 0.0


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/coverage.py <shader_source_root> [--tree-sitter <path>]")
        sys.exit(1)
    root = sys.argv[1]
    ts_exe = "./node_modules/tree-sitter-cli/tree-sitter.exe"
    for i, arg in enumerate(sys.argv[2:], 2):
        if arg == "--tree-sitter" and i + 1 < len(sys.argv):
            ts_exe = sys.argv[i + 1]

    # 切到 grammar 项目目录（让 tree-sitter 能找到 grammar）
    grammar_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(grammar_dir)

    print(f"Shader source root: {root}")
    print(f"Tree-sitter: {ts_exe}")
    print(f"Grammar dir: {grammar_dir}")
    print("Searching shader files...")

    files = find_shader_files(root)
    total = len(files)
    print(f"Found {total} files\n")

    ok_count = 0
    error_count_total = 0
    failed_files = []  # (path, error_count)
    ext_stats = Counter()
    dir_stats = Counter()
    total_ms = 0.0
    wall_start = time.time()

    for i, path in enumerate(files, 1):
        ok, errors, parse_ms = parse_file(ts_exe, path)
        total_ms += parse_ms
        if ok:
            ok_count += 1
        else:
            error_count_total += errors
            rel = os.path.relpath(path, root)
            failed_files.append((rel, errors))
        ext = os.path.splitext(path)[1].lower()
        ext_stats[ext] += 1
        # 顶层目录
        parts = os.path.relpath(path, root).replace("\\", "/").split("/")
        if len(parts) > 1:
            dir_stats[parts[0]] += 1
        if i % 100 == 0:
            print(f"  {i}/{total} ({ok_count} ok)...")

    wall_end = time.time()

    print("\n" + "=" * 60)
    print("==== Baseline coverage report ====")
    print("=" * 60)
    print(f"total_files:            {total}")
    print(f"parsed_ok:              {ok_count}  ({ok_count*100//total}%)")
    print(f"failed:                 {total - ok_count}  ({(total-ok_count)*100//total}%)")
    print(f"total ERROR nodes:      {error_count_total}")
    print(f"")
    print(f"parse time (sum):       {total_ms:.1f} ms")
    print(f"wall time:              {wall_end - wall_start:.1f} s")
    print(f"avg per file:           {total_ms/total:.2f} ms")
    print(f"")
    print("==== By extension ====")
    for ext, c in ext_stats.most_common():
        print(f"  {ext}: {c}")
    print(f"")
    print("==== By top-level dir ====")
    for d, c in dir_stats.most_common():
        print(f"  {d}/: {c}")
    print(f"")

    # Top 20 失败文件（按 ERROR 数排）
    failed_files.sort(key=lambda x: -x[1])
    print(f"==== Top 20 files by ERROR count ====")
    for rel, errors in failed_files[:20]:
        print(f"  {errors:5d}  {rel}")
    print(f"")
    print(f"==== Bottom 5 failed (smallest ERROR count) ====")
    for rel, errors in failed_files[-5:]:
        print(f"  {errors:5d}  {rel}")


if __name__ == "__main__":
    main()
