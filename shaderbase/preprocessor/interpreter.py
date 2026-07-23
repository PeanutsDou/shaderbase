"""interpreter — PreprocessorView 状态机解释器（核心算法）。

迭代 1 直通版：只处理最简单的条件结构，不碰表达式求值/宏展开/inactive probe。
- #if 0 / #if 1 → 分支 active/inactive
- #ifdef X / #ifndef X → 查 defines 字典
- #else → 前面没选中就 active
- #endif → pop 分支栈
- #if 表达式 → 暂当 0 处理（留 TODO，迭代 2 补）

输入：tree-sitter AST 根节点 + 源码 bytes + defines
输出：PreprocessorView（line_active + branch_sigs）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tree_sitter import Node

from .view import (
    BranchMergeInfo,
    ConditionDiagnostic,
    MacroEvent,
    MacroReplacement,
    MacroSource,
    PreprocessorView,
)
from ..parser.ast_utils import text_of


@dataclass
class _BranchFrame:
    """分支栈帧（对齐 nsp BranchFrame）。"""
    id: int
    branch_index: int = 0
    next_branch_index: int = 1
    branch_chosen: bool = False   # 是否已选中某分支
    parent_active: bool = True    # 父分支是否 active


@dataclass
class _InterpState:
    """解释器内部状态（对齐 nsp PreprocessorInterpreterState）。"""
    source: bytes
    defines: dict[str, int]
    line_count: int
    result: PreprocessorView = field(default_factory=PreprocessorView)
    branch_stack: list[_BranchFrame] = field(default_factory=list)
    next_branch_id: int = 0
    active: bool = True             # 当前是否在 active 分支
    macro_active: bool = True       # 宏处理是否 active（父分支 active 时才处理）
    # branch_sig 当前活跃的分支签名（branch_id, branch_index）列表
    cur_sig: list[tuple[int, int]] = field(default_factory=list)
    # function-like macro 定义（迭代 3：给 expr_parser 展开用）
    function_macros: dict = field(default_factory=dict)


def build_preprocessor_view(
    root_node: Node,
    source_text: bytes,
    defines: Optional[dict[str, int]] = None,
    art_macros: Optional[list] = None,
    configured_macros: Optional[dict[str, str]] = None,
) -> PreprocessorView:
    """主入口（对齐 nsp buildPreprocessorView）。

    迭代 6：用 macro_seeder 按优先级链注入初始宏表。
    迭代 3：收集 function-like macro 定义给 expr_parser 展开用。
    """
    defines = dict(defines or {})
    # 收集 #art 宏（没传就从 AST 自己收集）
    from .art_macros import collect_art_macros
    if art_macros is None:
        art_macros = collect_art_macros(root_node)

    # 迭代 6：按优先级链注入（art < configured < defines）
    from .macro_seeder import seed_defines, seed_initial_macros
    defines = seed_defines(defines, art_macros, configured_macros)
    initial_macros = seed_initial_macros(defines, art_macros, configured_macros)

    # 迭代 3：收集 function-like macro 定义（给 #if 表达式里的宏调用展开用）
    from .macro_expander import collect_defines as _collect_func_macros
    function_macros = _collect_func_macros(root_node)

    line_count = source_text.count(b"\n") + 1
    state = _InterpState(
        source=source_text,
        defines=defines,
        line_count=line_count,
        function_macros=function_macros,
    )
    state.result.initial_macros = initial_macros
    state.result.extend_to_lines(line_count)
    _interpret_children(state, root_node)
    return state.result


def _interpret_children(state: _InterpState, node: Node) -> None:
    """遍历节点的直接子节点，分发到对应处理函数。"""
    for child in node.children:
        _dispatch_node(state, child)


def _dispatch_node(state: _InterpState, node: Node) -> None:
    """分发单个节点到对应处理函数（递归处理嵌套结构）。"""
    t = node.type
    if t == "preproc_if":
        _interpret_preproc_if(state, node)
    elif t == "preproc_ifdef":
        _interpret_preproc_ifdef(state, node)
    elif t == "preproc_def":
        _handle_preproc_def(state, node)
    elif t == "preproc_function_def":
        _handle_preproc_function_def(state, node)
    elif t == "preproc_undef":
        _handle_preproc_undef(state, node)
    elif t == "preproc_call":
        _handle_preproc_call(state, node)
    elif t == "preproc_art_directive":
        pass  # 迭代 5
    elif t in ("preproc_elif", "preproc_else", "preproc_endif"):
        pass  # preproc_if 的子节点，顶层不该出现
    elif t in ("compound_statement", "field_declaration_list",
               "declaration", "function_definition", "struct_specifier",
               "technique_block", "pass_block", "cbuffer_specifier",
               "metadata_block", "sampler_state_block",
               "texture_declaration", "sampler_state_declaration"):
        # 这些节点可能含嵌套的 #if——递归进它们的子节点
        _write_line_state_for_node(state, node)
        for child in node.children:
            _dispatch_node(state, child)
    else:
        _write_line_state_for_node(state, node)
        # 对其他有子节点的也递归（保险）
        for child in node.children:
            if child.type in ("preproc_if", "preproc_ifdef", "preproc_def",
                              "preproc_function_def", "preproc_undef",
                              "preproc_call", "preproc_art_directive"):
                _dispatch_node(state, child)


def _interpret_preproc_if(state: _InterpState, node: Node) -> None:
    """处理 #if ... #endif 块（含嵌套的 #elif / #else）。

    AST 形状：
    preproc_if:
      #if
      <condition: number_literal | binary_expression | identifier | ...>
      <body nodes...>
      preproc_elif (可选):
        #elif
        <condition>
        <body>
        preproc_else (可选):
          #else
          <body>
      preproc_else (可选):
        #else
        <body>
      #endif
    """
    branch_id = state.next_branch_id
    state.next_branch_id += 1
    parent_active = state.active
    parent_sig = list(state.cur_sig)

    frame = _BranchFrame(id=branch_id, parent_active=parent_active)
    state.branch_stack.append(frame)

    branches = _collect_branches(node)
    active_branch_index = -1

    for i, branch in enumerate(branches):
        frame.branch_index = i
        frame.next_branch_index = max(frame.next_branch_index, i + 1)
        # 恢复到父分支状态
        state.active = parent_active
        state.cur_sig = list(parent_sig)

        branch_active = False
        if parent_active:
            if i == 0:
                # #if 分支：求值条件（支持 identifier/number/binary）
                branch_active = _eval_if_condition(state, branch)
            elif branch.kind == "elif":
                # #elif 分支：前面没选中才求值
                if not frame.branch_chosen:
                    branch_active = _eval_elif_condition(state, branch)
            elif branch.kind == "else":
                branch_active = not frame.branch_chosen

        if branch_active:
            frame.branch_chosen = True
            active_branch_index = i
            state.cur_sig.append((branch_id, i))

        state.active = branch_active
        state.macro_active = parent_active and branch_active
        # 处理分支体（body 节点的行状态由 _interpret_body_node 写）
        for body_node in branch.body_nodes:
            _interpret_body_node(state, body_node)

    # 写 #endif 行 + 各指令行本身（在循环外写，避免被 body 覆盖）
    _write_directive_lines(state, node, parent_active, parent_sig,
                           branch_id, branches, active_branch_index)

    state.branch_stack.pop()
    state.active = parent_active
    state.cur_sig = parent_sig


def _write_directive_lines(
    state: _InterpState, if_node: Node, parent_active: bool,
    parent_sig: list, branch_id: int, branches: list[_Branch],
    active_branch_index: int,
) -> None:
    """写 #if/#elif/#else/#endif 指令行本身的状态。

    parent_active 时，#if/#elif/#else 行标 True（指令本身总是被处理），
    #endif 行标 True；parent_active=False 时全标 False。
    """
    endif_line = -1
    for child in if_node.children:
        t = child.type
        if t in ("#if", "#elif", "#else"):
            line = child.start_point[0] + 1
            state.result.line_active[line-1] = parent_active
            state.result.branch_sigs[line-1] = list(parent_sig)
        elif t == "#endif":
            endif_line = child.start_point[0] + 1
        elif t == "preproc_elif":
            for sub in child.children:
                if sub.type in ("#elif", "#else"):
                    line = sub.start_point[0] + 1
                    state.result.line_active[line-1] = parent_active
                    state.result.branch_sigs[line-1] = list(parent_sig)
        elif t == "preproc_else":
            for sub in child.children:
                if sub.type == "#else":
                    line = sub.start_point[0] + 1
                    state.result.line_active[line-1] = parent_active
                    state.result.branch_sigs[line-1] = list(parent_sig)
    if endif_line >= 0:
        state.result.line_active[endif_line-1] = parent_active
        state.result.branch_sigs[endif_line-1] = list(parent_sig)
        state.result.branch_merges.append(BranchMergeInfo(
            line=endif_line, branch_id=branch_id,
            active_branch_index=active_branch_index,
            branch_count=len(branches),
        ))


@dataclass
class _Branch:
    """#if 的一个分支（#if 本身 / #elif / #else）。"""
    kind: str               # "if" | "elif" | "else"
    directive_line: int    # 指令所在行（1-based）
    condition_node: Optional[Node] = None
    body_nodes: list[Node] = field(default_factory=list)


