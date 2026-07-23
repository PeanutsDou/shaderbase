"""indexer — 把抽取器产出的节点 + PreprocessorView 签名存进 SQLite。

吃 NodeExtractor 的节点列表 + build_preprocessor_view 的 branch_sigs，
按 DEV_PLAN §3.2 schema 写入 nodes 表。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from tree_sitter import Node

from ..extract.nodes import NodeExtractor, ShaderNode
from ..extract.edges import EdgeExtractor, Edge
from ..parser.ast_utils import walk, text_of
from ..parser.tree_sitter_loader import parser as get_parser
from ..preprocessor.branch_signature import branch_signature_key, branch_family_key
from ..preprocessor.interpreter import build_preprocessor_view


def index_file(
    conn: sqlite3.Connection,
    file_path: str,
    source: bytes,
    project: str,
    extractor: Optional[NodeExtractor] = None,
    edge_extractor: Optional[EdgeExtractor] = None,
    root_path: str = "",
) -> dict:
    """索引单个文件：parse → 抽取节点+边 → 算 PV 签名 → 写入 SQLite。

    file_path: 相对 root_path 的相对路径（如 'base/animated_grass.nsf'），
               或绝对路径（兼容旧数据）。
    root_path: shader 源码根目录（绝对路径或相对项目根）。空串时 file_path 当绝对路径用。
    返回 {node_count, edge_count, parsed_ok, error_count}。
    """
    extractor = extractor or NodeExtractor()
    edge_extractor = edge_extractor or EdgeExtractor()
    parser = get_parser()

    # parse
    tree = parser.parse(source)
    parsed_ok = not tree.root_node.has_error
    error_count = _count_errors(tree.root_node)

    # 算 PV（空 defines，索引阶段全分支视图）
    view = build_preprocessor_view(tree.root_node, source, {})

    # 抽节点
    nodes = extractor.extract_file(source, file_path)
    # 抽边（传 nodes 给 USES_UNIFORM 匹配本文件 Uniform 名用）
    edges = edge_extractor.extract_file(source, file_path, view, nodes)
    # 给 CALLS 边填 source_name（找所在函数名）——用本文件的抽取结果查
    _fill_call_sources_from_nodes(edges, nodes)

    # 写入 SQLite
    _delete_file_nodes(conn, file_path, project)
    _delete_file_edges(conn, file_path, project)
    node_ids = _insert_nodes(conn, nodes, file_path, project, view)
    _insert_edges(conn, edges, file_path, project)
    _insert_file_meta(conn, file_path, project, source, len(nodes), len(edges), parsed_ok, error_count, root_path)

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "parsed_ok": parsed_ok,
        "error_count": error_count,
    }


def index_project(
    conn: sqlite3.Connection,
    root_path: str,
    project: str,
    extractor: Optional[NodeExtractor] = None,
    edge_extractor: Optional[EdgeExtractor] = None,
    resolve_calls: bool = True,
) -> dict:
    """全量索引一个项目。

    遍历 root_path 下所有 .nsf/.hlsl/.fxh，逐文件索引。
    索引完成后可选 resolve CALLS 边（跨文件找函数定义）。
    """
    extractor = extractor or NodeExtractor()
    edge_extractor = edge_extractor or EdgeExtractor()
    SKIP = {"no_source", "no_source_pc", "pipeline_output", "bin", ".git"}
    EXTS = {".nsf", ".hlsl", ".fxh"}

    # 注册项目
    conn.execute(
        "INSERT OR REPLACE INTO projects (name, root_path, updated_at) VALUES (?, ?, datetime('now'))",
        (project, root_path),
    )

    # 清旧数据
    conn.execute("DELETE FROM nodes WHERE project = ?", (project,))
    conn.execute("DELETE FROM edges WHERE project = ?", (project,))
    conn.execute("DELETE FROM file_meta WHERE project = ?", (project,))
    conn.execute("DELETE FROM reverse_deps WHERE project = ?", (project,))

    total_nodes = 0
    total_edges = 0
    crash_count = 0
    error_files = 0

    # 解析 root_path 成绝对路径（用于 os.walk 和文件读取）
    from .connection import resolve_root_path
    abs_root = resolve_root_path(root_path)

    files = []
    for dp, dns, fns in os.walk(abs_root):
        dns[:] = [d for d in dns if d not in SKIP]
        for f in fns:
            if os.path.splitext(f)[1].lower() in EXTS:
                files.append(os.path.join(dp, f))

    for i, path in enumerate(files, 1):
        try:
            with open(path, "rb") as fh:
                src = fh.read()
            # file_path 存相对 root_path 的相对路径（可移植）
            rel_path = os.path.relpath(path, abs_root).replace("\\", "/")
            result = index_file(
                conn, rel_path, src, project,
                extractor, edge_extractor, abs_root,
            )
            total_nodes += result["node_count"]
            total_edges += result["edge_count"]
            if not result["parsed_ok"]:
                error_files += 1
        except Exception as e:
            crash_count += 1
            if crash_count <= 5:
                print(f"  CRASH {path}: {e}")
        if i % 200 == 0:
            print(f"  {i}/{len(files)}  (nodes={total_nodes}, edges={total_edges})")

    # 写 reverse_deps（include 反向）
    _build_reverse_deps(conn, project)

    # resolve CALLS 边
    calls_resolved = None
    if resolve_calls:
        from ..extract.resolve_calls import resolve_calls as _resolve
        calls_resolved = _resolve(conn, project, root_path)

    conn.commit()
    return {
        "total_files": len(files),
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "crash_count": crash_count,
        "error_files": error_files,
        "calls_resolved": calls_resolved,
    }


def _count_errors(root: Node) -> int:
    """统计 AST 里 ERROR 节点数。"""
    return sum(1 for n in walk(root) if n.type == "ERROR")


def _delete_file_nodes(conn: sqlite3.Connection, file_path: str, project: str) -> None:
    """删除某文件的所有旧节点（增量更新用）。"""
    conn.execute(
        "DELETE FROM nodes WHERE file_path = ? AND project = ?",
        (file_path, project),
    )
    conn.execute(
        "DELETE FROM file_meta WHERE file_path = ? AND project = ?",
        (file_path, project),
    )


def _delete_file_edges(conn: sqlite3.Connection, file_path: str, project: str) -> None:
    """删除某文件的所有旧边（增量更新用）。"""
    conn.execute(
        "DELETE FROM edges WHERE source_file = ? AND project = ?",
        (file_path, project),
    )


def _insert_edges(
    conn: sqlite3.Connection,
    edges: list,
    file_path: str,
    project: str,
) -> None:
    """插入边列表。

    source_id/target_id 暂填 0，resolve 阶段 UPDATE。
    """
    for e in edges:
        props_json = json.dumps(e.properties, ensure_ascii=False, default=str)
        conn.execute(
            """INSERT INTO edges
               (kind, source_file, source_line, source_name, target_name,
                source_id, target_id, properties, conditional_signature, project)
               VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
            (e.kind, file_path, e.source_line, e.source_name,
             e.target_name, props_json, e.conditional_signature, project),
        )


