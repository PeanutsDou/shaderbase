#!/usr/bin/env python3
# coding: utf-8
"""PreprocessorView fixture 比对脚本。

跑 build_preprocessor_view 在每个 fixture 上，拿实际 line_active + branch_sigs
跟 .expected.yaml 比，报 diff。AI 只对照不判断。

用法：py -3 -m shaderbase.preprocessor.tests.check_pv_fixtures
"""
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, '.')

from shaderbase.parser.tree_sitter_loader import parser as get_parser
from shaderbase.preprocessor.interpreter import build_preprocessor_view


@dataclass
class ExpectedPV:
    name: str
    source: bytes
    defines: dict
    expected_line_active: list[bool]
    expected_branch_count: int   # 期望的 branch_merges 数量


def parse_fixture(path: str) -> ExpectedPV:
    """解析 fixture 文件。

    格式：
    ```
    ---defines---
    KEY=VALUE
    ---source---
    <shader 源码>
    ---expected_line_active---
    true,false,true,...
    ---expected_branch_count---
    1
    ```
    """
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    sections = {}
    cur_section = "header"
    cur_lines = []
    for line in content.splitlines():
        if line.strip().startswith("---") and line.strip().endswith("---"):
            if cur_lines:
                sections[cur_section] = cur_lines
            cur_section = line.strip().strip("-").strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines:
        sections[cur_section] = cur_lines

    defines = {}
    if "defines" in sections:
        for line in sections["defines"]:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    defines[k.strip()] = int(v.strip())
                except ValueError:
                    defines[k.strip()] = 1
            else:
                defines[line.strip()] = 1

    source = ""
    if "source" in sections:
        source = "\n".join(sections["source"])
    source_bytes = source.encode("utf-8")

    expected_line_active = []
    if "expected_line_active" in sections:
        line_str = "".join(sections["expected_line_active"]).strip()
        for tok in line_str.replace(" ", "").split(","):
            tok = tok.strip().lower()
            if tok in ("true", "1", "t"):
                expected_line_active.append(True)
            elif tok in ("false", "0", "f", ""):
                expected_line_active.append(False)

    expected_branch_count = 0
    if "expected_branch_count" in sections:
        try:
            expected_branch_count = int(sections["expected_branch_count"][0].strip())
        except (ValueError, IndexError):
            pass

    return ExpectedPV(
        name=os.path.basename(path).replace(".txt", ""),
        source=source_bytes,
        defines=defines,
        expected_line_active=expected_line_active,
        expected_branch_count=expected_branch_count,
    )


def main():
    fixture_dir = os.path.join(
        os.path.dirname(__file__), "fixtures"
    )
    if not os.path.isdir(fixture_dir):
        print(f"fixture 目录不存在: {fixture_dir}")
        return

    fixtures = [
        os.path.join(fixture_dir, f)
        for f in os.listdir(fixture_dir)
        if f.endswith(".txt")
    ]
    if not fixtures:
        print("没有 fixture")
        return

    parser = get_parser()
    pass_count = 0
    fail_count = 0
    total_diffs = 0

    for fpath in sorted(fixtures):
        exp = parse_fixture(fpath)
        tree = parser.parse(exp.source)
        view = build_preprocessor_view(tree.root_node, exp.source, exp.defines)

        diffs = []
        # 比对 line_active
        actual_la = view.line_active
        exp_la = exp.expected_line_active
        if len(actual_la) != len(exp_la):
            diffs.append(f"line_active 长度: 实际={len(actual_la)} 期望={len(exp_la)}")
        for i, (a, e) in enumerate(zip(actual_la, exp_la)):
            if a != e:
                diffs.append(f"L{i+1} active: 实际={a} 期望={e}")

        # 比对 branch_merges 数量
        actual_bm = len(view.branch_merges)
        if actual_bm != exp.expected_branch_count:
            diffs.append(f"branch_merges 数: 实际={actual_bm} 期望={exp.expected_branch_count}")

        if diffs:
            fail_count += 1
            total_diffs += len(diffs)
            print(f"FAIL  {exp.name}  ({len(diffs)} diffs)")
            for d in diffs[:5]:
                print(f"  {d}")
            if len(diffs) > 5:
                print(f"  ... 还有 {len(diffs)-5} 条")
        else:
            pass_count += 1
            print(f"PASS  {exp.name}  ({len(exp_la)} lines, {actual_bm} branches)")

    print(f"\n{'='*60}")
    print(f"PASS: {pass_count}   FAIL: {fail_count}   total_diffs: {total_diffs}")


if __name__ == "__main__":
    main()