def _collect_branches(if_node: Node) -> list[_Branch]:
    """从 preproc_if 节点收集所有分支。

    AST 形状：
    preproc_if
      #if
      <condition>
      <body...>
      preproc_elif (可选，嵌套)
        #elif
        <condition>
        <body...>
        preproc_else (可选，嵌套在 elif 里)
          #else
          <body...>
      preproc_else (可选，直接嵌套在 if 里)
        #else
        <body...>
      #endif
    """
    branches = []
    cur_branch: Optional[_Branch] = None
    for child in if_node.children:
        t = child.type
        if t == "#if":
            cur_branch = _Branch(kind="if", directive_line=child.start_point[0] + 1)
            branches.append(cur_branch)
        elif t == "#elif":
            cur_branch = _Branch(kind="elif", directive_line=child.start_point[0] + 1)
            branches.append(cur_branch)
        elif t == "#else":
            cur_branch = _Branch(kind="else", directive_line=child.start_point[0] + 1)
            branches.append(cur_branch)
        elif t == "preproc_elif":
            # 嵌套的 preproc_elif 子节点
            for sub in child.children:
                if sub.type == "#elif":
                    cur_branch = _Branch(kind="elif", directive_line=sub.start_point[0] + 1)
                    branches.append(cur_branch)
                elif sub.type == "preproc_else":
                    for subsub in sub.children:
                        if subsub.type == "#else":
                            cur_branch = _Branch(kind="else", directive_line=subsub.start_point[0] + 1)
                            branches.append(cur_branch)
                elif sub.type not in ("#elif", "\n"):
                    if cur_branch is not None:
                        if cur_branch.condition_node is None and _is_condition(sub):
                            cur_branch.condition_node = sub
                        else:
                            cur_branch.body_nodes.append(sub)
        elif t == "preproc_else":
            # 嵌套的 preproc_else 子节点
            for sub in child.children:
                if sub.type == "#else":
                    cur_branch = _Branch(kind="else", directive_line=sub.start_point[0] + 1)
                    branches.append(cur_branch)
                elif sub.type != "\n":
                    if cur_branch is not None:
                        cur_branch.body_nodes.append(sub)
        elif t == "#endif":
            continue
        elif t == "\n":
            continue
        else:
            # body 节点 or condition
            if cur_branch is not None:
                if cur_branch.condition_node is None and _is_condition(child) and cur_branch.kind in ("if", "elif"):
                    cur_branch.condition_node = child
                else:
                    cur_branch.body_nodes.append(child)
    return branches


