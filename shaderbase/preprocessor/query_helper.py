"""query_helper — 查询阶段算 active（DEV_PLAN §2 双视图核心）。

索引阶段用空 defines 算 branch_sigs 存 SQLite；
查询阶段 Agent 传 macros，本模块重算 line_active，按行过滤边/节点。

缓存：同 file_path + macros 内容 hash → 复用 PreprocessorView（LRU 100）。
文件改了 mtime 变 → 缓存 key 含 mtime，自动失效。
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Optional

from tree_sitter import Node

from .interpreter import build_preprocessor_view
from ..parser.tree_sitter_loader import parser as get_parser
from .view import PreprocessorView


def _macros_key(macros: dict) -> str:
    """把 macros dict 序列化成稳定 hash key。"""
    if not macros:
        return ""
    items = sorted(macros.items())
    return ";".join(f"{k}={v}" for k, v in items)


def _file_signature(file_path: str) -> str:
    """文件签名：mtime + size，用于缓存失效。"""
    try:
        st = os.stat(file_path)
        return f"{int(st.st_mtime)}.{st.st_size}"
    except OSError:
        return "0.0"


# LRU cache：key = (file_path, mtime.size, macros_key)
# 100 个文件 × 平均 500 行 view，内存占用可接受
@lru_cache(maxsize=100)
def _build_view_cached(
    file_path: str, file_sig: str, macros_key: str,
) -> Optional[PreprocessorView]:
    """缓存的 build_preprocessor_view。

    file_sig 变了（文件改了）→ key 不同 → 自动重建。
    macros_key 变了 → key 不同 → 重建。
    """
    try:
        with open(file_path, "rb") as f:
            source = f.read()
    except OSError:
        return None
    parser = get_parser()
    tree = parser.parse(source)
    # 解析 macros_key 回 dict
    macros: dict[str, int] = {}
    if macros_key:
        for part in macros_key.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    macros[k] = int(v)
                except ValueError:
                    macros[k] = 1
    return build_preprocessor_view(tree.root_node, source, macros)


def compute_active_for_file(
    file_path: str, macros: Optional[dict[str, int]] = None,
) -> Optional[PreprocessorView]:
    """读源码 → parse → build_preprocessor_view(macros) → 返回 view。

    带 LRU 缓存（同 file + macros → 复用）。文件不存在返回 None。
    """
    if not file_path or not os.path.exists(file_path):
        return None
    file_sig = _file_signature(file_path)
    macros_key = _macros_key(macros or {})
    return _build_view_cached(file_path, file_sig, macros_key)


def is_line_active(
    file_path: str, line: int, macros: Optional[dict[str, int]] = None,
) -> Optional[bool]:
    """查某文件某行在某 macros 配置下是否 active。

    返回 None = 文件不存在或行号越界（保守当 active 处理）。
    """
    view = compute_active_for_file(file_path, macros)
    if view is None:
        return None
    idx = line - 1
    if 0 <= idx < len(view.line_active):
        return view.line_active[idx]
    return None


def annotate_edges_with_active(
    edges: list[dict], macros: Optional[dict[str, int]] = None,
) -> list[dict]:
    """给边列表加 active 字段。

    edges: [{..., "file_path": str, "line": int, ...}, ...]
    macros: None = 不算 active（保持现状）；{} = 空 macros 算；{KEY:1} = 指定 macros
    返回同列表，每条边加 "active": bool（macros=None 时不加）。
    """
    if macros is None:
        return edges
    # 按 file_path 分组，每个文件只算一次 view
    file_views: dict[str, Optional[PreprocessorView]] = {}
    for e in edges:
        fp = e.get("file_path") or e.get("source_file")
        if fp and fp not in file_views:
            file_views[fp] = compute_active_for_file(fp, macros)
    for e in edges:
        fp = e.get("file_path") or e.get("source_file")
        line = e.get("line") or e.get("source_line")
        view = file_views.get(fp) if fp else None
        if view is not None and line and 1 <= line <= len(view.line_active):
            e["active"] = view.line_active[line - 1]
        else:
            e["active"] = None    # 保守：算不出来不标
    return edges


__all__ = [
    "compute_active_for_file",
    "is_line_active",
    "annotate_edges_with_active",
]
