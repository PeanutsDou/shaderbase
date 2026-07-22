#!/usr/bin/env python3
# coding: utf-8
"""按 FIXTURE_LIST 里的 50 个文件，跑抽取器生成 expected.yaml 候选。

DEV_PLAN §1.7.5 流程：AI 把实际输出当候选期望 → 用户 review 入库。
本脚本生成候选，人工 review 后入库。候选文件名带 `.candidate.yaml` 后缀，
review 通过后改名为 `.expected.yaml`。

用法：py -3 scripts/gen_fixture_candidates.py [shader_source_root]
"""
import os
import sys

sys.path.insert(0, '.')
from shaderbase.extract.nodes import NodeExtractor

# FIXTURE_LIST.md 里的 47 个文件（pbr_rock 三件套已有 expected.yaml）
FIXTURE_FILES = [
    'base/road_specular.nsf',
    'billboard/pbr_foliage_billboard.nsf',
    'common_cocosui/labeloutline.nsf',
    'common_pipeline/allocate_voxel_surfel_cluster.nsf',
    'common_pipeline/compact_cluster_surfel_data.nsf',
    'common_pipeline/gicommon.hlsl',
    'common_pipeline/makeup_v4_color.nsf',
    'common_shader/fresnel_noise_transparent_rt_output.nsf',
    'common_shader/skydome_v2_functions.hlsl',
    'hlod/hierarchicallod.nsf',
    'matcap/nodes/matcap_sand_nodes.hlsl',
    'meadow/meadow_base_v2_1_billboard.nsf',
    'pbr/nodes/crystal_functions.hlsl',
    'pbr/nodes/eye_functions.hlsl',
    'pbr/nodes/hair_functions.hlsl',
    'pbr/nodes/pbr_bluetide_parameters.hlsl',
    'pbr/nodes/pbr_carrier_parameters.hlsl',
    'pbr/nodes/pbr_monster_parameters.hlsl',
    'pbr/nodes/pbr_monster_va_parameters.hlsl',
    'pbr/nodes/pbr_volcano_parameters .hlsl',
    'pbr/nodes/skin_functions.hlsl',
    'pbr/pbr_default_volcano.nsf',
    'sfx/blend_highlight.nsf',
    'sfx/nodes/pbr_flow_water_parameters.hlsl',
    'sfx/nodes/uber_fx_common_input.hlsl',
    'sfx/nodes/uber_fx_common_multilayer_input.hlsl',
    'sfx/scanning_light_noise.nsf',
    'sfx/uber_flow_sparkles.nsf',
    'sfx/uber_fx_flowmap.nsf',
    'sfx/uber_fx_glitch_2d.nsf',
    'sfx/uber_mask_rim_diss.nsf',
    'sfx/uber_noise_mask.nsf',
    'sfx/uber_shoal_wave.nsf',
    'sfx/uber_water_circle.nsf',
    'sfx/uber_water_flow.nsf',
    'sfx/uber_water_wave.nsf',
    'sfx/wpo_fragment.nsf',
    'shaderlib/builtin_uniforms.hlsl',
    'shaderlib/ffx_a.fxh',
    'shaderlib/foliage_anim_functions.hlsl',
    'shaderlib/function.hlsl',
    'shaderlib/season_uniforms.hlsl',
    'shaderlib/shading_models.hlsl',
    'shaderlib/surface_functions.hlsl',
    'shaderlib/vat_bonebase.hlsl',
    'terrain/new_water_sea_fishing.nsf',
    'terrain/terrain_diffuse_common.hlsl',
    'terrain/terrain_water_common.hlsl',
    'test/test_svon.nsf',
    'ui/button_flow_o.nsf',
]


def node_to_yaml_lines(n, indent='  '):
    """把一个 ShaderNode 转成 expected.yaml 行（只保留稳定字段）。"""
    lines = [f'{indent}- kind: {n.kind}']
    lines.append(f'{indent}  name: {n.name}')
    lines.append(f'{indent}  line: {n.line}')
    p = n.properties
    if n.kind == 'Function':
        rt = p.get('return_type')
        if rt:
            lines.append(f'{indent}  return_type: {rt}')
        stage = p.get('stage')
        if stage:
            lines.append(f'{indent}  stage: {stage}')
        params = p.get('parameters', [])
        if params:
            lines.append(f'{indent}  param_count: {len(params)}')
        attrs = p.get('attributes')
        if attrs:
            lines.append(f'{indent}  attributes: {attrs}')
    elif n.kind == 'Struct':
        fields = p.get('fields', [])
        lines.append(f'{indent}  field_count: {len(fields)}')
    elif n.kind == 'Texture':
        lines.append(f'{indent}  texture_type: {p.get("texture_type")}')
        sem = p.get('semantic')
        if sem:
            lines.append(f'{indent}  semantic: {sem}')
        if p.get('annotation'):
            lines.append(f'{indent}  has_annotation: true')
    elif n.kind == 'SamplerState':
        states = p.get('states', [])
        lines.append(f'{indent}  state_count: {len(states)}')
        sem = p.get('semantic')
        if sem:
            lines.append(f'{indent}  semantic: {sem}')
    elif n.kind == 'Uniform':
        lines.append(f'{indent}  type: {p.get("type")}')
        if p.get('annotation'):
            lines.append(f'{indent}  has_annotation: true')
        if 'default' in p:
            lines.append(f'{indent}  default: {p["default"]}')
        if p.get('is_const'):
            lines.append(f'{indent}  is_const: true')
    elif n.kind == 'Technique':
        passes = p.get('passes', [])
        lines.append(f'{indent}  pass_count: {len(passes)}')
        if p.get('annotation'):
            lines.append(f'{indent}  has_annotation: true')
    elif n.kind == 'CBuffer':
        fields = p.get('fields', [])
        lines.append(f'{indent}  field_count: {len(fields)}')
        sem = p.get('semantic')
        if sem:
            lines.append(f'{indent}  semantic: {sem}')
    return lines


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else r'D:/douzhongjun/work/shader/shader-source'
    ext = NodeExtractor()
    out_dir = os.path.join('test', 'fixtures', 'nodes', 'candidates')
    os.makedirs(out_dir, exist_ok=True)

    ok = 0
    miss = []
    for rel in FIXTURE_FILES:
        # 文件名里的空格保留（pbr_volcano_parameters .hlsl 有空格）
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            miss.append(rel)
            continue
        nodes = ext.extract_path(full)
        # 候选文件名：把路径分隔符换成 __
        safe = rel.replace('/', '__').replace('\\', '__').replace(' ', '_')
        out_path = os.path.join(out_dir, safe + '.candidate.yaml')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f'# 候选 expected.yaml — 待 review 后改名为 .expected.yaml\n')
            f.write(f'# 生成自: {rel}\n')
            f.write(f'# 节点总数: {len(nodes)}\n')
            f.write(f'input: {rel}\n')
            f.write(f'\nexpected_nodes:\n')
            for n in nodes:
                f.write('\n'.join(node_to_yaml_lines(n)) + '\n')
        ok += 1

    print(f'生成候选: {ok}/{len(FIXTURE_FILES)}')
    if miss:
        print(f'缺失文件 ({len(miss)}):')
        for m in miss:
            print(f'  {m}')


if __name__ == '__main__':
    main()