def _is_condition(node: Node) -> bool:
    """判断节点是否是 #if/#elif 的条件表达式。"""
    return node.type in (
        "number_literal", "identifier", "binary_expression", "unary_expression",
        "preproc_defined", "call_expression", "parenthesized_expression",
    )


def _eval_if_condition(state: _InterpState, branch: _Branch) -> bool:
    """求值 #if 条件。迭代 1 直通版：只认 number_literal。"""
    cond = branch.condition_node
    if cond is None:
        return False
    return _eval_condition(state, cond)


def _eval_elif_condition(state: _InterpState, branch: _Branch) -> bool:
    """求值 #elif 条件。"""
    return _eval_if_condition(state, branch)


def _eval_condition(state: _InterpState, node: Node) -> bool:
    """求值条件表达式（迭代 2：接入 expr_parser；迭代 3：传 function_macros）。"""
    from .expr_parser import eval_condition
    return eval_condition(
        node, state.defines,
        state.result.condition_diagnostics,
        node.start_point[0] + 1,
        state.function_macros,
    )


def _interpret_preproc_ifdef(state: _InterpState, node: Node) -> None:
    """处理 #ifdef X / #ifndef X ... #endif。

    AST 形状：
    preproc_ifdef
      #ifdef | #ifndef
      identifier
      <body...>
      #endif
    """
    is_ifndef = any(c.type == "#ifndef" for c in node.children)
    name_node = None
    body_nodes = []
    directive_line = node.start_point[0] + 1
    endif_line = -1
    for child in node.children:
        if child.type in ("#ifdef", "#ifndef"):
            continue
        if child.type == "identifier" and name_node is None:
            name_node = child
        elif child.type == "#endif":
            endif_line = child.start_point[0] + 1
        elif child.type != "\n":
            body_nodes.append(child)

    name = text_of(name_node) if name_node else ""
    defined = name in state.defines
    if is_ifndef:
        # #ifndef X: X 未定义时 active
        branch_active = not defined
    else:
        # #ifdef X: X 已定义时 active
        branch_active = defined

    parent_active = state.active
    branch_id = state.next_branch_id
    state.next_branch_id += 1
    parent_sig = list(state.cur_sig)

    if parent_active and branch_active:
        state.cur_sig.append((branch_id, 0))
        state.active = True
        state.macro_active = True
    else:
        state.active = False
        state.macro_active = False

    _write_line_state(state, directive_line, parent_active)
    for body_node in body_nodes:
        _interpret_body_node(state, body_node)

    if endif_line >= 0:
        state.active = parent_active
        state.cur_sig = parent_sig
        _write_line_state(state, endif_line, parent_active)
        state.result.branch_merges.append(BranchMergeInfo(
            line=endif_line, branch_id=branch_id,
            active_branch_index=0 if (parent_active and branch_active) else -1,
            branch_count=1,
        ))


