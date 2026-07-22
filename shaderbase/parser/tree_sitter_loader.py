"""tree_sitter_loader — 加载自研 g66-shader-grammar，暴露复用的 Parser。

grammar 子项目产出 `tree_sitter_g66_shader` Python 绑定包（同进程 in-process 加载，
跑全库 1227 个文件 ~2.2 秒，远快于 subprocess 调 tree-sitter.exe）。
本模块只负责把它包成 shaderbase 自己的 Parser 单例，抽取器直接拿去用。
"""
from __future__ import annotations

from functools import lru_cache

from tree_sitter import Language, Parser

import tree_sitter_g66_shader


@lru_cache(maxsize=1)
def language() -> Language:
    """g66-shader grammar 的 Language 对象（单例复用）。"""
    return Language(tree_sitter_g66_shader.language())


@lru_cache(maxsize=1)
def parser() -> Parser:
    """全局复用的 Parser。tree-sitter Parser 本身是线程不安全的写对象，
    单进程单线程顺序解析够用；并发场景另造 Parser 实例。"""
    return Parser(language())


__all__ = ["language", "parser"]
