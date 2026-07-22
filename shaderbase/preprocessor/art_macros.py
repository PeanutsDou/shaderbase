"""art_macros — #art 宏识别和注入（迭代 5）。

G66 特化：#art NAME "描述" "BOOL"/"INT"
- 编辑器勾选 #art → #define NAME 1
- 不勾选时 → NAME 默认 0（对齐 nsp art_macro_defaults.hpp）
- companion constant：#art 块里同级的 enum 常量，按 include 闭包作用域化

shaderbase 简化：
- 索引阶段（空 defines）：#art NAME 注入 NAME=0（默认值）
  这样 #ifndef NAME #define NAME 0 不冲突，#if NAME 走 false 分支
- 查询阶段：Agent 传的 macros 覆盖 #art 默认值
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tree_sitter import Node

from ..parser.ast_utils import text_of, walk


@dataclass
class ArtMacro:
    """一个 #art 声明。"""
    name: str
    art_type: str         # "BOOL" / "INT"
    description: str = ""
    line: int = 0
    companion_constants: list[tuple[str, int]] = None  # (name, value)

    def __post_init__(self):
        if self.companion_constants is None:
            self.companion_constants = []


def collect_art_macros(root_node: Node) -> list[ArtMacro]:
    """从 AST 收集所有 #art 指令。

    #art NAME "desc" "BOOL" → ArtMacro(name=NAME, art_type="BOOL")
    """
    out: list[ArtMacro] = []
    for node in walk(root_node):
        if node.type != "preproc_art_directive":
            continue
        name = None
        description = ""
        art_type = ""
        for child in node.children:
            if child.type == "identifier" and name is None:
                name = text_of(child)
            elif child.type == "string_literal":
                inner = _extract_string(child)
                if not description:
                    description = inner
                else:
                    art_type = inner
        if name:
            out.append(ArtMacro(
                name=name, art_type=art_type or "BOOL",
                description=description,
                line=node.start_point[0] + 1,
            ))
    return out


def inject_art_defaults(
    defines: dict[str, int],
    art_macros: list[ArtMacro],
) -> dict[str, int]:
    """把 #art 默认 0 注入 defines（优先级最低，会被后续覆盖）。

    对齐 nsp seedInitialPreprocessorMacros 的 L1 层：
    #art BOOL/INT → NAME=0（默认未勾选）
    companion constants → NAME=value
    """
    out = dict(defines)  # 不改原 dict
    for macro in art_macros:
        if macro.art_type in ("BOOL", "INT"):
            # 默认 0（未勾选）
            if macro.name not in out:
                out[macro.name] = 0
        # companion constants
        for cname, cval in (macro.companion_constants or []):
            if cname not in out:
                out[cname] = cval
    return out


def _extract_string(node: Node) -> str:
    """从 string_literal 节点提取字符串内容（去引号）。"""
    txt = text_of(node)
    if len(txt) >= 2 and txt[0] == '"':
        return txt[1:-1]
    return txt


__all__ = ["ArtMacro", "collect_art_macros", "inject_art_defaults"]