def _interpret_body_node(state: _InterpState, node: Node) -> None:
    """处理分支体内的节点（递归处理嵌套 #if）。"""
    _dispatch_node(state, node)


def _handle_preproc_def(state: _InterpState, node: Node) -> None:
    """处理 #define NAME body。"""
    _write_line_state_for_node(state, node)
    if not state.macro_active:
        return
    _apply_define(state, node)


def _handle_preproc_function_def(state: _InterpState, node: Node) -> None:
    """处理 #define NAME(params) body。"""
    _write_line_state_for_node(state, node)
    if not state.macro_active:
        return
    _apply_function_define(state, node)


def _handle_preproc_undef(state: _InterpState, node: Node) -> None:
    """处理 #undef NAME。"""
    _write_line_state_for_node(state, node)
    if not state.macro_active:
        return
    _apply_undef(state, node)


def _handle_preproc_call(state: _InterpState, node: Node) -> None:
    """处理其他预处理指令（#include / #undef / #pragma 等）。

    tree-sitter-cpp 把 #undef / #include 等解析成 preproc_call + preproc_directive，
    需要从文本里识别指令类型。
    """
    _write_line_state_for_node(state, node)
    if not state.macro_active:
        return
    txt = text_of(node).strip()
    if txt.startswith("#undef"):
        # 提取宏名
        parts = txt.split()
        if len(parts) >= 2:
            name = parts[1].strip()
            state.defines.pop(name, None)


def _apply_define(state: _InterpState, node: Node) -> None:
    """object-like #define: #define NAME body → 加进 defines。"""
    name = None
    body = ""
    for child in node.children:
        if child.type == "identifier" and name is None:
            name = text_of(child)
        elif child.type == "preproc_arg":
            body = text_of(child)
    if name and state.macro_active:
        try:
            state.defines[name] = int(body.strip())
        except (ValueError, AttributeError):
            # 非数字宏，标 1（C 预处理语义：#define X → X 是 defined）
            state.defines[name] = 1


def _apply_function_define(state: _InterpState, node: Node) -> None:
    """function-like #define: 暂存但不在 #if 里直接展开（迭代 3 部分支持）。"""
    name = None
    for child in node.children:
        if child.type == "identifier" and name is None:
            name = text_of(child)
    if name and state.macro_active:
        # function-like 宏不进 defines（defines 是 int），
        # 但标 defined(name)=1（#ifdef NAME 时命中）
        if name not in state.defines:
            state.defines[name] = 1


def _apply_undef(state: _InterpState, node: Node) -> None:
    """#undef NAME → 从 defines 删。"""
    name = None
    for child in node.children:
        if child.type == "identifier":
            name = text_of(child)
            break
    if name and state.macro_active:
        state.defines.pop(name, None)


def _write_line_state(state: _InterpState, line: int, active: bool) -> None:
    """写一行的 active 状态 + branch_sig（直接赋值，inactive 覆盖成 False）。

    line: 1-based
    branch_sig 存原始 [(branch_id, branch_index), ...]，
    签名键/家族键由查询层调 branch_signature 模块算（避免重复计算）。
    """
    if 1 <= line <= state.line_count:
        idx = line - 1
        state.result.line_active[idx] = active
        state.result.branch_sigs[idx] = list(state.cur_sig)


def _write_line_state_for_node(state: _InterpState, node: Node) -> None:
    """为普通节点写行状态（按节点起始行）。

    active 时写 True，inactive 时写 False（覆盖默认 True）。
    """
    active = state.active
    for line in range(node.start_point[0] + 1, node.end_point[0] + 2):
        if 1 <= line <= state.line_count:
            idx = line - 1
            state.result.line_active[idx] = active
            state.result.branch_sigs[idx] = list(state.cur_sig)


def _find_endif_line(if_node: Node) -> int:
    """找 preproc_if 节点的 #endif 行号。"""
    for child in if_node.children:
        if child.type == "#endif":
            return child.start_point[0] + 1
    return -1


__all__ = ["build_preprocessor_view"]
