"""ast_utils — tree-sitter AST 遍历 helper。

抽取器要用的几个高频操作：
- 先序遍历整个 AST（拿节点列表）
- 按类型查节点（找所有 function_definition）
- 取节点文本（bytes → str，容忍编码问题）
- 取第一个 identifier 子节点（节点名）
- 取语义（`: TEXCOORD0` / `: register(t0)`）
"""
from __future__ import annotations

from typing import Iterator, Optional

from tree_sitter import Node


def walk(node: Node) -> Iterator[Node]:
    """先序遍历，DFS 栈实现（不递归，AST 深的文件不会爆栈）。"""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        # 反序入栈保证前序
        for child in reversed(n.children):
            stack.append(child)


def find_by_type(node: Node, type_name: str) -> list[Node]:
    """在 node 子树里找所有 type == type_name 的节点。"""
    return [n for n in walk(node) if n.type == type_name]


def text_of(node: Node) -> str:
    """取节点文本，bytes → utf-8，replace 容忍编码问题。"""
    return node.text.decode("utf-8", "replace")


def first_child(node: Node, type_name: str) -> Optional[Node]:
    """取第一个 type == type_name 的直接子节点。"""
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def first_identifier(node: Node) -> Optional[str]:
    """取节点子树里第一个 identifier 的文本，常用作节点名。"""
    for n in walk(node):
        if n.type == "identifier":
            return text_of(n)
    return None


def semantics_of(node: Node) -> Optional[str]:
    """取 `semantics` 子节点的语义名文本。

    支持:
    - : TEXCOORD0            → 'TEXCOORD0'
    - : register(t0)         → 'register(t0)'  (call 形态)
    - : SV_Position          → 'SV_Position'
    多个 semantics（G66 双冒号）取第一个，第二个目前 grammar 标 ERROR。
    """
    sem = first_child(node, "semantics")
    if not sem:
        return None
    # semantics 直接子节点里 ':' 后面跟 identifier 或 call_expression
    # 直接取 ':' 之后的纯文本，去掉前后空白和冒号
    txt = text_of(sem).strip()
    if txt.startswith(":"):
        txt = txt[1:].strip()
    return txt or None


__all__ = [
    "walk",
    "find_by_type",
    "text_of",
    "first_child",
    "first_identifier",
    "semantics_of",
]
