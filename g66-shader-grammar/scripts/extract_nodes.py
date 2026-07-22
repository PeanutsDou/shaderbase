#!/usr/bin/env python3
# coding: utf-8
"""
g66-shader-grammar/scripts/extract_nodes.py

AST 抽取器骨架：遍历 tree-sitter AST，抽出 Function/Struct/Uniform/Technique 等节点。
用法:
  py -3 scripts/extract_nodes.py <shader_file>

输出: JSON 格式的节点列表
"""
import json
import sys

import tree_sitter
import tree_sitter_g66_shader
from tree_sitter import Parser, Language


def create_parser():
    """创建 tree-sitter parser，加载 G66 shader grammar"""
    parser = Parser()
    parser.set_language(Language(tree_sitter_g66_shader.language()))
    return parser


def walk_ast(node, results, source_bytes, file_path="<inline>"):
    """递归遍历 AST，抽取节点"""
    # function_definition
    if node.type == "function_definition":
        name = None
        return_type = None
        params = []
        body_start = None
        body_end = None
        for child in node.children:
            if child.type == "function_declarator":
                for sub in child.children:
                    if sub.type == "identifier":
                        name = sub.text.decode("utf-8", errors="replace")
                    elif sub.type == "parameter_list":
                        params = _extract_params(sub)
            elif child.type == "type_identifier" or child.type == "primitive_type":
                return_type = child.text.decode("utf-8", errors="replace")
            elif child.type == "compound_statement":
                body_start = child.start_point[0]
                body_end = child.end_point[0]
        if name:
            results.append({
                "kind": "Function",
                "name": name,
                "return_type": return_type,
                "params": params,
                "file": file_path,
                "line": node.start_point[0] + 1,
                "body_start_line": body_start + 1 if body_start is not None else None,
                "body_end_line": body_end + 1 if body_end is not None else None,
            })

    # struct_specifier
    elif node.type == "struct_specifier":
        name = None
        fields = []
        for child in node.children:
            if child.type == "type_identifier":
                name = child.text.decode("utf-8", errors="replace")
            elif child.type == "field_declaration_list":
                for field_child in child.children:
                    if field_child.type == "field_declaration":
                        field_info = _extract_field(field_child)
                        if field_info:
                            fields.append(field_info)
        if name:
            results.append({
                "kind": "Struct",
                "name": name,
                "fields": fields,
                "file": file_path,
                "line": node.start_point[0] + 1,
            })

    # declaration（变量/uniform 声明）
    elif node.type == "declaration":
        info = _extract_declaration(node, source_bytes)
        if info and info.get("name"):
            results.append({
                "kind": "Variable",
                "name": info["name"],
                "type": info.get("type", ""),
                "file": file_path,
                "line": node.start_point[0] + 1,
            })

    # technique_block
    elif node.type == "technique_block":
        name = None
        passes = []
        for child in node.children:
            if child.type == "identifier" and not name:
                name = child.text.decode("utf-8", errors="replace")
            elif child.type == "pass_block":
                pass_info = _extract_pass(child)
                if pass_info:
                    passes.append(pass_info)
        if name:
            results.append({
                "kind": "Technique",
                "name": name,
                "passes": passes,
                "file": file_path,
                "line": node.start_point[0] + 1,
            })

    # metadata_block (annotation)
    elif node.type == "metadata_block":
        assignments = []
        for child in node.children:
            if child.type == "metadata_assignment":
                key = None
                value = None
                for sub in child.children:
                    if sub.type == "identifier":
                        if key is None:
                            key = sub.text.decode("utf-8", errors="replace")
                        else:
                            value = sub.text.decode("utf-8", errors="replace")
                    elif sub.type == "string_literal":
                        value = sub.text.decode("utf-8", errors="replace")
                    elif sub.type == "number_literal":
                        value = sub.text.decode("utf-8", errors="replace")
                if key:
                    assignments.append({"key": key, "value": value})
        if assignments:
            results.append({
                "kind": "Annotation",
                "assignments": assignments,
                "file": file_path,
                "line": node.start_point[0] + 1,
            })

    # preproc_art_directive
    elif node.type == "preproc_art_directive":
        name = None
        art_type = None
        for child in node.children:
            if child.type == "identifier" and not name:
                name = child.text.decode("utf-8", errors="replace")
            elif child.type == "string_literal":
                if art_type is None:
                    art_type = child.text.decode("utf-8", errors="replace")
        if name:
            results.append({
                "kind": "ArtMacro",
                "name": name,
                "art_type": art_type,
                "file": file_path,
                "line": node.start_point[0] + 1,
            })

    # call_expression（函数调用）
    elif node.type == "call_expression":
        callee = None
        for child in node.children:
            if child.type == "identifier":
                callee = child.text.decode("utf-8", errors="replace")
                break
            elif child.type == "field_expression":
                # tex.Sample -> 取最后一个 identifier
                for sub in child.children:
                    if sub.type == "identifier":
                        callee = sub.text.decode("utf-8", errors="replace")
        if callee:
            results.append({
                "kind": "CallExpression",
                "callee": callee,
                "file": file_path,
                "line": node.start_point[0] + 1,
            })

    # 递归遍历子节点
    for child in node.children:
        walk_ast(child, results, source_bytes, file_path)


