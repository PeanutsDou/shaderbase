"""expr_parser — #if 表达式求值器（迭代 2）。

tree-sitter 已经把 #if 条件解析成 AST（binary_expression / preproc_defined /
unary_expression / parenthesized_expression / number_literal / identifier），
本模块遍历这棵 AST 求值，不需要自己 tokenize（跟 nsp C++ 版的关键差异）。

支持：
- number_literal: 0 → False, 非 0 → True（含 hex 0x..、float）
- identifier: 宏名，查 defines；未定义在数值上下文合成 0（对齐 nsp）
- preproc_defined: defined(X) / defined X → X 在 defines 里为 True
- unary_expression: ! / - / + 前缀
- binary_expression: && / || / ! / == / != / < / > / <= / >= / & / | / ^ / + / - / * / / / %
- parenthesized_expression: ( expr )
"""
from __future__ import annotations

from typing import Optional

from tree_sitter import Node

from .view import ConditionDiagnostic
from ..parser.ast_utils import text_of


def eval_condition(
    node: Node,
    defines: dict[str, int],
    diagnostics: Optional[list[ConditionDiagnostic]] = None,
    line: int = 0,
) -> bool:
    """求值 #if/#elif 条件表达式，返回 bool。"""
    val = _eval_expr(node, defines, diagnostics, line)
    return val != 0


def _eval_expr(
    node: Node,
    defines: dict[str, int],
    diagnostics: Optional[list[ConditionDiagnostic]] = None,
    line: int = 0,
) -> int:
    """求值表达式，返回 int（C 预处理语义：0=False，非0=True）。"""
    t = node.type

    if t == "number_literal":
        return _parse_number(text_of(node))

    if t == "identifier":
        name = text_of(node)
        if name in defines:
            return int(defines[name])
        # 数值上下文未定义宏合成 0（对齐 nsp evaluateMacro）
        if diagnostics is not None:
            diagnostics.append(ConditionDiagnostic(
                line=line, message=f"undefined macro '{name}' used in #if, synthesized as 0",
                macro_name=name, synthesized_zero=True,
            ))
        return 0

    if t == "preproc_defined":
        return 1 if _eval_defined(node, defines) else 0

    if t == "unary_expression":
        return _eval_unary(node, defines, diagnostics, line)

    if t == "binary_expression":
        return _eval_binary(node, defines, diagnostics, line)

    if t == "parenthesized_expression":
        # ( expr ) → 取内部表达式
        for child in node.children:
            if child.type not in ("(", ")"):
                return _eval_expr(child, defines, diagnostics, line)
        return 0

    if t == "char_literal":
        # 'A' → ASCII 值
        txt = text_of(node)
        if len(txt) >= 3 and txt[0] == "'":
            return ord(txt[1])
        return 0

    # 未知节点，保守返回 0
    return 0


def _eval_defined(node: Node, defines: dict[str, int]) -> bool:
    """求值 defined(X) / defined X。"""
    name = None
    for child in node.children:
        if child.type == "identifier":
            name = text_of(child)
            break
    if name is None:
        return False
    return name in defines


def _eval_unary(
    node: Node,
    defines: dict[str, int],
    diagnostics: Optional[list[ConditionDiagnostic]] = None,
    line: int = 0,
) -> int:
    """求值一元表达式: ! / - / + / ~。"""
    op = None
    operand = None
    for child in node.children:
        if child.type in ("!", "-", "+", "~"):
            op = child.type
        elif op is not None:
            operand = child
    if operand is None:
        return 0
    val = _eval_expr(operand, defines, diagnostics, line)
    if op == "!":
        return 0 if val != 0 else 1
    if op == "-":
        return -val
    if op == "+":
        return val
    if op == "~":
        return ~val
    return val


def _eval_binary(
    node: Node,
    defines: dict[str, int],
    diagnostics: Optional[list[ConditionDiagnostic]] = None,
    line: int = 0,
) -> int:
    """求值二元表达式。

    tree-sitter binary_expression 形状：left op right
    其中 op 是叶子节点（&& / || / == / ...）
    短路求值：&& / || 用短路（对齐 C 预处理语义，避免未定义宏副作用）
    """
    left = None
    op = None
    right = None
    for child in node.children:
        if child.type in ("&&", "||", "!=", "==", "<", ">", "<=", ">=",
                          "&", "|", "^", "+", "-", "*", "/", "%", "<<", ">>"):
            op = child.type
        elif op is None:
            left = child
        else:
            right = child
    if left is None or right is None:
        return 0

    # 短路求值
    if op == "&&":
        l = _eval_expr(left, defines, diagnostics, line)
        if l == 0:
            return 0
        return 0 if _eval_expr(right, defines, diagnostics, line) == 0 else 1
    if op == "||":
        l = _eval_expr(left, defines, diagnostics, line)
        if l != 0:
            return 1
        return 0 if _eval_expr(right, defines, diagnostics, line) == 0 else 1

    # 非短路：两边都求值
    l = _eval_expr(left, defines, diagnostics, line)
    r = _eval_expr(right, defines, diagnostics, line)

    if op == "==":
        return 1 if l == r else 0
    if op == "!=":
        return 1 if l != r else 0
    if op == "<":
        return 1 if l < r else 0
    if op == ">":
        return 1 if l > r else 0
    if op == "<=":
        return 1 if l <= r else 0
    if op == ">=":
        return 1 if l >= r else 0
    if op == "&":
        return l & r
    if op == "|":
        return l | r
    if op == "^":
        return l ^ r
    if op == "+":
        return l + r
    if op == "-":
        return l - r
    if op == "*":
        return l * r
    if op == "/":
        return int(l / r) if r != 0 else 0
    if op == "%":
        return l % r if r != 0 else 0
    if op == "<<":
        return l << r
    if op == ">>":
        return l >> r
    return 0


def _parse_number(text: str) -> int:
    """解析数值字面量（支持 hex / decimal / float 后缀）。"""
    txt = text.strip()
    # 去掉后缀 u/U/L/L/f/F
    while txt and txt[-1] in "uUlLfF":
        txt = txt[:-1]
    if not txt:
        return 0
    try:
        if txt.lower().startswith("0x"):
            return int(txt, 16)
        if "." in txt or "e" in txt.lower():
            return 1 if float(txt) != 0 else 0
        return int(txt, 10)
    except ValueError:
        return 0


__all__ = ["eval_condition"]
