#!/usr/bin/env python3
# coding: utf-8
"""全 pipeline 耗时基准：parse + extract，测当前阶段4 完整流程耗时。"""
import os
import sys
import time

sys.path.insert(0, '.')
from shaderbase.extract.nodes import NodeExtractor
from shaderbase.parser.tree_sitter_loader import parser as get_parser

ROOT = r'D:/douzhongjun/work/shader/shader-source'
SKIP = {'no_source', 'no_source_pc', 'pipeline_output', 'bin', '.git'}
EXTS = {'.nsf', '.hlsl', '.fxh'}

files = []
for dp, dns, fns in os.walk(ROOT):
    dns[:] = [d for d in dns if d not in SKIP]
    for f in fns:
        if os.path.splitext(f)[1].lower() in EXTS:
            files.append(os.path.join(dp, f))

ext = NodeExtractor()
parser = get_parser()
total_nodes = 0
t0 = time.perf_counter()
parse_ms = 0.0
extract_ms = 0.0
for p in files:
    with open(p, 'rb') as f:
        src = f.read()
    t1 = time.perf_counter()
    tree = parser.parse(src)
    parse_ms += (time.perf_counter() - t1) * 1000
    t2 = time.perf_counter()
    nodes = ext.extract_file(src, p.replace(os.sep, '/'))
    extract_ms += (time.perf_counter() - t2) * 1000
    total_nodes += len(nodes)
wall = time.perf_counter() - t0
print(f'files={len(files)}  nodes={total_nodes}')
print(f'parse   sum: {parse_ms:.0f} ms')
print(f'extract sum: {extract_ms:.0f} ms')
print(f'wall total : {wall*1000:.0f} ms  ({wall:.2f}s)')
print(f'per file   : {wall*1000/len(files):.2f} ms')
