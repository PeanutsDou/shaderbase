#!/usr/bin/env python3
# coding: utf-8
"""跨文件 fixture 比对脚本（DEV_PLAN §1.7.2 层 4 防退化）。

验证 include 闭包 + CALLS 跨文件 resolve 的正确性。

用法：
  py -3 scripts/check_resolve_fixtures.py [shader_source_root]

fixture 格式（test/fixtures/resolve/<file>.expected.yaml）：
  input: pbr/pbr_rock.nsf
  expected_include_closure:
    - shaderlib/common.hlsl
    - pbr/nodes/pbr_rock_parameters.hlsl
  expected_resolved_calls:
    - caller: PixelNodesBasedGraph
      callee: CalcWorldNormal
      resolved_to: shaderlib/surface_functions_shared.hlsl
"""
import os
import sys

sys.path.insert(0, '.')

ROOT_DEFAULT = r'D:/douzhongjun/work/shader/shader-source'


def parse_expected_yaml(path: str) -> dict:
    """解析跨文件 fixture yaml。"""
    out = {'input': '', 'include_closure': [], 'resolved_calls': []}
    section = None
    cur: dict | None = None
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            stripped = line.strip()
            if stripped.startswith('#') or not stripped:
                continue
            if stripped.startswith('input:'):
                out['input'] = stripped.split(':', 1)[1].strip()
                continue
            if stripped == 'expected_include_closure:':
                section = 'closure'
                continue
            if stripped == 'expected_resolved_calls:':
                section = 'calls'
                continue
            if stripped.startswith('- '):
                val = stripped[2:].strip()
                if section == 'closure':
                    out['include_closure'].append(val)
                elif section == 'calls':
                    if cur:
                        out['resolved_calls'].append(cur)
                    cur = {}
                    # 解析第一项
                    if ':' in val:
                        k, v = val.split(':', 1)
                        cur[k.strip()] = v.strip()
                continue
            if cur is not None and ':' in stripped and section == 'calls':
                k, v = stripped.split(':', 1)
                cur[k.strip()] = v.strip()
                continue
        if cur and section == 'calls':
            out['resolved_calls'].append(cur)
    return out


def main():
    shader_root = sys.argv[1] if len(sys.argv) > 1 else ROOT_DEFAULT
    fixture_dir = os.path.join('test', 'fixtures', 'resolve')
    if not os.path.isdir(fixture_dir):
        print(f'fixture 目录不存在: {fixture_dir}')
        return

    # 需要 SQLite 连接 + 已建图
    import sqlite3
    from shaderbase.store.connection import connect
    from shaderbase.extract.resolve_calls import build_include_closure, build_function_index

    conn = connect('shaderbase.db')
    project = 'g66'

    # 构建 include 闭包（全量，只建一次）
    include_closure = build_include_closure(conn, project, shader_root)
    func_index = build_function_index(conn, project)

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
        exp = parse_expected_yaml(yaml_path)
        input_rel = exp['input']
        if not input_rel:
            print(f'!! {yaml_path}: 缺 input 字段')
            fail_count += 1
            continue
        full_path = os.path.join(shader_root, input_rel).replace("\\", "/")
        if not os.path.exists(full_path):
            print(f'!! {yaml_path}: 文件不存在 {full_path}')
            fail_count += 1
            continue

        diffs = []

        # 1. 验 include 闭包
        actual_closure = include_closure.get(full_path, set())
        # 期望的 include 路径是相对路径，转成 basename 集合比对
        actual_basenames = {os.path.basename(p) for p in actual_closure}
        for exp_inc in exp['include_closure']:
            exp_base = os.path.basename(exp_inc.replace("\\", "/"))
            if exp_base not in actual_basenames:
                # 宽松：路径后缀匹配
                found = any(p.replace("\\", "/").endswith(exp_inc.replace("\\", "/"))
                           for p in actual_closure)
                if not found:
                    diffs.append(f'  MISSING include: {exp_inc}')

        # 2. 验 resolved calls
        for exp_call in exp['resolved_calls']:
            caller = exp_call.get('caller', '')
            callee = exp_call.get('callee', '')
            exp_resolved = exp_call.get('resolved_to', '')
            # 查 CALLS 边
            cur = conn.execute(
                """SELECT properties FROM edges
                   WHERE project = ? AND kind = 'CALLS'
                   AND source_name = ? AND target_name = ?""",
                (project, caller, callee),
            )
            row = cur.fetchone()
            if not row:
                diffs.append(f'  MISSING call edge: {caller} -> {callee}')
                continue
            import json
            props = json.loads(row['properties']) if row['properties'] else {}
            actual_resolved = props.get('resolved_to_file', '')
            if exp_resolved and actual_resolved:
                # basename 比对
                if os.path.basename(exp_resolved) != os.path.basename(actual_resolved):
                    diffs.append(
                        f'  RESOLVE {caller} -> {callee}: '
                        f'期望 {exp_resolved} 实际 {actual_resolved}'
                    )

        name = os.path.basename(yaml_path).replace('.expected.yaml', '')
        if diffs:
            fail_count += 1
            total_diffs += len(diffs)
            print(f'FAIL  {name}  ({len(diffs)} diffs)')
            for d in diffs[:10]:
                print(d)
        else:
            pass_count += 1
            print(f'PASS  {name}  (closure={len(exp["include_closure"])} calls={len(exp["resolved_calls"])})')

    print(f'\n{"="*60}')
    print(f'PASS: {pass_count}   FAIL: {fail_count}   total_diffs: {total_diffs}')


if __name__ == '__main__':
    main()
