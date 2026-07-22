#!/usr/bin/env python3
# coding: utf-8
"""迭代 8：全量 shader 代码 PreprocessorView 测试。

跑 build_preprocessor_view 在 shader-source 全库上，验证：
1. 全库跑通不崩（0 异常）
2. line_active 合理性（active/inactive 占比）
3. branch_sigs 合理性（嵌套深度分布）
4. #art 宏注入正确（default 0 生效）
5. 抽样人工核验（取有代表性的文件对照源码）

用法：py -3 -m shaderbase.preprocessor.bench_pv_full [shader_source_root]
"""
import os
import sys
import time
from collections import Counter

sys.path.insert(0, '.')

from shaderbase.parser.tree_sitter_loader import parser as get_parser
from shaderbase.preprocessor.interpreter import build_preprocessor_view
from shaderbase.preprocessor.art_macros import collect_art_macros

ROOT_DEFAULT = r'D:/douzhongjun/work/shader/shader-source'
SKIP_DIRS = {'no_source', 'no_source_pc', 'pipeline_output', 'bin', '.git'}
EXTS = {'.nsf', '.hlsl', '.fxh'}


def find_files(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            if os.path.splitext(f)[1].lower() in EXTS:
                out.append(os.path.join(dp, f))
    return out


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else ROOT_DEFAULT
    files = find_files(root)
    total = len(files)
    print(f"Shader source: {root}")
    print(f"Found {total} files\n")

    parser = get_parser()
    crash_count = 0
    total_lines = 0
    total_active = 0
    total_inactive = 0
    total_branches = 0
    total_art_macros = 0
    max_depth = 0
    depth_dist = Counter()
    files_with_art = 0
    files_with_branches = 0

    wall_start = time.time()
    parse_ms = 0.0
    pv_ms = 0.0

    for i, path in enumerate(files, 1):
        with open(path, 'rb') as f:
            src = f.read()
        t0 = time.perf_counter()
        tree = parser.parse(src)
        parse_ms += (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        try:
            # 空 defines（索引阶段，对齐 nsp 全分支视图）
            view = build_preprocessor_view(tree.root_node, src, {})
            art_macros = collect_art_macros(tree.root_node)
        except Exception as e:
            crash_count += 1
            if crash_count <= 5:
                print(f"  CRASH {path}: {e}")
            continue
        pv_ms += (time.perf_counter() - t1) * 1000

        total_lines += len(view.line_active)
        active = sum(1 for x in view.line_active if x)
        total_active += active
        total_inactive += len(view.line_active) - active
        total_branches += len(view.branch_merges)
        if view.branch_merges:
            files_with_branches += 1
        if art_macros:
            total_art_macros += len(art_macros)
            files_with_art += 1

        # 嵌套深度分布
        for sig in view.branch_sigs:
            depth = len(sig)
            max_depth = max(max_depth, depth)
            depth_dist[depth] += 1

        if i % 200 == 0:
            print(f"  {i}/{total}  (active={total_active}, branches={total_branches})")

    wall = time.time() - wall_start

    print("\n" + "=" * 64)
    print("==== 全量 PreprocessorView 报告（迭代 8）====")
    print("=" * 64)
    print(f"total_files:            {total}")
    print(f"crash_count:            {crash_count}  ({crash_count*100/max(total,1):.2f}%)")
    print(f"files_with_branches:    {files_with_branches}  ({files_with_branches*100/max(total,1):.1f}%)")
    print(f"files_with_art:         {files_with_art}")
    print(f"total_art_macros:       {total_art_macros}")
    print()
    print(f"total_lines:            {total_lines}")
    print(f"total_active_lines:     {total_active}  ({total_active*100/max(total_lines,1):.1f}%)")
    print(f"total_inactive_lines:   {total_inactive}  ({total_inactive*100/max(total_lines,1):.1f}%)")
    print(f"total_branch_merges:     {total_branches}")
    print(f"max_nesting_depth:       {max_depth}")
    print()
    print("==== 嵌套深度分布 ====")
    for depth, count in sorted(depth_dist.items()):
        if depth <= 5 or count > 100:
            print(f"  depth {depth}: {count} lines")
    print()
    print(f"parse time (sum):       {parse_ms:.0f} ms")
    print(f"pv time (sum):          {pv_ms:.0f} ms")
    print(f"wall time:              {wall:.2f} s")
    print(f"per file:               {wall*1000/total:.2f} ms")

    # 抽样核验：取有 #art 的文件，检查默认 0 注入
    print("\n" + "=" * 64)
    print("==== 抽样核验：#art 文件的默认 0 注入 ====")
    print("=" * 64)
    sample_count = 0
    for path in files:
        if sample_count >= 3:
            break
        with open(path, 'rb') as f:
            src = f.read()
        tree = parser.parse(src)
        art = collect_art_macros(tree.root_node)
        if not art:
            continue
        view = build_preprocessor_view(tree.root_node, src, {})
        rel = os.path.relpath(path, root).replace(os.sep, '/')
        print(f"\n{rel}  ({len(art)} art macros)")
        for m in art[:3]:
            # 检查这个 art macro 在 defines 里是不是 0
            in_defines = m.name in view.initial_macros
            print(f"  #art {m.name} ({m.art_type}) → initial_macros: {in_defines}, source={view.initial_macros.get(m.name).source if in_defines else 'n/a'}")
        sample_count += 1


if __name__ == '__main__':
    main()
