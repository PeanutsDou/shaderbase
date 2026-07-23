"""edges — AST → 知识库边抽取。

遍历 AST 找关系，建边。每条边带 conditional_signature（PV 算）。

边类型（DEV_PLAN §3.2）：
  - INCLUDES: file A #include file B → 文件间依赖
  - CALLS: function A 的函数体里调了 function B → 调用关系（跨文件 resolve）
  - HAS_MEMBER: struct → field
  - DECLARES_UNIFORM: file → uniform 声明（带 metadata_block 或 static const）
  - USES_UNIFORM: function → uniform 使用（函数体里引用了 uniform 名）
  - FLOWS_TO: struct field → semantic（VS 输出 / PS 输入的 semantic 绑定）
  - IS_ENTRY_POINT: technique → vs_main/ps_main
  - EXPOSES_TECHNIQUE: file → technique
  - CONDITIONAL_ON: #art 开关声明（标记条件编译入口）
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from tree_sitter import Node

from ..parser.ast_utils import find_by_type, first_child, text_of, walk
from ..parser.tree_sitter_loader import parser as get_parser
from ..preprocessor.branch_signature import branch_signature_key, branch_family_key
from ..preprocessor.interpreter import build_preprocessor_view


@dataclass
class Edge:
    """一条知识库边。"""
    kind: str
    source_file: str
    source_line: int
    source_name: Optional[str]
    target_name: Optional[str]
    target_file: Optional[str] = None   # CALLS resolve 后填
    properties: dict = field(default_factory=dict)
    conditional_signature: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_name": self.source_name,
            "target_name": self.target_name,
            "target_file": self.target_file,
            "properties": self.properties,
            "conditional_signature": self.conditional_signature,
        }


class EdgeExtractor:
    """遍历 AST 抽边。

    某些边类型（USES_UNIFORM）需要先知道本文件声明了哪些 Uniform 节点名，
    所以 extract_file 入口先用 NodeExtractor 抽一遍节点列表，再抽边。
    外部传 nodes 列表可复用（避免跟 indexer 重复抽）。
    """

    def __init__(self, parser=None):
        self._parser = parser or get_parser()

    def extract_file(
        self, source: bytes, file_path: str, pv_view=None,
        nodes: Optional[list] = None,
    ) -> list[Edge]:
        """抽一个文件的所有边。

        pv_view: PreprocessorView（算 conditional_signature 用）
                没传就现场算（空 defines，索引阶段）
        nodes: 本文件的 ShaderNode 列表（给 USES_UNIFORM 用）。
               没传就现场抽一遍（复用 NodeExtractor）。
        """
        tree = self._parser.parse(source)
        if pv_view is None:
            pv_view = build_preprocessor_view(tree.root_node, source, {})

        # 收集本文件的 Uniform 名字集合（给 USES_UNIFORM 匹配用）
        if nodes is None:
            from .nodes import NodeExtractor
            nodes = NodeExtractor(self._parser).extract_file(source, file_path)
        uniform_names = {
            n.name for n in nodes if n.kind == "Uniform" and n.name
        }

        edges: list[Edge] = []
        for node in walk(tree.root_node):
            edges.extend(self._extract_node_edges(
                node, file_path, pv_view, uniform_names,
            ))
        return edges

    def extract_path(self, abs_path: str, pv_view=None) -> list[Edge]:
        with open(abs_path, "rb") as f:
            src = f.read()
        return self.extract_file(src, abs_path.replace("\\", "/"), pv_view)

    def _sig_at(self, pv_view, line: int) -> Optional[str]:
        """取某行的条件签名。"""
        idx = line - 1
        if 0 <= idx < len(pv_view.branch_sigs):
            sig = pv_view.branch_sigs[idx]
            return branch_signature_key(sig) if sig else None
        return None

    def _extract_node_edges(
        self, node: Node, file_path: str, pv_view,
        uniform_names: Optional[set] = None,
    ) -> list[Edge]:
        t = node.type
        if t == "preproc_include":
            return self._extract_includes(node, file_path, pv_view)
        if t == "call_expression":
            return self._extract_calls(node, file_path, pv_view)
        if t == "struct_specifier":
            return self._extract_struct_members(node, file_path, pv_view)
        if t == "technique_block":
            return self._extract_technique_edges(node, file_path, pv_view)
        if t == "preproc_art_directive":
            return self._extract_art_edges(node, file_path, pv_view)
        if t == "declaration" and uniform_names:
            return self._extract_declares_uniform(
                node, file_path, pv_view, uniform_names,
            )
        if t == "function_definition" and uniform_names:
            return self._extract_uses_uniform(
                node, file_path, pv_view, uniform_names,
            )
        return []

    # ---- INCLUDES ----

    def _extract_includes(
        self, node: Node, file_path: str, pv_view,
    ) -> list[Edge]:
        """#include "path" → INCLUDES 边。"""
        target = None
        for child in node.children:
            if child.type == "string_literal":
                txt = text_of(child)
                if len(txt) >= 2 and txt[0] == '"':
                    target = txt[1:-1]
                break
        if not target:
            return []
        line = node.start_point[0] + 1
        return [Edge(
            kind="INCLUDES",
            source_file=file_path,
            source_line=line,
            source_name=None,
            target_name=target,
            conditional_signature=self._sig_at(pv_view, line),
        )]

    # ---- CALLS ----

    def _extract_calls(
        self, node: Node, file_path: str, pv_view,
    ) -> list[Edge]:
        """call_expression → CALLS 边。

        AST: call_expression
          callee: identifier | field_expression
          argument_list
        """
        callee = None
        for child in node.children:
            if child.type == "identifier":
                callee = text_of(child)
                break
            if child.type == "field_expression":
                callee = text_of(child)
                break
        if not callee:
            return []
        # 排除类型构造（float4(...) / int3(...) 等）
        TYPE_CTOR = {
            "float", "float2", "float3", "float4", "float2x2", "float3x3", "float4x4",
            "int", "int2", "int3", "int4", "uint", "uint2", "uint3", "uint4",
            "half", "half2", "half3", "half4", "bool", "bool2", "bool3", "bool4",
            "double", "double2", "double3", "double4",
        }
        if callee in TYPE_CTOR:
            return []
        line = node.start_point[0] + 1
        return [Edge(
            kind="CALLS",
            source_file=file_path,
            source_line=line,
            source_name=None,   # 后续填（需要找所在函数）
            target_name=callee,
            conditional_signature=self._sig_at(pv_view, line),
        )]

    # ---- HAS_MEMBER + FLOWS_TO ----

    def _extract_struct_members(
        self, node: Node, file_path: str, pv_view,
    ) -> list[Edge]:
        """struct → field → HAS_MEMBER 边；field 带 semantic → FLOWS_TO 边。

        FLOWS_TO 把"struct_name.field : SEMANTIC"三元组记下来，
        查询层按 semantic 名匹配 VS 输出 struct 和 PS 输入 struct。
        stage 字段不在这里判（VS/PS 由 struct 名约定或查询层推断），
        只记 raw semantic 文本。
        """
        struct_name_node = first_child(node, "type_identifier")
        struct_name = text_of(struct_name_node) if struct_name_node else None
        if not struct_name:
            return []
        edges = []
        fdl = first_child(node, "field_declaration_list")
        if not fdl:
            return edges
        for child in fdl.children:
            if child.type != "field_declaration":
                continue
            fname = None
            ftype = None
            for cc in child.children:
                if cc.type in ("field_identifier", "identifier"):
                    fname = text_of(cc)
                elif cc.type in ("type_identifier", "primitive_type"):
                    ftype = text_of(cc)
            if fname:
                line = child.start_point[0] + 1
                edges.append(Edge(
                    kind="HAS_MEMBER",
                    source_file=file_path,
                    source_line=line,
                    source_name=struct_name,
                    target_name=fname,
                    conditional_signature=self._sig_at(pv_view, line),
                ))
            # semantic → FLOWS_TO
            sem = self._field_semantic(child)
            if sem and fname:
                line = child.start_point[0] + 1
                edges.append(Edge(
                    kind="FLOWS_TO",
                    source_file=file_path,
                    source_line=line,
                    source_name=struct_name,
                    target_name=sem,
                    properties={
                        "field": fname,
                        "field_type": ftype,
                        "semantic": sem,
                    },
                    conditional_signature=self._sig_at(pv_view, line),
                ))
        return edges

    def _field_semantic(self, field_node: Node) -> Optional[str]:
        """取 field_declaration 的 semantic 文本（去 ':'）。

        struct field 的 `: TEXCOORD0` 在 CPP 上游 grammar 里走 bitfield_clause
        （不是 semantics，semantics 规则只挂在 declaration/function/parameter）。
        两种形态都认：semantics / bitfield_clause。
        """
        sem = first_child(field_node, "semantics")
        if sem is None:
            sem = first_child(field_node, "bitfield_clause")
        if not sem:
            return None
        txt = text_of(sem).strip()
        if txt.startswith(":"):
            txt = txt[1:].strip()
        return txt or None

    # ---- ENTRY_POINT + EXPOSES_TECHNIQUE ----

    def _extract_technique_edges(
        self, node: Node, file_path: str, pv_view,
    ) -> list[Edge]:
        """technique → pass → VertexShader/PixelShader → IS_ENTRY_POINT 边。"""
        tech_name = None
        for child in node.children:
            if child.type == "identifier":
                tech_name = text_of(child)
                break
        if not tech_name:
            return []
        edges = []
        line = node.start_point[0] + 1
        # EXPOSES_TECHNIQUE: file → technique
        edges.append(Edge(
            kind="EXPOSES_TECHNIQUE",
            source_file=file_path,
            source_line=line,
            source_name=None,
            target_name=tech_name,
            conditional_signature=self._sig_at(pv_view, line),
        ))
        # 找 pass 里的 VertexShader/PixelShader
        for child in node.children:
            if child.type != "pass_block":
                continue
            for sub in child.children:
                if sub.type != "state_assignment":
                    continue
                state_name = None
                state_val = None
                for sc in sub.children:
                    if sc.type == "identifier":
                        if state_name is None:
                            state_name = text_of(sc)
                        else:
                            state_val = text_of(sc)
                if state_name in ("VertexShader", "PixelShader") and state_val:
                    sl = sub.start_point[0] + 1
                    edges.append(Edge(
                        kind="IS_ENTRY_POINT",
                        source_file=file_path,
                        source_line=sl,
                        source_name=tech_name,
                        target_name=state_val,
                        properties={"stage": "vertex" if state_name == "VertexShader" else "pixel"},
                        conditional_signature=self._sig_at(pv_view, sl),
                    ))
        return edges

    # ---- DECLARES_UNIFORM ----

    def _extract_declares_uniform(
        self, node: Node, file_path: str, pv_view,
        uniform_names: set,
    ) -> list[Edge]:
        """declaration 带 metadata_block 或 static const 的 → DECLARES_UNIFORM 边。

        source_name=None（代表 file）→ target_name=uniform 名。
        只对实际是 Uniform 的声明建边（跟 NodeExtractor 的 Uniform 判定对齐）。
        """
        # 带 metadata_block 的 declaration
        has_meta = first_child(node, "metadata_block") is not None
        is_static = any(c.type == "storage_class_specifier" for c in node.children)
        is_const = any(
            c.type == "type_qualifier" and "const" in text_of(c)
            for c in node.children
        )
        if not (has_meta or (is_static and is_const)):
            return []
        edges = []
        line = node.start_point[0] + 1
        for c in node.children:
            if c.type != "identifier":
                continue
            name = text_of(c)
            if name in uniform_names:
                edges.append(Edge(
                    kind="DECLARES_UNIFORM",
                    source_file=file_path,
                    source_line=line,
                    source_name=None,    # file-level
                    target_name=name,
                    conditional_signature=self._sig_at(pv_view, line),
                ))
        return edges

    # ---- USES_UNIFORM ----

    def _extract_uses_uniform(
        self, node: Node, file_path: str, pv_view,
        uniform_names: set,
    ) -> list[Edge]:
        """function_definition 函数体里引用了 uniform 名 → USES_UNIFORM 边。

        遍历函数体所有 identifier，匹配本文件声明的 Uniform 名集合。
        每对 (function, uniform) 只记一条边（去重）。
        source_name=函数名 → target_name=uniform 名。
        """
        # 找函数名
        decl = first_child(node, "function_declarator")
        func_name = None
        if decl:
            for c in decl.children:
                if c.type == "identifier":
                    func_name = text_of(c)
                    break
        if not func_name:
            return []
        # 函数体（compound_statement）里收集 identifier
        body = first_child(node, "compound_statement")
        if not body:
            return []
        used: set[str] = set()
        for n in walk(body):
            if n.type == "identifier":
                name = text_of(n)
                if name in uniform_names:
                    used.add(name)
        if not used:
            return []
        # 函数起始行（CALLS 边的 source_line 用调用行，USES_UNIFORM 用函数起始行）
        line = node.start_point[0] + 1
        sig = self._sig_at(pv_view, line)
        return [
            Edge(
                kind="USES_UNIFORM",
                source_file=file_path,
                source_line=line,
                source_name=func_name,
                target_name=uname,
                conditional_signature=sig,
            )
            for uname in sorted(used)
        ]

    # ---- CONDITIONAL_ON ----

    def _extract_art_edges(
        self, node: Node, file_path: str, pv_view,
    ) -> list[Edge]:
        """#art NAME "desc" "BOOL"/"INT" → CONDITIONAL_ON 边。

        记"#art 开关声明"本身——查询层靠这条边找到所有 #art 开关名。
        source_name=None（file-level 声明）→ target_name=NAME，
        properties 存 art_type + description。
        """
        name = None
        description = ""
        art_type = ""
        for child in node.children:
            if child.type == "identifier" and name is None:
                name = text_of(child)
            elif child.type == "string_literal":
                txt = text_of(child)
                if len(txt) >= 2 and txt[0] == '"':
                    txt = txt[1:-1]
                if not description:
                    description = txt
                else:
                    art_type = txt
        if not name:
            return []
        line = node.start_point[0] + 1
        return [Edge(
            kind="CONDITIONAL_ON",
            source_file=file_path,
            source_line=line,
            source_name=None,
            target_name=name,
            properties={
                "art_type": art_type or "BOOL",
                "description": description,
            },
            conditional_signature=self._sig_at(pv_view, line),
        )]


__all__ = ["Edge", "EdgeExtractor"]