def _fill_call_sources_from_nodes(
    edges: list,
    nodes: list,
) -> None:
    """给 CALLS 边填 source_name（找所在函数名）。

    用本文件抽取出的 Function 节点列表查（不查 SQLite，因为此时还没插入）。
    找 line 落在哪个 function 的 [line, end_line] 范围内。
    """
    funcs = [(n.name, n.line, n.end_line) for n in nodes if n.kind == "Function"]
    funcs.sort(key=lambda x: x[1] or 0)

    for e in edges:
        if e.kind != "CALLS":
            continue
        for fname, fstart, fend in funcs:
            if fstart and fend and fstart <= e.source_line <= fend:
                e.source_name = fname
                break


def _build_reverse_deps(conn: sqlite3.Connection, project: str) -> None:
    """构建反向依赖图（include 反向）。

    INCLUDES 边：source_file 依赖 target_name（include 路径）
    reverse_deps: source_file = 被 include 的文件, dependent_file = include 它的文件
    """
    cur = conn.execute(
        """SELECT DISTINCT source_file, target_name FROM edges
           WHERE project = ? AND kind = 'INCLUDES'""",
        (project,),
    )
    for row in cur:
        # target_name 是 include 路径，不是绝对路径
        # reverse_deps 存路径文本，resolve 由查询层做
        conn.execute(
            """INSERT OR REPLACE INTO reverse_deps
               (source_file, dependent_file, dep_kind, project)
               VALUES (?, ?, 'INCLUDE', ?)""",
            (row["target_name"], row["source_file"], project),
        )


def _insert_nodes(
    conn: sqlite3.Connection,
    nodes: list[ShaderNode],
    file_path: str,
    project: str,
    pv_view,
) -> list[int]:
    """插入节点列表，返回 node id 列表。

    每个节点查 PV 的 branch_sigs[line-1] 算 conditional_signature + branch_family。
    """
    ids = []
    for n in nodes:
        # 算条件签名
        line_idx = n.line - 1
        sig = []
        if 0 <= line_idx < len(pv_view.branch_sigs):
            sig = pv_view.branch_sigs[line_idx]
        cond_sig = branch_signature_key(sig) if sig else None
        fam_key = branch_family_key(sig) if sig else None

        props_json = json.dumps(n.properties, ensure_ascii=False, default=str)

        cur = conn.execute(
            """INSERT INTO nodes
               (kind, name, qualified_name, file_path, line, start_col,
                end_line, end_col, properties, conditional_signature, branch_family, project)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (n.kind, n.name, n.name, file_path, n.line, n.start_col,
             n.end_line, n.end_col, props_json, cond_sig, fam_key, project),
        )
        ids.append(cur.lastrowid)
    return ids


def _insert_file_meta(
    conn: sqlite3.Connection,
    file_path: str,
    project: str,
    source: bytes,
    node_count: int,
    edge_count: int,
    parsed_ok: bool,
    error_count: int,
    root_path: str = "",
) -> None:
    """写入文件元数据（增量索引用）。

    root_path 非空时，file_path 是相对路径，stat 用 os.path.join(root_path, file_path)。
    root_path 空时，file_path 当绝对路径用（兼容旧数据）。
    """
    if root_path:
        abs_path = os.path.join(root_path, file_path)
    else:
        abs_path = file_path
    stat = os.stat(abs_path) if os.path.exists(abs_path) else None
    content_hash = hashlib.md5(source).hexdigest()

    conn.execute(
        """INSERT OR REPLACE INTO file_meta
           (file_path, project, mtime, size, content_hash, node_count,
            edge_count, parsed_ok, error_count, last_indexed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (file_path, project,
         int(stat.st_mtime) if stat else 0,
         len(source),
         content_hash,
         node_count, edge_count,
         int(parsed_ok), error_count),
    )


__all__ = ["index_file", "index_project"]
