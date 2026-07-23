#!/usr/bin/env python3
# coding: utf-8
"""按选定文件生成边 fixture 候选 .expected.yaml。

DEV_PLAN §1.7.5 流程：AI 把实际输出当候选期望 → 用户 review 入库。
本脚本生成候选，人工 review 后入库。候选文件名带 `.candidate.yaml` 后缀，
review 通过后改名为 `.expected.yaml`。

用法：py -3 scripts/gen_edge_fixture_candidates.py [shader_source_root]
"""
import os
import sys

sys.path.insert(0, '.')
from shaderbase.extract.edges import EdgeExtractor
from shaderbase.extract.nodes import NodeExtractor

ROOT_DEFAULT = r'D:/douzhongjun/work/shader/shader-source'

# 10 个代表性文件（覆盖各种边类型）
FIXTURE_FILES = [
    'base/animated_grass.nsf',
    'base/road_specular.nsf',
    'pbr/pbr_default_volcano.nsf',
    'sfx/blend_highlight.nsf',
    'billboard/pbr_foliage_billboard.nsf',
    'meadow/meadow_base_v2_1_billboard.nsf',
    'hlod/hierarchicallod.nsf',
    'common_pipeline/gicommon.hlsl',
    'matcap/nodes/matcap_sand_nodes.hlsl',
    'common_shader/skydome_v2_functions.hlsl',
]


def gen_candidate(rel: str, shader_root: str, out_dir: str):
    fp = os.path.join(shader_root, rel)
    if not os.path.exists(fp):
        print(f'MISS: {rel}')
        return
    with open(fp, 'rb') as f:
        src = f.read()
    node_ext = NodeExtractor()
    ext = EdgeExtractor()
    nodes = node_ext.extract_file(src, fp.replace("\\", "/"))
    edges = ext.extract_file(src, fp.replace("\\", "/"), nodes=nodes)

    # 文件名：目录__文件名（跟节点 fixture 一致）
    safe_name = rel.replace('/', '__').replace('\\', '__').replace(' ', '_')
    out_path = os.path.join(out_dir, safe_name + '.candidate.yaml')

    lines = [f'# {rel} 的边抽取期望候选', f'input: {rel}', '', 'expected_edges:']
    for e in edges:
        line_str = f'  - kind: {e.kind}'
        line_str += f'\n    source: {e.source_name or ""}'
        line_str += f'\n    target: {e.target_name or ""}'
        line_str += f'\n    line: {e.source_line}'
        if e.kind == 'IS_ENTRY_POINT' and e.properties.get('stage'):
            line_str += f'\n    stage: {e.properties["stage"]}'
        lines.append(line_str)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'WROTE: {out_path}  ({len(edges)} edges)')


def main():
    shader_root = sys.argv[1] if len(sys.argv) > 1 else ROOT_DEFAULT
    out_dir = os.path.join('test', 'fixtures', 'edges')
    os.makedirs(out_dir, exist_ok=True)
    for rel in FIXTURE_FILES:
        gen_candidate(rel, shader_root, out_dir)


if __name__ == '__main__':
    main()
