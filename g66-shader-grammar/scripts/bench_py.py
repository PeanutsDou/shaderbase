#!/usr/bin/env python3
# coding: utf-8
"""
临时全量 bench：用 Python 绑定 in-process 跑 shader-source 全库，
对比现有 subprocess 版 coverage.py 的速度。
跑完看 wall time / 解析率 / ERROR 数。
"""
import os
import sys
import time
from collections import Counter, defaultdict

from tree_sitter import Parser, Language
import tree_sitter_g66_shader

LANG = Language(tree_sitter_g66_shader.language())
PARSER = Parser(LANG)

SKIP_DIRS = {"no_source", "no_source_pc", "pipeline_output", "bin", ".git"}
EXTS = {".nsf", ".hlsl", ".fxh"}

# 想观察的节点类型计数（跟 v2 报告口径对齐 + G66 特化）
WATCH_TYPES = {
    "function_definition", "call_expression", "struct_specifier",
    "preproc_if", "preproc_ifdef", "preproc_ifndef", "preproc_elif",
    "preproc_else", "preproc_endif", "preproc_define", "preproc_include",
    "preproc_art_directive", "preproc_exclude_from_temp_tech",
    "technique_block", "pass_block", "metadata_block", "metadata_assignment",
    "texture_declaration", "sampler_state_declaration", "sampler_state_block",
    "cbuffer_specifier", "state_assignment", "g66_macro_statement",
    "field_declaration", "declaration",
}


def find_files(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            if os.path.splitext(f)[1].lower() in EXTS:
                out.append(os.path.join(dp, f))
    return out


def parse_one(path):
    """返回 (ok, error_count, total_nodes, type_counts, first_error_line)"""
    with open(path, "rb") as f:  # tree-sitter 吃 bytes，避开编码问题
        src = f.read()
    try:
        tree = PARSER.parse(src)
    except Exception as e:
        return False, -1, 0, Counter(), 0, str(e)
    root = tree.root_node
    err_count = 0
    total = 0
    counts = Counter()
    first_err_line = -1
    # cursor 前序遍历，比递归快且不爆栈
    cur = root.walk()
    if hasattr(cur, "goto_first_child") and callable(cur.goto_first_child):
        # 0.22 cursor API
        stack = [root]
        while stack:
            n = stack.pop()
            total += 1
            t = n.type
            counts[t] += 1
            if t == "ERROR":
                err_count += 1
                if first_err_line < 0:
                    first_err_line = n.start_point[0] + 1
            # children 反序入栈保证前序
            cs = n.children
            for c in reversed(cs):
                stack.append(c)
    else:
        # fallback: 走 walk() generator
        for n in root.walk():
            total += 1
            t = n.type
            counts[t] += 1
            if t == "ERROR":
                err_count += 1
                if first_err_line < 0:
                    first_err_line = n.start_point[0] + 1
    return (err_count == 0), err_count, total, counts, first_err_line, None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else r"D:\douzhongjun\work\shader\shader-source"
    files = find_files(root)
    total = len(files)
    print(f"Shader source: {root}")
    print(f"Found {total} files\n")

    ok_count = 0
    err_total = 0
    node_total = 0
    type_agg = Counter()
    failed = []  # (rel, err_count, first_err_line)
    ext_stats = Counter()
    dir_stats = Counter()
    parse_ms_total = 0.0
    wall_start = time.time()

    for i, path in enumerate(files, 1):
        t0 = time.perf_counter()
        ok, errs, nodes, counts, err_line, _err = parse_one(path)
        parse_ms_total += (time.perf_counter() - t0) * 1000
        if ok:
            ok_count += 1
        else:
            err_total += errs
            rel = os.path.relpath(path, root).replace("\\", "/")
            failed.append((rel, errs, err_line))
        node_total += nodes
        type_agg.update(counts)
        ext_stats[os.path.splitext(path)[1].lower()] += 1
        parts = os.path.relpath(path, root).replace("\\", "/").split("/")
        if len(parts) > 1:
            dir_stats[parts[0]] += 1
        if i % 200 == 0:
            print(f"  {i}/{total}  (ok={ok_count}, err_files={i-ok_count})")

    wall = time.time() - wall_start

    print("\n" + "=" * 64)
    print("==== Python-binding coverage report ====")
    print("=" * 64)
    print(f"total_files:            {total}")
    print(f"parsed_ok:              {ok_count}  ({ok_count*100//total}%)")
    print(f"failed (has ERROR):     {total - ok_count}  ({(total-ok_count)*100//total}%)")
    print(f"total ERROR nodes:      {err_total}")
    print(f"total AST nodes:        {node_total}")
    print(f"ERROR rate:             {err_total*100.0/max(node_total,1):.2f}%")
    print()
    print(f"parse time (sum):       {parse_ms_total:.0f} ms")
    print(f"wall time:              {wall:.2f} s")
    print(f"avg per file:           {parse_ms_total/total:.2f} ms (parse only)")
    print(f"throughput:             {total/wall:.0f} files/s  ({node_total/wall:.0f} nodes/s)")
    print()
    print("==== By extension ====")
    for ext, c in ext_stats.most_common():
        print(f"  {ext}: {c}")
    print()
    print("==== By top-level dir ====")
    for d, c in dir_stats.most_common():
        print(f"  {d}/: {c}")
    print()
    print("==== Watched node types ====")
    for t in sorted(WATCH_TYPES, key=lambda x: -type_agg.get(x, 0)):
        v = type_agg.get(t, 0)
        if v:
            print(f"  {t:32s} {v}")
    print()
    failed.sort(key=lambda x: -x[1])
    print("==== Top 20 files by ERROR count ====")
    for rel, errs, line in failed[:20]:
        print(f"  {errs:5d}  {rel}  (first ERROR @ line {line})")
    print()
    print("==== Bottom 5 failed ====")
    for rel, errs, line in failed[-5:]:
        print(f"  {errs:5d}  {rel}  (first ERROR @ line {line})")


if __name__ == "__main__":
    main()
