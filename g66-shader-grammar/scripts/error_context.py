#!/usr/bin/env python3
# coding: utf-8
"""
g66-shader-grammar/scripts/error_context.py

跑 tree-sitter parse，对每个失败文件提取所有 ERROR 节点的源码上下文。
输出每条 ERROR 的：文件路径、ERROR 行号、该行源码。
"""
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict


def find_shader_files(root):
    skip_dirs = {"no_source", "no_source_pc", "pipeline_output", "bin", ".git"}
    exts = {".nsf", ".hlsl", ".fxh"}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext in exts:
                files.append(os.path.join(dirpath, f))
    return sorted(files)


def parse_and_extract_errors(ts_exe, path):
    """跑 tree-sitter parse，提取 ERROR 节点的行列范围"""
    try:
        result = subprocess.run(
            [ts_exe, "parse", path],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        # 找所有 (ERROR [start_row, start_col] - [end_row, end_col])
        errors = []
        for m in re.finditer(r'\(ERROR \[(\d+),\s*(\d+)\]\s*-\s*\[(\d+),\s*(\d+)\]', output):
            start_row = int(m.group(1))
            start_col = int(m.group(2))
            end_row = int(m.group(3))
            end_col = int(m.group(4))
            errors.append((start_row, start_col, end_row, end_col))
        return errors
    except Exception:
        return []


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/error_context.py <shader_source_root> [--out <output_file>]")
        sys.exit(1)
    root = sys.argv[1]
    ts_exe = "./node_modules/tree-sitter-cli/tree-sitter.exe"
    out_file = None
    for i, arg in enumerate(sys.argv[2:], 2):
        if arg == "--out" and i + 1 < len(sys.argv):
            out_file = sys.argv[i + 1]

    grammar_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(grammar_dir)

    files = find_shader_files(root)
    total = len(files)
    print("Found {} files".format(total))

    # 收集所有 ERROR 上下文
    all_errors = []  # (rel_path, line_num, source_line)
    line_patterns = Counter()  # 源码行模式（归一化后）

    for i, path in enumerate(files, 1):
        errors = parse_and_extract_errors(ts_exe, path)
        if not errors:
            continue
        # 读源码
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        for (sr, sc, er, ec) in errors:
            # 拿 ERROR 起始行
            if sr < len(lines):
                src_line = lines[sr].rstrip("\n\r")
            else:
                src_line = "(out of range)"
            all_errors.append((rel, sr + 1, src_line))
        if i % 200 == 0:
            print("  {}/{}".format(i, total))

    # 按源码行模式归类
    # 归一化：去掉数字/字符串/标识符的具体值，看语法形态
    def normalize(line):
        # 去掉行首空白
        s = line.strip()
        # 替换数字
        s = re.sub(r'\b\d+\.?\d*[fFlLuU]*\b', 'N', s)
        # 替换字符串
        s = re.sub(r'"[^"]*"', '"S"', s)
        # 替换标识符（但保留关键字）
        keywords = {'if','else','for','while','return','void','float','float4','float3','float2',
                    'half','half4','half3','half2','int','uint','bool','true','false',
                    'struct','cbuffer','texture','SamplerState','technique','pass',
                    'const','static','uniform','inline','groupshared','in','out','inout',
                    'discard','break','continue','switch','case','default','do',
                    '#include','#define','#undef','#if','#ifdef','#ifndef','#elif','#else','#endif',
                    '#art','#excludefromtemptech','#pragma','#error','#warning',
                    'Texture2D','Texture3D','TextureCube','Texture2DArray',
                    'RWTexture2D','RWTexture3D','RWStructuredBuffer','StructuredBuffer',
                    'Buffer','ByteAddressBuffer','register','packoffset'}
        parts = re.split(r'(\b[a-zA-Z_]\w*\b)', s)
        result = []
        for p in parts:
            if re.match(r'\b[a-zA-Z_]\w*\b', p) and p not in keywords:
                result.append('ID')
            else:
                result.append(p)
        return ''.join(result)[:120]

    pattern_counts = Counter()
    pattern_samples = defaultdict(list)
    for (rel, line_num, src_line) in all_errors:
        norm = normalize(src_line)
        pattern_counts[norm] += 1
        if len(pattern_samples[norm]) < 3:
            pattern_samples[norm].append((rel, line_num, src_line))

    # 输出
    out_lines = []
    out_lines.append("=" * 70)
    out_lines.append("ERROR 上下文分类报告")
    out_lines.append("总 ERROR 数: {}".format(len(all_errors)))
    out_lines.append("失败文件数: {}".format(len(set(e[0] for e in all_errors))))
    out_lines.append("不同语法模式数: {}".format(len(pattern_counts)))
    out_lines.append("=" * 70)
    out_lines.append("")
    out_lines.append("==== 按语法模式分类（Top 50）====")
    for i, (norm, count) in enumerate(pattern_counts.most_common(50), 1):
        out_lines.append("")
        out_lines.append("{}. 模式出现 {} 次".format(i, count))
        out_lines.append("   归一化模式: {}".format(norm))
        out_lines.append("   样例:")
        for (rel, ln, src) in pattern_samples[norm]:
            # ASCII-safe
            src_safe = src.encode("ascii", "replace").decode("ascii")[:120]
            out_lines.append("     {}:{}: {}".format(rel, ln, src_safe))

    report = "\n".join(out_lines)
    print(report[:3000])  # 只打印前 3000 字符
    print("\n... (完整报告见输出文件)")

    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report)
        print("完整报告已写入: {}".format(out_file))


if __name__ == "__main__":
    main()
