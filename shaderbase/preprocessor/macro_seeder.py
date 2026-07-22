"""macro_seeder — 6 级宏优先级链（迭代 6）。

对齐 nsp seedInitialPreprocessorMacros，按优先级从低到高注入初始宏表。
后覆盖前。

shaderbase 砍掉了 compiler private/snapshot（不接 shadercompiler），4 级：
  L1: #art companion constants（最低）
  L2: #art default zero（BOOL/INT → 0）
  L3: configured_macros（Agent 传入的 nsf.preprocessorMacros）
  L4: numeric defines（Agent 传入的 -D defines，最高）

每级注入时记录 MacroReplacement.source，给宏来源追溯用。
"""
from __future__ import annotations

from typing import Optional

from tree_sitter import Node

from .art_macros import ArtMacro, collect_art_macros, inject_art_defaults
from .view import MacroReplacement, MacroSource


def seed_initial_macros(
    defines: dict[str, int],
    art_macros: list[ArtMacro],
    configured_macros: Optional[dict[str, str]] = None,
) -> dict[str, MacroReplacement]:
    """按 6 级优先级链注入初始宏表，返回 {name: MacroReplacement}。

    优先级从低到高：art companion < art default < configured < numeric defines
    后覆盖前。但 L4 numeric defines 只覆盖用户显式传入的，
    不覆盖由 art 注入的（避免 art 的 source 标记被 L4 冲掉）。
    """
    out: dict[str, MacroReplacement] = {}

    # 记录 art 注入的宏名（这些不该被 L4 当 numeric define 覆盖 source）
    art_names = set()
    for macro in art_macros:
        if macro.art_type in ("BOOL", "INT") and macro.name:
            art_names.add(macro.name)
        for cname, _ in (macro.companion_constants or []):
            if cname:
                art_names.add(cname)

    # L1: art companion constants
    for macro in art_macros:
        for cname, cval in (macro.companion_constants or []):
            if cname:
                out[cname] = MacroReplacement(
                    replacement=str(cval),
                    source=MacroSource.ART_COMPANION_CONSTANT,
                )

    # L2: art default zero（BOOL/INT → 0）
    for macro in art_macros:
        if macro.art_type in ("BOOL", "INT") and macro.name:
            out[macro.name] = MacroReplacement(
                replacement="0",
                source=MacroSource.ART_DEFAULT_ZERO,
                source_line=macro.line,
            )

    # L3: configured macros（Agent 传入的 nsf.preprocessorMacros）
    if configured_macros:
        for name, repl in configured_macros.items():
            out[name] = MacroReplacement(
                replacement=repl,
                source=MacroSource.CONFIGURED,
            )

    # L4: numeric defines（Agent 显式传入的 -D，最高优先级）
    # 只覆盖非 art 来源的，保留 art 的 source 标记
    for name, val in defines.items():
        if name not in art_names:
            out[name] = MacroReplacement(
                replacement=str(val),
                source=MacroSource.NUMERIC_DEFINE,
            )

    return out


def seed_defines(
    defines: dict[str, int],
    art_macros: list[ArtMacro],
    configured_macros: Optional[dict[str, str]] = None,
) -> dict[str, int]:
    """按优先级链注入 defines（int 版，给 #if 求值用）。

    返回合并后的 defines dict。优先级：art < configured < numeric defines。
    """
    out: dict[str, int] = {}

    # L1+L2: art 默认 0 + companion
    out = inject_art_defaults(out, art_macros)

    # L3: configured macros → 尝试解析成 int，非数字的标 1
    if configured_macros:
        for name, repl in configured_macros.items():
            try:
                out[name] = int(repl)
            except ValueError:
                out[name] = 1

    # L4: numeric defines（最高，覆盖）
    out.update(defines)

    return out


__all__ = ["seed_initial_macros", "seed_defines"]
