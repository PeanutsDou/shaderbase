"""view — PreprocessorView 输出数据结构（对齐 nsp preprocessor_view.hpp）。

数据结构对照见 DEV_PLAN §2.4。只定义结构，不算算法。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Flag, auto


class MacroSource(Flag):
    """宏来源标记（对齐 nsp PreprocessorMacroReplacement 的 6 个 source* 布尔）。"""
    NONE = 0
    ART_DEFAULT_ZERO = auto()           # #art BOOL/INT 默认 0
    ART_COMPANION_CONSTANT = auto()      # #art 的 companion enum 常量
    COMPILER_PRIVATE_CONSTANT = auto()  # 编译器私有常量（shaderbase 不接 shadercompiler，保留字段）
    CONFIGURED = auto()                 # 用户配置的 nsf.preprocessorMacros
    COMPILER_MACRO_SNAPSHOT = auto()    # 编译器宏快照（保留字段）
    NUMERIC_DEFINE = auto()             # 用户 -D defines
    IFNDEF_DEFAULT = auto()            # #ifndef X #define X 0 的 ifndef 默认


@dataclass
class MacroReplacement:
    """单个宏的替换信息（对齐 nsp PreprocessorMacroReplacement）。"""
    function_like: bool = False
    replacement: str = ""
    source_uri: str = ""
    source_line: int = -1
    source_start: int = 0
    source_end: int = 0
    source: MacroSource = MacroSource.NONE
    # nsp 的 6 个独立布尔，合并到 source enum；保留细分访问
    synthesized_zero: bool = False       # 数值上下文未定义宏合成 0


@dataclass
class MacroEvent:
    """源码级的宏变更事件（对齐 nsp PreprocessorMacroEvent）。

    #define / #undef / 合成 0 等都会产生事件，记录"在第 line 行宏 name 变成了什么"。
    """
    line: int = 0
    name: str = ""
    undefined: bool = False
    replacement: MacroReplacement = field(default_factory=MacroReplacement)
    source_uri: str = ""
    source_line: int = -1


@dataclass
class BranchMergeInfo:
    """#endif 处的分支汇合点（对齐 nsp PreprocessorBranchMergeInfo）。"""
    line: int = 0
    branch_id: int = 0
    active_branch_index: int = -1   # 选中的分支索引（-1 = 没选中任何分支）
    branch_count: int = 0


@dataclass
class ConditionDiagnostic:
    """条件编译诊断（对齐 nsp PreprocessorConditionDiagnostic）。

    shaderbase 精简版，只做两类：
    - 未定义宏在数值上下文用 0 替代
    - #if 表达式语法错
    """
    line: int = 0
    message: str = ""
    macro_name: str = ""
    synthesized_zero: bool = False
    inactive_branch: bool = False
    branch_id: int = 0
    branch_index: int = 0


@dataclass
class PreprocessorView:
    """PreprocessorView 主输出结构（对齐 nsp PreprocessorView）。

    索引阶段（空 defines）：line_active 全 False（不算 active），branch_sigs 全算
    查询阶段（Agent macros）：line_active 按 macros 算，branch_sigs 也算
    """
    line_active: list[bool] = field(default_factory=list)
    branch_sigs: list[list[tuple[int, int]]] = field(default_factory=list)
    condition_diagnostics: list[ConditionDiagnostic] = field(default_factory=list)
    active_include_uris: list[str] = field(default_factory=list)
    initial_macros: dict[str, MacroReplacement] = field(default_factory=dict)
    macro_events: list[MacroEvent] = field(default_factory=list)
    branch_merges: list[BranchMergeInfo] = field(default_factory=list)

    def extend_to_lines(self, n: int) -> None:
        """把列表扩展到 n 行（不足补默认值）。

        line_active 默认 True（顶层行默认 active，条件分支内才覆盖成 False）。
        branch_sigs 默认 []（无分支）。
        """
        while len(self.line_active) < n:
            self.line_active.append(True)
        while len(self.branch_sigs) < n:
            self.branch_sigs.append([])


__all__ = [
    "MacroSource", "MacroReplacement", "MacroEvent",
    "BranchMergeInfo", "ConditionDiagnostic", "PreprocessorView",
]
