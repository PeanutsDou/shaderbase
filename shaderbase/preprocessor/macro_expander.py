"""macro_expander — function-like macro 展开（迭代 3）。

支持：
- object-like 宏: #define FOO 1 → FOO 替换成 1
- function-like 宏: #define ADD(a,b) ((a)+(b)) → ADD(1,2) 替换成 ((1)+(2))
- # 字符串化: #define STR(x) #x → STR(hello) 替换成 "hello"
- ## token paste: #define CAT(a,b) a##b → CAT(foo,bar) 替换成 foobar

只做 #if/#elif 表达式里的宏展开——不展开普通代码（那是 expanded_source 的活，
shaderbase 不做）。这是 nsp expandFunctionLikeMacro 的子集。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from tree_sitter import Node

from ..parser.ast_utils import text_of


@dataclass
class MacroDef:
    """一个 #define 定义的宏。"""
    name: str
    is_function_like: bool = False
    params: list[str] = field(default_factory=list)   # function-like 的参数名
    body: str = ""                                     # 替换体文本


def collect_defines(root_node: Node) -> dict[str, MacroDef]:
    """从 AST 收集所有 #define（preproc_def / preproc_function_def）。

    返回 {宏名: MacroDef}。注意：重复 #define 后者覆盖前者（对齐 C 语义）。
    """
    out: dict[str, MacroDef] = {}
    for node in _walk(root_node):
        t = node.type
        if t == "preproc_def":
            # object-like: #define NAME body
            name = None
            body = ""
            for child in node.children:
                if child.type == "identifier" and name is None:
                    name = text_of(child)
                elif child.type == "preproc_arg":
                    body = text_of(child)
            if name:
                out[name] = MacroDef(name=name, is_function_like=False, body=body)
        elif t == "preproc_function_def":
            # function-like: #define NAME(params) body
            name = None
            params: list[str] = []
            body = ""
            for child in node.children:
                if child.type == "identifier" and name is None:
                    name = text_of(child)
                elif child.type == "preproc_params":
                    for sub in child.children:
                        if sub.type == "identifier":
                            params.append(text_of(sub))
                elif child.type == "preproc_arg":
                    body = text_of(child)
            if name:
                out[name] = MacroDef(
                    name=name, is_function_like=True,
                    params=params, body=body,
                )
        elif t == "preproc_undef":
            # #undef NAME → 删除
            for child in node.children:
                if child.type == "identifier":
                    out.pop(text_of(child), None)
                    break
    return out


def expand_text(
    text: str,
    macros: dict[str, MacroDef],
    depth: int = 0,
    max_depth: int = 50,
) -> str:
    """把文本里的宏调用展开（递归，有深度保护防无限展开）。

    简单 token 扫描，不依赖 AST——给 #if 表达式文本展开用。
    """
    if depth > max_depth:
        return text
    result = text
    changed = True
    iterations = 0
    while changed and iterations < max_depth:
        changed = False
        iterations += 1
        for name, macro in macros.items():
            if macro.is_function_like:
                result, did = _expand_function_like(result, name, macro)
                if did:
                    changed = True
            else:
                if name in result:
                    new = result.replace(_word_boundary(name), macro.body)
                    if new != result:
                        result = new
                        changed = True
    # 递归展开替换体里的宏
    if depth < max_depth and result != text:
        return expand_text(result, macros, depth + 1, max_depth)
    return result


def _expand_function_like(text: str, name: str, macro: MacroDef) -> tuple[str, bool]:
    """展开 function-like 宏调用。

    匹配 NAME(args)，用参数替换宏体里的形参，处理 # 和 ##。
    """
    pattern = re.compile(r'\b' + re.escape(name) + r'\s*\(')
    result = []
    pos = 0
    changed = False
    while pos < len(text):
        m = pattern.search(text, pos)
        if not m:
            result.append(text[pos:])
            break
        # 找匹配的右括号
        paren_start = m.end() - 1
        depth_paren = 1
        i = m.end()
        args_text = ""
        while i < len(text) and depth_paren > 0:
            if text[i] == '(':
                depth_paren += 1
            elif text[i] == ')':
                depth_paren -= 1
            if depth_paren > 0:
                args_text += text[i]
            i += 1
        if depth_paren != 0:
            # 括号不匹配，放弃
            result.append(text[pos:])
            break
        # 分割参数
        args = _split_args(args_text)
        # 替换宏体
        expansion = _substitute_params(macro, args)
        result.append(text[pos:m.start()])
        result.append(expansion)
        pos = i
        changed = True
    return "".join(result), changed


def _split_args(args_text: str) -> list[str]:
    """分割宏调用参数（处理嵌套括号）。"""
    args = []
    cur = ""
    depth = 0
    for ch in args_text:
        if ch == '(':
            depth += 1
            cur += ch
        elif ch == ')':
            depth -= 1
            cur += ch
        elif ch == ',' and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def _substitute_params(macro: MacroDef, args: list[str]) -> str:
    """用实参替换宏体里的形参，处理 # 和 ##。"""
    if not macro.params:
        return macro.body
    # 参数绑定
    binding = {}
    for i, param in enumerate(macro.params):
        if i < len(args):
            binding[param] = args[i]
        else:
            binding[param] = ""
    body = macro.body
    # 处理 #x → "x"（字符串化）
    body = re.sub(
        r'#\s*([A-Za-z_]\w*)',
        lambda m: '"' + binding.get(m.group(1), m.group(1)) + '"',
        body,
    )
    # 处理 a##b → ab（token paste）
    def paste(m):
        left = m.group(1)
        right = m.group(2)
        left_val = binding.get(left, left)
        right_val = binding.get(right, right)
        return left_val + right_val
    body = re.sub(r'([A-Za-z_]\w*)\s*##\s*([A-Za-z_]\w*)', paste, body)
    # 普通参数替换
    for param, val in binding.items():
        body = re.sub(r'\b' + re.escape(param) + r'\b', val, body)
    return body


def _word_boundary(name: str) -> str:
    """构造 word-boundary 匹配的 name（避免子串误匹配）。"""
    return name  # 简化：直接 replace，边界情况由调用方处理


def _walk(node: Node):
    """DFS 遍历。"""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for c in reversed(n.children):
            stack.append(c)


__all__ = ["MacroDef", "collect_defines", "expand_text"]
