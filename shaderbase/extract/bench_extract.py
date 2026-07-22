#!/usr/bin/env python3
# coding: utf-8
"""全库抽取率验证脚本（阶段 4 第 3 步）。

跑 NodeExtractor 在 shader-source 全库上，统计：
- 每类节点抽取总数（vs INVENTORY 文档的预期量级）
- 有没有抽不出名字的节点（抽取 bug 信号）
- 抽取耗时（应在 grammar parse 之上，不应有几个量级开销）

用法：py -3 -m shaderbase.extract.bench_extract <shader_source_root>
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter, defaultdict

from .nodes import NodeExtractor
from ..parser.tree_sitter_loader import parser as get_parser


SKIP_DIRS = {"no_source", "no_source_pc", "pipeline_output", "bin", ".git"}
EXTS = {".nsf", ".hlsl", ".fxh"}


def find_files(root: str) -> list[str]:
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            if os.path.splitext(f)[1].lower() in EXTS:
                out.append(os.path.join(dp, f))
    return out


# INVENTORY 文档的预期量级（用于对照抽取率是否合理）
EXPECTED = {
    "Function": 4758,        # grammar 跑出来的 function_definition 数
    "Struct": 1456,
    "Texture": 1794 + 2120,  # texture_declaration + 大写 Texture2D
    "SamplerState": 2096,
    "Technique": 852,
    "CBuffer": 8,            # INVENTORY §A4
    "Uniform": "~8000",      # annotation 块数 + static const
}


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else r"D:\douzhongjun\work\shader\shader-source"
    files = find_files(root)
    total = len(files)
    print(f"Shader source: {root}")
    print(f"Found {total} files\n")

    ext = NodeExtractor()
    # 跑一遍 parse-only 拿基线时间
    parser = get_parser()

    kind_total = Counter()
    kind_unnamed = Counter()       # 抽不出名字的节点
    kind_by_file = defaultdict(Counter)  # 每文件每类节点数
    files_with_extract_errors = 0
    total_nodes = 0

    wall_start = time.time()
    parse_ms = 0.0
    extract_ms = 0.0
    for i, path in enumerate(files, 1):
        with open(path, "rb") as f:
            src = f.read()
        t0 = time.perf_counter()
        tree = parser.parse(src)
        parse_ms += (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        try:
            nodes = ext.extract_file(src, path.replace("\\", "/"))
        except Exception as e:
            files_with_extract_errors += 1
            nodes = []
        extract_ms += (time.perf_counter() - t1) * 1000

        rel = os.path.relpath(path, root).replace("\\", "/")
        for n in nodes:
            kind_total[n.kind] += 1
            if n.name is None:
                kind_unnamed[n.kind] += 1
            kind_by_file[rel][n.kind] += 1
        total_nodes += len(nodes)

        if i % 200 == 0:
            print(f"  {i}/{total}  (nodes so far: {total_nodes})")

    wall = time.time() - wall_start

    print("\n" + "=" * 64)
    print("==== 抽取率报告（阶段 4 第 3 步） ====")
    print("=" * 64)
    print(f"total_files:            {total}")
    print(f"files_with_extract_err: {files_with_extract_errors}")
    print(f"total nodes extracted:  {total_nodes}")
    print()
    print(f"parse time (sum):       {parse_ms:.0f} ms")
    print(f"extract time (sum):     {extract_ms:.0f} ms")
    print(f"extract / parse ratio:  {extract_ms/max(parse_ms,1):.2f}x")
    print(f"wall time:              {wall:.2f} s")
    print()
    print("==== 各类节点抽取数 vs 预期 ====")
    print(f"{'kind':14s} {'extracted':>10s} {'unnamed':>9s} {'expected':>10s}  note")
    for kind in ["Function", "Struct", "Texture", "SamplerState", "Technique", "CBuffer", "Uniform"]:
        got = kind_total.get(kind, 0)
        unnamed = kind_unnamed.get(kind, 0)
        exp = EXPECTED.get(kind, "?")
        print(f"{kind:14s} {got:>10d} {unnamed:>9d} {str(exp):>10s}")
    print()
    # 抽取量 Top 10 文件
    file_totals = [(rel, sum(c.values())) for rel, c in kind_by_file.items()]
    file_totals.sort(key=lambda x: -x[1])
    print("==== Top 10 files by node count ====")
    for rel, n in file_totals[:10]:
        print(f"  {n:5d}  {rel}")
    print()
    if files_with_extract_errors:
        print(f"!! {files_with_extract_errors} files raised during extract — 需修抽取器")


if __name__ == "__main__":
    main()
