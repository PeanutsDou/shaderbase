"""expr_parser — #if 表达式求值器（迭代 2 + 迭代 3 接 macro_expander）。

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
- call_expression: function-like macro 调用（如 IS_HIGH(QUALITY)）→ expand_text 展开后递归求值
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
    function_macros: Optional[dict] = None,
) -> bool:
    """求值 #if/#elif 条件表达式，返回 bool。

    function_macros: {name: MacroDef}，给 function-like macro 展开用（迭代 3）。
    """
    val = _eval_expr(node, defines, diagnostics, line, function_macros)
    return val != 0


def _eval_expr(
    node: Node,
    defines: dict[str, int],
    diagnostics: Optional[list[ConditionDiagnostic]] = None,
    line: int = 0,
    function_macros: Optional[dict] = None,
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
        return _eval_unary(node, defines, diagnostics, line, function_macros)

    if t == "binary_expression":
        return _eval_binary(node, defines, diagnostics, line, function_macros)

    if t == "parenthesized_expression":
        # ( expr ) → 取内部表达式
        for child in node.children:
            if child.type not in ("(", ")"):
                return _eval_expr(child, defines, diagnostics, line, function_macros)
        return 0

    if t == "call_expression":
        return _eval_call_expression(
            node, defines, diagnostics, line, function_macros,
        )

    if t == "char_literal":
        # 'A' → ASCII 值
        txt = text_of(node)
        if len(txt) >= 3 and txt[0] == "'":
            return ord(txt[1])
        return 0

    # 未知节点，保守返回 0
    return 0


def _eval_call_expression(
    node: Node,
    defines: dict[str, int],
    diagnostics: Optional[list[ConditionDiagnostic]] = None,
    line: int = 0,
    function_macros: Optional[dict] = None,
) -> int:
    """求值 call_expression。

    两种情况：
    1. callee 是 function-like macro（在 function_macros 里）→ expand_text 展开后递归求值
    2. callee 是普通 identifier（宏名/类型构造）→ 查 defines，未定义合成 0
    """
    # 取 callee 名
    callee = None
    for child in node.children:
        if child.type == "identifier":
            callee = text_of(child)
            break
    if callee is None:
        return 0

    # 情况 1：function-like macro 展开
    if function_macros and callee in function_macros:
        from .macro_expander import expand_text
        expr_text = text_of(node)
        expanded = expand_text(expr_text, function_macros)
        # 展开结果可能是 "((QUALITY) > 0)" 这种，需要把里面的 identifier 再求值
        # 简单做法：把展开文本里的 identifier 查 defines 替换成值，再 eval
        return _eval_expanded_text(expanded, defines, diagnostics, line)

    # 情况 2：普通 identifier 当宏名查 defines
    if callee in defines:
        return int(defines[callee])
    if diagnostics is not None:
        diagnostics.append(ConditionDiagnostic(
            line=line,
            message=f"undefined macro '{callee}' used as call in #if, synthesized as 0",
            macro_name=callee, synthesized_zero=True,
        ))
    return 0


def _eval_expanded_text(
    text: str,
    defines: dict[str, int],
    diagnostics: Optional[list[ConditionDiagnostic]] = None,
    line: int = 0,
) -> int:
    """把展开后的文本当算术表达式求值。

    展开结果如 "((QUALITY) > 0)"，里面的 identifier 查 defines 替换成值，
    再用 Python eval 求值（安全：只含数字/运算符/括号/已替换的 identifier）。
    C 三元运算符 cond ? x : y 转成 Python 的 x if cond else y。
    """
    import re
    # 把 identifier 替换成 defines 值（未定义的合成 0）
    def repl(m):
        name = m.group(0)
        if name in defines:
            return str(defines[name])
        if diagnostics is not None:
            diagnostics.append(ConditionDiagnostic(
                line=line,
                message=f"undefined macro '{name}' in expanded #if, synthesized as 0",
                macro_name=name, synthesized_zero=True,
            ))
        return "0"
    # 匹配 identifier（字母/下划线开头，不含运算符和括号）
    expr = re.sub(r'[A-Za-z_]\w*', repl, text)
    # C 三元 cond ? x : y → Python x if cond else y
    # 用栈匹配最外层 ? : 简单转换（不处理嵌套三元，#if 里极少嵌套）
    expr = _c_ternary_to_python(expr)
    # 安全求值：只允许数字、运算符、括号
    try:
        # 限制 globals/locals 防注入
        result = eval(expr, {"__builtins__": {}}, {})
        return int(result) if result else 0
    except Exception:
        return 0


def _c_ternary_to_python(expr: str) -> str:
    """C 三元 cond ? x : y → Python x if cond else y（非嵌套简单版）。

    #if 里三元罕见，只处理一层。无 ? 直接返回。
    括号会让 ? 在非 0 深度，所以先剥最外层平衡括号，再扫 depth=0 的 ?。
    """
    if "?" not in expr:
        return expr
    # 剥最外层平衡括号（((x ? y : z)) → x ? y : z）
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        balanced = True
        for i, ch in enumerate(expr[1:-1]):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth < 0:
                balanced = False
                break
        if balanced:
            expr = expr[1:-1]
        else:
            break
    # 找最浅深度的 ?（最小 depth 处的第一个 ?）
    depth = 0
    min_depth = 999
    q_idx = -1
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "?":
            if depth < min_depth:
                min_depth = depth
                q_idx = i
    if q_idx < 0:
        return expr
    # 找对应 :（同 min_depth，从 q_idx 之后找第一个同深度 :）
    depth = 0
    c_idx = -1
    for i in range(q_idx + 1, len(expr)):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ":" and depth == min_depth:
            c_idx = i
            break
    if c_idx < 0:
        return expr
    cond = expr[:q_idx].strip()
    true_part = expr[q_idx+1:c_idx].strip()
    false_part = expr[c_idx+1:].strip()
    return f"({true_part} if {cond} else {false_part})"


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
    function_macros: Optional[dict] = None,
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
    val = _eval_expr(operand, defines, diagnostics, line, function_macros)
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
    function_macros: Optional[dict] = None,
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
        l = _eval_expr(left, defines, diagnostics, line, function_macros)
        if l == 0:
            return 0
        return 0 if _eval_expr(right, defines, diagnostics, line, function_macros) == 0 else 1
    if op == "||":
        l = _eval_expr(left, defines, diagnostics, line, function_macros)
        if l != 0:
            return 1
        return 0 if _eval_expr(right, defines, diagnostics, line, function_macros) == 0 else 1

    # 非短路：两边都求值
    l = _eval_expr(left, defines, diagnostics, line, function_macros)
    r = _eval_expr(right, defines, diagnostics, line, function_macros)

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