def _extract_params(param_list_node):
    """从 parameter_list 抽参数"""
    params = []
    for child in param_list_node.children:
        if child.type == "parameter_declaration":
            param = {}
            for sub in child.children:
                if sub.type == "type_identifier" or sub.type == "primitive_type":
                    param["type"] = sub.text.decode("utf-8", errors="replace")
                elif sub.type == "identifier":
                    param["name"] = sub.text.decode("utf-8", errors="replace")
            if param.get("name"):
                params.append(param)
    return params


def _extract_field(field_node):
    """从 field_declaration 抽字段"""
    field = {}
    for child in field_node.children:
        if child.type == "type_identifier" or child.type == "primitive_type":
            field["type"] = child.text.decode("utf-8", errors="replace")
        elif child.type == "field_identifier" or child.type == "identifier":
            field["name"] = child.text.decode("utf-8", errors="replace")
    return field if field.get("name") else None


def _extract_declaration(decl_node, source_bytes):
    """从 declaration 抽变量信息"""
    info = {}
    for child in decl_node.children:
        if child.type == "type_identifier" or child.type == "primitive_type":
            info["type"] = child.text.decode("utf-8", errors="replace")
        elif child.type == "identifier" and "name" not in info:
            info["name"] = child.text.decode("utf-8", errors="replace")
    return info


def _extract_pass(pass_node):
    """从 pass_block 抽 pass 信息"""
    info = {}
    for child in pass_node.children:
        if child.type == "identifier" and "name" not in info:
            info["name"] = child.text.decode("utf-8", errors="replace")
        elif child.type == "state_assignment":
            if "states" not in info:
                info["states"] = []
            state = {}
            for sub in child.children:
                if sub.type == "identifier":
                    if "name" not in state:
                        state["name"] = sub.text.decode("utf-8", errors="replace")
                    else:
                        state["value"] = sub.text.decode("utf-8", errors="replace")
            if state.get("name"):
                info["states"].append(state)
    return info if info.get("name") else None


def extract_from_file(file_path):
    """解析一个 shader 文件，返回抽取的节点列表"""
    with open(file_path, "rb") as f:
        source = f.read()

    parser = create_parser()
    tree = parser.parse(source)
    results = []
    walk_ast(tree.root_node, results, source, file_path)
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: py -3 scripts/extract_nodes.py <shader_file>")
        sys.exit(1)

    file_path = sys.argv[1]
    nodes = extract_from_file(file_path)

    # 统计
    stats = {}
    for n in nodes:
        stats[n["kind"]] = stats.get(n["kind"], 0) + 1

    print(f"File: {file_path}")
    print(f"Total nodes: {len(nodes)}")
    print(f"By kind: {stats}")
    print()

    # 打印前 20 个节点
    for n in nodes[:20]:
        if n["kind"] == "Function":
            print(f"  [Function] {n['return_type']} {n['name']}({len(n['params'])} params) @ line {n['line']}")
        elif n["kind"] == "Struct":
            print(f"  [Struct] {n['name']} ({len(n['fields'])} fields) @ line {n['line']}")
        elif n["kind"] == "Technique":
            print(f"  [Technique] {n['name']} ({len(n['passes'])} passes) @ line {n['line']}")
        elif n["kind"] == "Variable":
            print(f"  [Variable] {n['type']} {n['name']} @ line {n['line']}")
        elif n["kind"] == "ArtMacro":
            print(f"  [ArtMacro] {n['name']} = {n.get('art_type', '')} @ line {n['line']}")
        elif n["kind"] == "CallExpression":
            print(f"  [Call] {n['callee']}() @ line {n['line']}")
        elif n["kind"] == "Annotation":
            keys = [a["key"] for a in n["assignments"]]
            print(f"  [Annotation] <{', '.join(keys[:3])}...> @ line {n['line']}")

    if len(nodes) > 20:
        print(f"  ... and {len(nodes) - 20} more nodes")

    # 可选：输出完整 JSON
    if "--json" in sys.argv:
        print("\n--- JSON ---")
        print(json.dumps(nodes, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
