#!/usr/bin/env python3
# coding: utf-8
"""分析全库节点分布，输出 50 个代表性文件候选列表。

选文件原则（80/20 + 覆盖所有节点类型）：
- 每类节点挑 Top 3 代表文件（确保每类有 fixture）
- 各顶层目录都要有覆盖
- pbr_rock 三件套（教学版）单列
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, '.')
from shaderbase.extract.nodes import NodeExtractor

ROOT = r'D:/douzhongjun/work/shader/shader-source'
SKIP = {'no_source', 'no_source_pc', 'pipeline_output', 'bin', '.git'}
EXTS = {'.nsf', '.hlsl', '.fxh'}


def main():
    files = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP]
        for f in fns:
            if os.path.splitext(f)[1].lower() in EXTS:
                files.append(os.path.join(dp, f))

    ext = NodeExtractor()
    per_file = []
    for p in files:
        nodes = ext.extract_path(p)
        kinds = defaultdict(int)
        for n in nodes:
            kinds[n.kind] += 1
        rel = os.path.relpath(p, ROOT).replace(os.sep, '/')
        top = rel.split('/')[0]
        per_file.append((rel, top, len(nodes), dict(kinds)))

    # 1. 各顶层目录文件数
    by_dir = defaultdict(list)
    for rel, top, n, k in per_file:
        by_dir[top].append((rel, n, k))
    print('==== 各顶层目录文件数 ====')
    for d, lst in sorted(by_dir.items(), key=lambda x: -len(x[1])):
        print(f'  {d:20s} {len(lst)} files')

    # 2. 各类节点 Top 3 代表文件
    print('\n==== 各类节点 Top 3 代表文件 ====')
    for kind in ['Function', 'Struct', 'Texture', 'SamplerState', 'Uniform', 'Technique', 'CBuffer']:
        cand = [(rel, n, k.get(kind, 0)) for rel, top, n, k in per_file if k.get(kind, 0) > 0]
        cand.sort(key=lambda x: -x[2])
        print(f'  {kind}:')
        for rel, n, c in cand[:3]:
            print(f'    {c:4d}  {rel}')

    # 3. 挑 50 个：每类 Top 3 + 各目录代表 + pbr_rock
    selected = set()
    # 各类 Top 3
    for kind in ['Function', 'Struct', 'Texture', 'SamplerState', 'Uniform', 'Technique', 'CBuffer']:
        cand = [(rel, k.get(kind, 0)) for rel, top, n, k in per_file if k.get(kind, 0) > 0]
        cand.sort(key=lambda x: -x[1])
        for rel, c in cand[:3]:
            selected.add(rel)
    # 各目录挑节点最多的代表（确保目录覆盖）
    for d, lst in by_dir.items():
        lst.sort(key=lambda x: -x[1])
        if lst:
            selected.add(lst[0][0])
    # 补到 50 个：按节点总数从大到小补
    all_sorted = sorted(per_file, key=lambda x: -x[2])
    for rel, top, n, k in all_sorted:
        if len(selected) >= 50:
            break
        selected.add(rel)

    print(f'\n==== 选定 {len(selected)} 个 fixture 文件 ====')
    sel_list = sorted(selected)
    for rel in sel_list:
        # 找对应节点统计
        for r, top, n, k in per_file:
            if r == rel:
                kind_str = ', '.join(f'{kk}={vv}' for kk, vv in sorted(k.items()))
                print(f'  {rel:70s}  total={n:4d}  [{kind_str}]')
                break


if __name__ == '__main__':
    main()
