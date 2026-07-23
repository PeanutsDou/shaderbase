#!/usr/bin/env python3
# coding: utf-8
"""边 fixture 比对脚本（DEV_PLAN §1.7.2 层 3 防退化）。

跑 EdgeExtractor 在每个 expected.yaml 对应的文件上，拿实际边列表跟期望比，
报 diff。AI 只对照不判断。

用法：
  py -3 scripts/check_edge_fixtures.py [shader_source_root]

输出：
  PASS / FAIL 计数 + 每个文件的 diff 详情

fixture 格式（test/fixtures/edges/<file>.expected.yaml）：
  input: <相对 shader-source 的路径>
  expected_edges:
    - kind: CALLS
      source: PixelNodesBasedGraph
      target: CalcWorldNormal
      line: 142
    - kind: IS_ENTRY_POINT
      source: TShader
      target: vs_main
      stage: vertex        # 可选，只在 IS_ENTRY_POINT 验
"""
import os
import sys
from collections import namedtuple

sys.path.insert(0, '.')
from shaderbase.extract.edges import EdgeExtractor
from shaderbase.extract.nodes import NodeExtractor

ROOT_DEFAULT = r'shader-source'   # 项目内子目录（相对项目根）

ExpectedEdge = namedtuple('ExpectedEdge', ['kind', 'source', 'target', 'line', 'extra'])


def parse_expected_yaml(path: str) -> tuple[str, list[ExpectedEdge]]:
    """解析 .expected.yaml，返回 (input_rel, [ExpectedEdge])。"""
    input_rel = None
    edges = []
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
            if stripped == 'expected_edges:':
                continue
            if stripped.startswith('- '):
                if cur:
                    edges.append(_dict_to_expected(cur))
                cur = {}
                rest = stripped[2:]
                if ':' in rest:
                    k, v = rest.split(':', 1)
                    cur[k.strip()] = v.strip()
                continue
            if cur is not None and ':' in stripped:
                k, v = stripped.split(':', 1)
                cur[k.strip()] = v.strip()
                continue
        if cur:
            edges.append(_dict_to_expected(cur))
    return input_rel or '', edges


def _dict_to_expected(d: dict) -> ExpectedEdge:
    extra = {k: v for k, v in d.items() if k not in ('kind', 'source', 'target', 'line')}
    if 'line' in d and d['line']:
        try:
            line = int(d['line'])
        except ValueError:
            line = 0
    else:
        line = 0
    return ExpectedEdge(
        kind=d.get('kind', ''),
        source=d.get('source'),
        target=d.get('target'),
        line=line,
        extra=extra,
    )


def edge_to_dict(e) -> dict:
    """把实际 Edge 转成跟 expected.yaml 同口径的 dict。"""
    d = {
        'kind': e.kind,
        'source': e.source_name or '',
        'target': e.target_name or '',
        'line': e.source_line,
    }
    # IS_ENTRY_POINT 的 stage
    if e.kind == 'IS_ENTRY_POINT' and e.properties.get('stage'):
        d['stage'] = e.properties['stage']
    return d


def compare(actual_edges: list, expected: list[ExpectedEdge]) -> list[str]:
    """比对，返回 diff 描述行列表（空 = 完全一致）。

    主键：(kind, source, target, line)。line=0 表示不验行号（宽松匹配）。
    """
    diffs = []
    actual_map = {}
    for e in actual_edges:
        a = edge_to_dict(e)
        key = (a['kind'], a['source'], a['target'], a['line'])
        actual_map[key] = a

    expected_map = {(e.kind, e.source or '', e.target or '', e.line): e for e in expected}

    # 缺失：期望有但实际没有
    for key, e in expected_map.items():
        if key in actual_map:
            continue
        # 宽松匹配：line=0 时按 (kind, source, target) 找
        if e.line == 0:
            loose_key = (e.kind, e.source or '', e.target or '')
            found = any(
                k[:3] == loose_key for k in actual_map
            )
            if found:
                continue
        diffs.append(f'  MISSING  {e.kind} {e.source} -> {e.target} @ L{e.line}')

    # 多出：实际有但期望没有（只在期望列表有 line>0 的严格项时才报 EXTRA，
    # 否则期望可能是抽样，报 EXTRA 噪音太大）
    has_strict = any(e.line > 0 for e in expected)
    if has_strict:
        for key, a in actual_map.items():
            if key in expected_map:
                continue
            # 宽松匹配
            loose_key = key[:3]
            if any(e.line == 0 and (e.kind, e.source or '', e.target or '') == loose_key
                   for e in expected):
                continue
            diffs.append(f'  EXTRA    {a["kind"]} {a["source"]} -> {a["target"]} @ L{a["line"]}')

    # 字段差异
    for key, e in expected_map.items():
        a = actual_map.get(key)
        if not a:
            # 试宽松匹配
            if e.line == 0:
                loose_key = (e.kind, e.source or '', e.target or '')
                for k, av in actual_map.items():
                    if k[:3] == loose_key:
                        a = av
                        break
        if not a:
            continue
        for ek, ev in e.extra.items():
            av = a.get(ek)
            if str(av) != str(ev):
                diffs.append(
                    f'  FIELD    {e.kind} {e.source} -> {e.target} @ L{e.line}: '
                    f'{ek} 期望={ev} 实际={av}'
                )
    return diffs


def main():
    shader_root = sys.argv[1] if len(sys.argv) > 1 else ROOT_DEFAULT
    # 解析成绝对路径（相对项目根）
    from shaderbase.store.connection import resolve_root_path
    shader_root = resolve_root_path(shader_root) if not os.path.isabs(shader_root) else shader_root
    repo_root = os.path.abspath('.')
    fixture_dir = os.path.join('test', 'fixtures', 'edges')
    if not os.path.isdir(fixture_dir):
        print(f'fixture 目录不存在: {fixture_dir}')
        return
    node_ext = NodeExtractor()
    ext = EdgeExtractor()

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
        candidates = [
            os.path.join(shader_root, input_rel),
            os.path.join(repo_root, input_rel),
        ]
        full = next((p for p in candidates if os.path.exists(p)), None)
        if not full:
            print(f'!! {yaml_path}: 文件不存在 (tried: {candidates})')
            fail_count += 1
            continue
        with open(full, 'rb') as f:
            src = f.read()
        nodes = node_ext.extract_file(src, full.replace("\\", "/"))
        actual = ext.extract_file(src, full.replace("\\", "/"), nodes=nodes)
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
            print(f'PASS  {name}  ({len(expected)} edges)')

    print(f'\n{"="*60}')
    print(f'PASS: {pass_count}   FAIL: {fail_count}   total_diffs: {total_diffs}')


if __name__ == '__main__':
    main()
