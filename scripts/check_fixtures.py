#!/usr/bin/env python3
# coding: utf-8
"""fixture 比对脚本（DEV_PLAN §1.7.2 层 2 防退化核心）。

跑 NodeExtractor 在每个 expected.yaml 对应的文件上，拿实际节点列表跟期望比，
报 diff。AI 只对照不判断——diff 几秒出结果，人工决定改 grammar 还是改期望。

用法：
  py -3 scripts/check_fixtures.py [shader_source_root]

输出：
  PASS / FAIL 计数 + 每个文件的 diff 详情
"""
import os
import sys
from collections import namedtuple

# 简易 YAML 解析（不引 PyYAML 依赖）。fixture 格式固定，能解析就行。
# 候选 .candidate.yaml 不参与比对，只比 .expected.yaml。

sys.path.insert(0, '.')
from shaderbase.extract.nodes import NodeExtractor

ROOT_DEFAULT = r'shader-source'   # 项目内子目录（相对项目根）


ExpectedNode = namedtuple('ExpectedNode', ['kind', 'name', 'line', 'extra'])


def parse_expected_yaml(path: str) -> tuple[str, list[ExpectedNode]]:
    """解析 .expected.yaml，返回 (input_rel, [ExpectedNode])。"""
    input_rel = None
    nodes = []
    cur: dict | None = None
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            stripped = line.strip()
            if stripped.startswith('#') or not stripped:
                continue
            if stripped.startswith('input:'):
                input_rel = stripped.split(':', 1)[1].strip()
                continue
            if stripped == 'expected_nodes:':
                continue
            # 列表项
            if stripped.startswith('- '):
                if cur:
                    nodes.append(_dict_to_expected(cur))
                cur = {}
                # 解析第一项
                rest = stripped[2:]
                if ':' in rest:
                    k, v = rest.split(':', 1)
                    cur[k.strip()] = v.strip()
                continue
            # 续行（缩进的 key: value）
            if cur is not None and ':' in stripped:
                k, v = stripped.split(':', 1)
                cur[k.strip()] = v.strip()
                continue
        if cur:
            nodes.append(_dict_to_expected(cur))
    return input_rel or '', nodes


def _dict_to_expected(d: dict) -> ExpectedNode:
    extra = {k: v for k, v in d.items() if k not in ('kind', 'name', 'line')}
    # 类型转换
    if 'line' in d:
        d = {**d, 'line': int(d['line'])}
    return ExpectedNode(
        kind=d.get('kind', ''),
        name=d.get('name'),
        line=int(d.get('line', 0)) if d.get('line') else 0,
        extra=extra,
    )


def actual_to_expected(n) -> dict:
    """把实际 ShaderNode 转成跟 expected.yaml 同口径的 dict。"""
    d = {'kind': n.kind, 'name': n.name, 'line': n.line}
    p = n.properties
    if n.kind == 'Function':
        if p.get('return_type'):
            d['return_type'] = p['return_type']
        if p.get('stage'):
            d['stage'] = p['stage']
        if p.get('parameters'):
            d['param_count'] = str(len(p['parameters']))
        if p.get('attributes'):
            d['attributes'] = str(p['attributes'])
    elif n.kind == 'Struct':
        d['field_count'] = str(len(p.get('fields', [])))
    elif n.kind == 'Texture':
        d['texture_type'] = p.get('texture_type', '')
        if p.get('semantic'):
            d['semantic'] = p['semantic']
        if p.get('annotation'):
            d['has_annotation'] = 'true'
    elif n.kind == 'SamplerState':
        d['state_count'] = str(len(p.get('states', [])))
        if p.get('semantic'):
            d['semantic'] = p['semantic']
    elif n.kind == 'Uniform':
        d['type'] = p.get('type', '')
        if p.get('annotation'):
            d['has_annotation'] = 'true'
        if 'default' in p:
            d['default'] = str(p['default'])
        if p.get('is_const'):
            d['is_const'] = 'true'
    elif n.kind == 'Technique':
        d['pass_count'] = str(len(p.get('passes', [])))
        if p.get('annotation'):
            d['has_annotation'] = 'true'
    elif n.kind == 'CBuffer':
        d['field_count'] = str(len(p.get('fields', [])))
        if p.get('semantic'):
            d['semantic'] = p['semantic']
    return d


def compare(actual_nodes: list, expected: list[ExpectedNode]) -> list[str]:
    """比对，返回 diff 描述行列表（空 = 完全一致）。"""
    diffs = []
    # 用 (kind, name, line) 做主键比对
    actual_map = {}
    for n in actual_nodes:
        a = actual_to_expected(n)
        key = (a['kind'], a['name'], a['line'])
        actual_map[key] = a

    expected_map = {(e.kind, e.name, e.line): e for e in expected}

    # 缺失：期望有但实际没有
    for key, e in expected_map.items():
        if key not in actual_map:
            diffs.append(f'  MISSING  {e.kind} {e.name} @ L{e.line}')

    # 多出：实际有但期望没有
    for key, a in actual_map.items():
        if key not in expected_map:
            diffs.append(f'  EXTRA    {a["kind"]} {a["name"]} @ L{a["line"]}')

    # 字段差异：同主键但字段值不同
    for key, e in expected_map.items():
        a = actual_map.get(key)
        if not a:
            continue
        for ek, ev in e.extra.items():
            av = a.get(ek)
            if str(av) != str(ev):
                diffs.append(f'  FIELD    {e.kind} {e.name} @ L{e.line}: {ek} 期望={ev} 实际={av}')
    return diffs


def main():
    shader_root = sys.argv[1] if len(sys.argv) > 1 else ROOT_DEFAULT
    # 解析成绝对路径（相对项目根）
    from shaderbase.store.connection import resolve_root_path
    shader_root = resolve_root_path(shader_root) if not os.path.isabs(shader_root) else shader_root
    repo_root = os.path.abspath('.')
    fixture_dir = os.path.join('test', 'fixtures', 'nodes')
    ext = NodeExtractor()

    yaml_files = [
        os.path.join(fixture_dir, f)
        for f in os.listdir(fixture_dir)
        if f.endswith('.expected.yaml')
    ]
    if not yaml_files:
        print('没有 .expected.yaml 文件可比对')
        return

    pass_count = 0
    fail_count = 0
    total_diffs = 0

    for yaml_path in sorted(yaml_files):
        input_rel, expected = parse_expected_yaml(yaml_path)
        if not input_rel:
            print(f'!! {yaml_path}: 缺 input 字段')
            fail_count += 1
            continue
        # 优先在 shader-source 找，再在仓库本地找（pbr_rock 教学版等）
        candidates = [
            os.path.join(shader_root, input_rel),
            os.path.join(repo_root, input_rel),
        ]
        full = next((p for p in candidates if os.path.exists(p)), None)
        if not full:
            print(f'!! {yaml_path}: 文件不存在 (tried: {candidates})')
            fail_count += 1
            continue
        actual = ext.extract_path(full)
        diffs = compare(actual, expected)
        name = os.path.basename(yaml_path).replace('.expected.yaml', '')
        if diffs:
            fail_count += 1
            total_diffs += len(diffs)
            print(f'FAIL  {name}  ({len(diffs)} diffs)')
            for d in diffs[:10]:
                print(d)
            if len(diffs) > 10:
                print(f'  ... 还有 {len(diffs)-10} 条')
        else:
            pass_count += 1
            print(f'PASS  {name}  ({len(expected)} nodes)')

    print(f'\n{"="*60}')
    print(f'PASS: {pass_count}   FAIL: {fail_count}   total_diffs: {total_diffs}')


if __name__ == '__main__':
    main()
