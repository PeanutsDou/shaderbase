"""edges — AST → 知识库边抽取。

遍历 AST 找关系，建边。每条边带 conditional_signature（PV 算）。

边类型（DEV_PLAN §3.2）：
  - INCLUDES: file A #include file B → 文件间依赖
  - CALLS: function A 的函数体里调了 function B → 调用关系（跨文件 resolve）
  - HAS_MEMBER: struct → field
  - DECLARES_UNIFORM: file/technique → uniform 声明
  - USES_UNIFORM: function → uniform 使用
  - FLOWS_TO: VS 输出 semantic → PS 输入 semantic
  - IS_ENTRY_POINT: technique → vs_main/ps_main
  - EXPOSES_TECHNIQUE: file → technique
  - CONDITIONAL_ON: function/node → #art 开关
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
    """遍历 AST 抽边。"""

    def __init__(self, parser=None):
        self._parser = parser or get_parser()

    def extract_file(
        self, source: bytes, file_path: str, pv_view=None,
    ) -> list[Edge]:
        """抽一个文件的所有边。

        pv_view: PreprocessorView（算 conditional_signature 用）
                没传就现场算（空 defines，索引阶段）
        """
        tree = self._parser.parse(source)
        if pv_view is None:
            pv_view = build_preprocessor_view(tree.root_node, source, {})

        edges: list[Edge] = []
        for node in walk(tree.root_node):
            edges.extend(self._extract_node_edges(node, file_path, pv_view))
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

    # ---- HAS_MEMBER ----

    def _extract_struct_members(
        self, node: Node, file_path: str, pv_view,
    ) -> list[Edge]:
        """struct → field → HAS_MEMBER 边。"""
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
            for cc in child.children:
                if cc.type in ("field_identifier", "identifier"):
                    fname = text_of(cc)
                    break
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
        return edges

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


__all__ = ["Edge", "EdgeExtractor"]
