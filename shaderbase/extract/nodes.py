"""nodes — AST → 知识库节点抽取器（阶段 4 核心）。

吃 tree-sitter AST，吐出 shaderbase SQLite nodes 表要的节点列表。

抽取的节点 kind（对齐 DEV_PLAN §3.2 schema）：
  - Function       : function_definition（含 entry point 判定 vs_main/ps_main/cs_main）
  - Struct         : struct_specifier
  - Uniform        : declaration 带 metadata_block 或带 initial 值的非 static 变量
  - Texture        : texture_declaration（小写 texture）+ 大写 Texture2D/Texture3D/TextureCube/RWTexture* 走 declaration
  - SamplerState   : sampler_state_declaration
  - CBuffer        : cbuffer_specifier
  - Technique      : technique_block（含 pass_block 子节点）

每个节点输出 dict，字段对齐 DEV_PLAN §3.2 nodes 表：
  kind, name, qualified_name(暂同 name), file_path, line, start_col,
  end_line, end_col, properties(JSON)

设计原则（DEV_PLAN §1.7）：
  - 抽取器只做机械翻译，不做主观判断（语义校验留给 fixture）
  - 抽不出名字的节点记 None，不抛异常——抽不动一个文件不影响其他文件
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from tree_sitter import Node, Parser

from ..parser.ast_utils import (
    find_by_type,
    first_child,
    first_identifier,
    text_of,
    walk,
)
from ..parser.tree_sitter_loader import parser as get_parser


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ShaderNode:
    """抽取出的一个知识库节点。"""
    kind: str
    name: Optional[str]
    file_path: str
    line: int                       # 1-based
    start_col: int                  # 0-based
    end_line: int                   # 1-based
    end_col: int                    # 0-based
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.name,
            "file_path": self.file_path,
            "line": self.line,
            "start_col": self.start_col,
            "end_line": self.end_line,
            "end_col": self.end_col,
            "properties": self.properties,
        }


# 入口函数名 → stage（G66 约定，INVENTORY §6.5）
ENTRY_POINTS = {
    "vs_main": "vertex",
    "ps_main": "pixel",
    "cs_main": "compute",
}


# 大写贴图类型（走 CPP declaration 路径，不走 texture_declaration）
TEXTURE_TYPES = {
    "Texture2D", "Texture2DArray", "Texture2DMS", "Texture2DMSArray",
    "Texture3D", "TextureCube", "TextureCubeArray",
    "RWTexture1D", "RWTexture2D", "RWTexture3D",
}


# ---------------------------------------------------------------------------
# 抽取器
# ---------------------------------------------------------------------------

class NodeExtractor:
    """遍历 AST 抽节点。一个实例可复用处理多个文件。"""

    def __init__(self, parser: Optional[Parser] = None):
        self._parser = parser or get_parser()

    # ---- 公共入口 ----

    def extract_file(self, source: bytes, file_path: str) -> list[ShaderNode]:
        """解析一个文件，返回抽到的节点列表。

        source: bytes（tree-sitter 吃 bytes，编码由 ast_utils 容忍）
        file_path: 用于填到节点的 file_path 字段
        """
        tree = self._parser.parse(source)
        root = tree.root_node
        nodes: list[ShaderNode] = []

        for node in walk(root):
            n = self._extract_one(node, file_path, root)
            if n:
                if isinstance(n, list):
                    nodes.extend(n)
                else:
                    nodes.append(n)
        return nodes

    def extract_path(self, abs_path: str) -> list[ShaderNode]:
        """从磁盘读文件并抽取。"""
        with open(abs_path, "rb") as f:
            src = f.read()
        return self.extract_file(src, abs_path.replace("\\", "/"))

    # ---- 各类节点抽取 ----

    def _extract_one(
        self, node: Node, file_path: str, root: Node
    ) -> Optional[ShaderNode | list[ShaderNode]]:
        t = node.type
        if t == "function_definition":
            return self._extract_function(node, file_path)
        if t == "struct_specifier":
            return self._extract_struct(node, file_path)
        if t == "texture_declaration":
            return self._extract_texture(node, file_path)
        if t == "sampler_state_declaration":
            return self._extract_sampler(node, file_path)
        if t == "cbuffer_specifier":
            return self._extract_cbuffer(node, file_path)
        if t == "technique_block":
            return self._extract_technique(node, file_path)
        if t == "declaration":
            return self._extract_declaration(node, file_path, root)
        return None

    # ---- Function ----

    def _extract_function(self, node: Node, file_path: str) -> ShaderNode:
        # function_definition: [hlsl_attribute] type function_declarator(id + params + semantics) body
        decl = first_child(node, "function_declarator")
        name = first_identifier(decl or node) or "<anonymous>"
        # 返回类型：function_definition 第一个 type_identifier/primitive_type
        ret_type = None
        for c in node.children:
            if c.type in ("type_identifier", "primitive_type"):
                ret_type = text_of(c)
                break
        # entry point 判定
        stage = ENTRY_POINTS.get(name)
        props: dict[str, Any] = {"return_type": ret_type}
        if stage:
            props["stage"] = stage
            props["is_entry_point"] = True
        # attribute（[numthreads]/[unroll]）
        attr = first_child(node, "hlsl_attribute")
        if attr:
            props["attributes"] = [text_of(attr).strip()]
        # 参数列表
        params = self._extract_params(decl)
        if params:
            props["parameters"] = params
        return ShaderNode(
            kind="Function",
            name=name,
            file_path=file_path,
            line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
            properties=props,
        )

    def _extract_params(self, func_decl: Optional[Node]) -> list[dict]:
        """抽 function_declarator 的参数列表。"""
        if not func_decl:
            return []
        plist = first_child(func_decl, "parameter_list")
        if not plist:
            return []
        out = []
        for c in plist.children:
            if c.type != "parameter_declaration":
                continue
            ptype = None
            pname = None
            for cc in c.children:
                if cc.type in ("type_identifier", "primitive_type"):
                    ptype = text_of(cc)
                elif cc.type == "identifier":
                    pname = text_of(cc)
            out.append({
                "type": ptype,
                "name": pname,
                "semantic": self._semantics_of(c),
            })
        return out

    # ---- Struct ----

    def _extract_struct(self, node: Node, file_path: str) -> ShaderNode:
        name = first_child(node, "type_identifier")
        name_str = text_of(name) if name else None
        fields = []
        fdl = first_child(node, "field_declaration_list")
        if fdl:
            for c in fdl.children:
                if c.type != "field_declaration":
                    continue
                ftype = None
                fname = None
                for cc in c.children:
                    if cc.type in ("type_identifier", "primitive_type"):
                        ftype = text_of(cc)
                    elif cc.type == "field_identifier":
                        fname = text_of(cc)
                fields.append({
                    "type": ftype,
                    "name": fname,
                    "semantic": self._semantics_of(c),
                })
        return ShaderNode(
            kind="Struct",
            name=name_str,
            file_path=file_path,
            line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
            properties={"fields": fields},
        )

    # ---- Texture（小写 texture）----

    def _extract_texture(self, node: Node, file_path: str) -> ShaderNode:
        name = first_child(node, "identifier")
        name_str = text_of(name) if name else None
        sem = self._semantics_of(node)
        meta = self._extract_metadata(node)
        props: dict[str, Any] = {"texture_type": "texture"}
        if sem:
            props["semantic"] = sem
        if meta:
            props["annotation"] = meta
        return ShaderNode(
            kind="Texture",
            name=name_str,
            file_path=file_path,
            line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
            properties=props,
        )

    # ---- SamplerState ----

    def _extract_sampler(self, node: Node, file_path: str) -> ShaderNode:
        name = first_child(node, "identifier")
        name_str = text_of(name) if name else None
        sem = self._semantics_of(node)
        states = self._extract_states(node, "sampler_state_block")
        props: dict[str, Any] = {}
        if sem:
            props["semantic"] = sem
        if states:
            props["states"] = states
        return ShaderNode(
            kind="SamplerState",
            name=name_str,
            file_path=file_path,
            line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
            properties=props,
        )

    # ---- CBuffer ----

    def _extract_cbuffer(self, node: Node, file_path: str) -> ShaderNode:
        # cbuffer_specifier: 'cbuffer' name? { field_declaration_list }
        # 名字可能在 _class_name 字段（alias），统一抓第一个 type_identifier
        name = None
        for c in node.children:
            if c.type == "type_identifier":
                name = text_of(c)
                break
        sem = self._semantics_of(node)
        fields = []
        fdl = first_child(node, "field_declaration_list")
        if fdl:
            for c in fdl.children:
                if c.type != "field_declaration":
                    continue
                ftype = None
                fname = None
                for cc in c.children:
                    if cc.type in ("type_identifier", "primitive_type"):
                        ftype = text_of(cc)
                    elif cc.type in ("field_identifier", "identifier"):
                        fname = text_of(cc)
                fields.append({
                    "type": ftype,
                    "name": fname,
                    "semantic": self._semantics_of(c),
                })
        props: dict[str, Any] = {"fields": fields}
        if sem:
            props["semantic"] = sem
        return ShaderNode(
            kind="CBuffer",
            name=name,
            file_path=file_path,
            line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
            properties=props,
        )

    # ---- Technique ----

    def _extract_technique(self, node: Node, file_path: str) -> ShaderNode:
        name = first_child(node, "identifier")
        name_str = text_of(name) if name else None
        meta = self._extract_metadata(node)
        passes = []
        for c in node.children:
            if c.type != "pass_block":
                continue
            pname = first_child(c, "identifier")
            pstates = self._extract_states(c, "sampler_state_block")  # state 赋值同形态
            # pass 内的 state_assignment 直接子节点
            states_direct = []
            for cc in c.children:
                if cc.type == "state_assignment":
                    states_direct.append(self._state_to_dict(cc))
            passes.append({
                "name": text_of(pname) if pname else None,
                "states": states_direct or pstates,
            })
        props: dict[str, Any] = {"passes": passes}
        if meta:
            props["annotation"] = meta
        return ShaderNode(
            kind="Technique",
            name=name_str,
            file_path=file_path,
            line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
            properties=props,
        )

    # ---- Declaration（含 Uniform / 大写 Texture / 普通变量）----

    def _extract_declaration(
        self, node: Node, file_path: str, root: Node
    ) -> Optional[ShaderNode | list[ShaderNode]]:
        """declaration 路径分流：
        - 大写 Texture2D 等 → Texture 节点
        - 带 metadata_block 的变量 → Uniform 节点（带 annotation）
        - static const → 常量 Uniform
        - 其他普通声明 → Variable 节点（阶段 4 暂不收，留给后续）
        """
        # 找类型
        type_node = None
        for c in node.children:
            if c.type in ("type_identifier", "primitive_type"):
                type_node = c
                break
            if c.type == "storage_class_specifier":
                continue
            if c.type == "type_qualifier":
                continue
        type_str = text_of(type_node) if type_node else None

        # 大写贴图类型
        if type_str in TEXTURE_TYPES:
            return self._extract_uppercase_texture(node, file_path, type_str)

        # 抓 storage qualifier（static/const/extern...）
        is_static = any(
            c.type == "storage_class_specifier" for c in node.children
        )
        is_const = any(
            c.type == "type_qualifier" and "const" in text_of(c)
            for c in node.children
        )

        # 带 metadata_block → Uniform
        meta = first_child(node, "metadata_block")
        if meta:
            annotation = self._extract_metadata(node)
            out = []
            for c in node.children:
                if c.type != "identifier":
                    continue
                props: dict[str, Any] = {
                    "type": type_str,
                    "is_const": is_const or None,
                    "is_static": is_static or None,
                }
                if annotation:
                    props["annotation"] = annotation
                # 初始值
                init = self._extract_init(node)
                if init is not None:
                    props["default"] = init
                out.append(ShaderNode(
                    kind="Uniform",
                    name=text_of(c),
                    file_path=file_path,
                    line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                    properties={k: v for k, v in props.items() if v is not None},
                ))
            return out or None

        # static const 常量 → Uniform（DEV_PLAN 范畴：常量也算 uniform）
        if is_static and is_const:
            out = []
            for c in node.children:
                if c.type != "identifier":
                    continue
                init = self._extract_init(node)
                props2: dict[str, Any] = {"type": type_str, "is_const": True}
                if init is not None:
                    props2["default"] = init
                out.append(ShaderNode(
                    kind="Uniform",
                    name=text_of(c),
                    file_path=file_path,
                    line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                    properties=props2,
                ))
            return out or None

        # 普通变量声明 → 暂不收（阶段 4 只关注 7 类节点）
        return None

    def _extract_uppercase_texture(
        self, node: Node, file_path: str, type_str: str
    ) -> Optional[ShaderNode | list[ShaderNode]]:
        sem = self._semantics_of(node)
        # 大写贴图通常没 metadata_block，但有的话也抓
        meta = self._extract_metadata(node)
        out = []
        for c in node.children:
            if c.type != "identifier":
                continue
            props: dict[str, Any] = {"texture_type": type_str}
            if sem:
                props["semantic"] = sem
            if meta:
                props["annotation"] = meta
            out.append(ShaderNode(
                kind="Texture",
                name=text_of(c),
                file_path=file_path,
                line=node.start_point[0] + 1,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
                properties=props,
            ))
        return out or None

    # ---- 公共 helper ----

    def _semantics_of(self, node: Node) -> Optional[str]:
        """取节点直接子 semantics 的文本（去掉 ':'）。

        struct field 的 `: TEXCOORD0` 在 CPP 上游 grammar 里走 bitfield_clause
        （不是 semantics），这里两种都认。
        """
        sem = first_child(node, "semantics")
        if sem is None:
            sem = first_child(node, "bitfield_clause")
        if not sem:
            return None
        txt = text_of(sem).strip()
        if txt.startswith(":"):
            txt = txt[1:].strip()
        return txt or None

    def _extract_metadata(self, parent: Node) -> dict:
        """抽 metadata_block 的所有 key=value 对。

        metadata_block 内混合 metadata_assignment 和裸 declaration（grammar 兜底）。
        """
        mb = first_child(parent, "metadata_block")
        if not mb:
            return {}
        out: dict[str, Any] = {}
        for c in mb.children:
            if c.type == "metadata_assignment":
                key, val = self._meta_assignment_to_pair(c)
                if key:
                    out[key] = val
            elif c.type == "declaration":
                # 兜底形态：`float SasUiMin = 0;`
                key, val = self._decl_to_meta_pair(c)
                if key:
                    out[key] = val
        return out

    def _meta_assignment_to_pair(self, node: Node) -> tuple[Optional[str], Any]:
        """metadata_assignment: [type] name = value ;"""
        key = None
        for c in node.children:
            if c.type == "identifier" and key is None:
                key = text_of(c)
        # value 是 = 之后第一个非标点子节点
        val: Any = None
        seen_eq = False
        for c in node.children:
            if c.type == "=":
                seen_eq = True
                continue
            if seen_eq and c.type not in (";",):
                val = self._literal_value(c)
                break
        return key, val

    def _decl_to_meta_pair(self, node: Node) -> tuple[Optional[str], Any]:
        """declaration 兜底形态抽 key=value。"""
        key = None
        for c in node.children:
            if c.type == "identifier":
                key = text_of(c)
                break
        val: Any = None
        seen_eq = False
        for c in node.children:
            if c.type == "=":
                seen_eq = True
                continue
            if seen_eq and c.type not in (";",):
                val = self._literal_value(c)
                break
        return key, val

    def _literal_value(self, node: Node) -> Any:
        """把 AST 字面量节点转成 Python 值。"""
        t = node.type
        if t == "string_literal":
            # 去掉前后引号
            txt = text_of(node)
            if len(txt) >= 2 and txt[0] in '"':
                return txt[1:-1]
            return txt
        if t == "number_literal":
            txt = text_of(node)
            try:
                if any(ch in txt for ch in ".fFeE"):
                    return float(txt.rstrip("fF"))
                return int(txt, 0)
            except ValueError:
                return txt
        if t in ("true_keyword", "false_keyword"):
            return text_of(node).lower() == "true"
        if t == "identifier":
            return text_of(node)
        # 复杂表达式（含算术/call）保留文本
        return text_of(node)

    def _extract_init(self, decl_node: Node) -> Any:
        """declaration 的 `= init` 部分。"""
        seen_eq = False
        for c in decl_node.children:
            if c.type == "=":
                seen_eq = True
                continue
            if seen_eq and c.type not in (";", ","):
                return self._literal_value(c)
        return None

    def _extract_states(self, parent: Node, block_type: str) -> list[dict]:
        """sampler_state_block / pass_block 内的 state_assignment 列表。"""
        block = first_child(parent, block_type)
        if not block:
            return []
        out = []
        for c in block.children:
            if c.type == "state_assignment":
                out.append(self._state_to_dict(c))
        return out

    def _state_to_dict(self, node: Node) -> dict:
        """state_assignment: identifier = value ;"""
        key = None
        val: Any = None
        seen_eq = False
        for c in node.children:
            if c.type == "identifier":
                if not seen_eq:
                    key = text_of(c)
                else:
                    val = text_of(c)
            elif c.type == "=":
                seen_eq = True
            elif c.type in ("number_literal", "string_literal", "true_keyword", "false_keyword"):
                if seen_eq and val is None:
                    val = self._literal_value(c)
        return {"name": key, "value": val}


# ---------------------------------------------------------------------------
# 便利函数
# ---------------------------------------------------------------------------

def extract_file(source: bytes, file_path: str) -> list[dict]:
    """便利入口：解析一段源码，返回节点 dict 列表（不带 ShaderNode 中间类型）。"""
    ext = NodeExtractor()
    return [n.to_dict() for n in ext.extract_file(source, file_path)]


def extract_path(abs_path: str) -> list[dict]:
    """便利入口：从磁盘读文件抽取。"""
    ext = NodeExtractor()
    return [n.to_dict() for n in ext.extract_path(abs_path)]


__all__ = ["ShaderNode", "NodeExtractor", "extract_file", "extract_path"]
