"""queries — SQLite 查询函数（给 web API 用）。

复用 store 的 SQLite 连接，按 web API 需要的形状查询。
支持 macros 参数（DEV_PLAN §2 双视图）：传了就给边标 active。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from ..preprocessor.query_helper import annotate_edges_with_active


def get_overview(conn: sqlite3.Connection, project: str) -> dict:
    """概览聚合：按 kind / 顶层目录统计。"""
    # 节点按 kind
    kind_counts = {}
    cur = conn.execute(
        "SELECT kind, COUNT(*) FROM nodes WHERE project = ? GROUP BY kind ORDER BY COUNT(*) DESC",
        (project,),
    )
    for row in cur:
        kind_counts[row[0]] = row[1]

    # 边按 kind
    edge_counts = {}
    cur = conn.execute(
        "SELECT kind, COUNT(*) FROM edges WHERE project = ? GROUP BY kind ORDER BY COUNT(*) DESC",
        (project,),
    )
    for row in cur:
        edge_counts[row[0]] = row[1]

    # 按顶层目录
    dir_counts = {}
    cur = conn.execute(
        "SELECT file_path FROM nodes WHERE project = ?",
        (project,),
    )
    for row in cur:
        parts = row[0].replace("\\", "/").split("/")
        # 找 project root 之后的第一个目录
        for i, p in enumerate(parts):
            if p == project or i >= len(parts) - 2:
                continue
        # 简单取倒数第三段（shader-source/xxx/yyy.hlsl → xxx）
        segs = row[0].replace("\\", "/").split("/")
        if len(segs) >= 2:
            d = segs[-2] if len(segs) >= 2 else "?"
            dir_counts[d] = dir_counts.get(d, 0) + 1

    # 文件数
    cur = conn.execute(
        "SELECT COUNT(*) FROM file_meta WHERE project = ?", (project,)
    )
    file_count = cur.fetchone()[0]

    # 带条件签名的节点/边
    cur = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE project = ? AND conditional_signature IS NOT NULL",
        (project,),
    )
    cond_nodes = cur.fetchone()[0]
    cur = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE project = ? AND conditional_signature IS NOT NULL",
        (project,),
    )
    cond_edges = cur.fetchone()[0]

    return {
        "project": project,
        "file_count": file_count,
        "node_count": sum(kind_counts.values()),
        "edge_count": sum(edge_counts.values()),
        "nodes_by_kind": kind_counts,
        "edges_by_kind": edge_counts,
        "nodes_by_dir": dir_counts,
        "conditional_nodes": cond_nodes,
        "conditional_edges": cond_edges,
    }


def search_nodes(
    conn: sqlite3.Connection, project: str,
    name_pattern: Optional[str] = None,
    kind: Optional[str] = None,
    file_pattern: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """搜节点。"""
    sql = "SELECT id, kind, name, file_path, line, conditional_signature, properties FROM nodes WHERE project = ?"
    params: list = [project]
    if name_pattern:
        sql += " AND name LIKE ?"
        params.append(f"%{name_pattern}%")
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if file_pattern:
        sql += " AND file_path LIKE ?"
        params.append(f"%{file_pattern}%")
    sql += " ORDER BY kind, name LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cur = conn.execute(sql, params)
    nodes = []
    for row in cur:
        props = json.loads(row["properties"]) if row["properties"] else {}
        nodes.append({
            "id": row["id"],
            "kind": row["kind"],
            "name": row["name"],
            "file_path": row["file_path"],
            "line": row["line"],
            "conditional_signature": row["conditional_signature"],
            "properties": props,
        })
    return {"nodes": nodes, "count": len(nodes)}


def get_node(conn: sqlite3.Connection, node_id: int) -> Optional[dict]:
    """节点详情。"""
    cur = conn.execute(
        "SELECT * FROM nodes WHERE id = ?", (node_id,)
    )
    row = cur.fetchone()
    if not row:
        return None
    props = json.loads(row["properties"]) if row["properties"] else {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "qualified_name": row["qualified_name"],
        "file_path": row["file_path"],
        "line": row["line"],
        "start_col": row["start_col"],
        "end_line": row["end_line"],
        "end_col": row["end_col"],
        "properties": props,
        "conditional_signature": row["conditional_signature"],
        "branch_family": row["branch_family"],
        "project": row["project"],
    }


def get_connections(conn: sqlite3.Connection, node_id: int, project: str) -> dict:
    """节点的所有连接（outbound + inbound），按 kind 分组。"""
    outbound = {}
    inbound = {}

    # outbound: node_id 是 source
    # 先用 source_name 查（边表存名字不存 id）
    node = get_node(conn, node_id)
    if not node:
        return {"outbound": {}, "inbound": {}}

    name = node["name"]

    # CALLS outbound: source_name == name
    cur = conn.execute(
        """SELECT kind, target_name, source_file, source_line, conditional_signature, properties
           FROM edges WHERE project = ? AND kind = 'CALLS' AND source_name = ?
           LIMIT 50""",
        (project, name),
    )
    for row in cur:
        outbound.setdefault("CALLS", []).append({
            "target": row["target_name"],
            "file": row["source_file"],
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
        })

    # CALLS inbound: target_name == name
    cur = conn.execute(
        """SELECT kind, source_name, source_file, source_line, conditional_signature, properties
           FROM edges WHERE project = ? AND kind = 'CALLS' AND target_name = ?
           LIMIT 50""",
        (project, name),
    )
    for row in cur:
        props = json.loads(row["properties"]) if row["properties"] else {}
        inbound.setdefault("CALLS", []).append({
            "source": row["source_name"],
            "file": row["source_file"],
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
            "resolved_to_file": props.get("resolved_to_file"),
        })

    # HAS_MEMBER: source_name == name (struct → field)
    cur = conn.execute(
        """SELECT target_name, source_line, conditional_signature
           FROM edges WHERE project = ? AND kind = 'HAS_MEMBER' AND source_name = ?
           LIMIT 50""",
        (project, name),
    )
    for row in cur:
        outbound.setdefault("HAS_MEMBER", []).append({
            "target": row["target_name"],
            "line": row["source_line"],
        })

    # IS_ENTRY_POINT: source_name == name (technique → entry func)
    cur = conn.execute(
        """SELECT target_name, source_line, properties
           FROM edges WHERE project = ? AND kind = 'IS_ENTRY_POINT' AND source_name = ?
           LIMIT 20""",
        (project, name),
    )
    for row in cur:
        props = json.loads(row["properties"]) if row["properties"] else {}
        outbound.setdefault("IS_ENTRY_POINT", []).append({
            "target": row["target_name"],
            "stage": props.get("stage"),
        })

    return {"outbound": outbound, "inbound": inbound}


def get_neighbors(
    conn: sqlite3.Connection, node_id: int, project: str,
    limit: int = 50,
) -> dict:
    """1 跳邻居（给 cytoscape 画子图用）。"""
    node = get_node(conn, node_id)
    if not node:
        return {"nodes": [], "edges": []}

    name = node["name"]
    neighbor_names = set()
    edges_out = []
    edges_in = []

    # CALLS outbound
    cur = conn.execute(
        """SELECT target_name, source_file, source_line, conditional_signature, properties
           FROM edges WHERE project = ? AND kind = 'CALLS' AND source_name = ?
           LIMIT ?""",
        (project, name, limit),
    )
    for row in cur:
        props = json.loads(row["properties"]) if row["properties"] else {}
        neighbor_names.add(row["target_name"])
        edges_out.append({
            "kind": "CALLS",
            "source": name,
            "target": row["target_name"],
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
            "resolved_to_file": props.get("resolved_to_file"),
        })

    # CALLS inbound
    cur = conn.execute(
        """SELECT source_name, source_file, source_line, conditional_signature
           FROM edges WHERE project = ? AND kind = 'CALLS' AND target_name = ?
           LIMIT ?""",
        (project, name, limit),
    )
    for row in cur:
        neighbor_names.add(row["source_name"])
        edges_in.append({
            "kind": "CALLS",
            "source": row["source_name"],
            "target": name,
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
        })

    # HAS_MEMBER
    cur = conn.execute(
        """SELECT target_name FROM edges WHERE project = ? AND kind = 'HAS_MEMBER' AND source_name = ? LIMIT ?""",
        (project, name, limit),
    )
    for row in cur:
        neighbor_names.add(row["target_name"])
        edges_out.append({"kind": "HAS_MEMBER", "source": name, "target": row["target_name"]})

    # IS_ENTRY_POINT
    cur = conn.execute(
        """SELECT target_name, properties FROM edges WHERE project = ? AND kind = 'IS_ENTRY_POINT' AND source_name = ? LIMIT ?""",
        (project, name, limit),
    )
    for row in cur:
        props = json.loads(row["properties"]) if row["properties"] else {}
        neighbor_names.add(row["target_name"])
        edges_out.append({
            "kind": "IS_ENTRY_POINT", "source": name,
            "target": row["target_name"], "stage": props.get("stage"),
        })

    # 查邻居节点详情
    nodes = [node]
    for nname in neighbor_names:
        if nname and nname != name:
            cur = conn.execute(
                """SELECT id, kind, name, file_path, line, conditional_signature
                   FROM nodes WHERE project = ? AND name = ? LIMIT 1""",
                (project, nname),
            )
            row = cur.fetchone()
            if row:
                nodes.append({
                    "id": row["id"], "kind": row["kind"], "name": row["name"],
                    "file_path": row["file_path"], "line": row["line"],
                    "conditional_signature": row["conditional_signature"],
                })

    return {"nodes": nodes, "edges": edges_out + edges_in}


def get_subgraph(
    conn: sqlite3.Connection, project: str,
    function_name: str, depth: int = 3, limit: int = 100,
    macros: Optional[dict] = None,
) -> dict:
    """BFS 沿 CALLS 边遍历调用链子图。

    macros 非 None 时给边标 active 字段。
    """
    visited = set()
    nodes = []
    edges = []
    queue = [(function_name, 0)]

    while queue and len(nodes) < limit:
        cur_name, cur_depth = queue.pop(0)
        if cur_name in visited or cur_depth > depth:
            continue
        visited.add(cur_name)

        # 查节点
        cur = conn.execute(
            """SELECT id, kind, name, file_path, line, conditional_signature
               FROM nodes WHERE project = ? AND name = ? AND kind = 'Function' LIMIT 1""",
            (project, cur_name),
        )
        row = cur.fetchone()
        if row:
            nodes.append({
                "id": row["id"], "kind": row["kind"], "name": row["name"],
                "file_path": row["file_path"], "line": row["line"],
                "conditional_signature": row["conditional_signature"],
            })

        if cur_depth >= depth:
            continue

        # outbound CALLS
        cur = conn.execute(
            """SELECT target_name, source_line, source_file, conditional_signature
               FROM edges WHERE project = ? AND kind = 'CALLS' AND source_name = ?
               LIMIT 20""",
            (project, cur_name),
        )
        for r in cur:
            edges.append({
                "kind": "CALLS", "source": cur_name, "target": r["target_name"],
                "line": r["source_line"],
                "file_path": r["source_file"],
                "conditional_signature": r["conditional_signature"],
            })
            if r["target_name"] not in visited:
                queue.append((r["target_name"], cur_depth + 1))

        # inbound CALLS
        cur = conn.execute(
            """SELECT source_name, source_line, source_file, conditional_signature
               FROM edges WHERE project = ? AND kind = 'CALLS' AND target_name = ?
               LIMIT 20""",
            (project, cur_name),
        )
        for r in cur:
            edges.append({
                "kind": "CALLS", "source": r["source_name"], "target": cur_name,
                "line": r["source_line"],
                "file_path": r["source_file"],
                "conditional_signature": r["conditional_signature"],
            })
            if r["source_name"] not in visited:
                queue.append((r["source_name"], cur_depth + 1))

    if macros is not None:
        annotate_edges_with_active(edges, macros)
    return {"nodes": nodes, "edges": edges, "truncated": len(nodes) >= limit}


def get_source(conn: sqlite3.Connection, node_id: int, context_lines: int = 0) -> dict:
    """读节点对应的源码片段。"""
    node = get_node(conn, node_id)
    if not node:
        return {"error": "node not found"}
    file_path = node["file_path"]
    start_line = node["line"]
    end_line = node["end_line"] or start_line
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        ctx_start = max(0, start_line - 1 - context_lines)
        ctx_end = min(len(lines), end_line + context_lines)
        source = "".join(lines[ctx_start:ctx_end])
        return {
            "file_path": file_path,
            "start_line": ctx_start + 1,
            "end_line": ctx_end,
            "source": source,
            "node_start": start_line,
            "node_end": end_line,
        }
    except Exception as e:
        return {"error": str(e), "file_path": file_path}


__all__ = [
    "get_overview", "search_nodes", "get_node",
    "get_connections", "get_neighbors", "get_subgraph", "get_source",
]
