"""tools — MCP 工具实现（14 个查询工具）。

每个工具返回结构化 JSON，Agent 直接消费。
复用 shaderbase.store 的 SQLite 查询 + shaderbase.web.queries 的查询函数。

DEV_PLAN §2 双视图：5 个查询工具支持 macros 参数，传了就按 macros 算 active，
给每条边/引用标 active=true/false。不传保持现状（返回全部 + conditional_signature）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from ..web.queries import (
    get_overview,
    search_nodes,
    get_node,
    get_connections,
    get_source,
    get_subgraph,
)
from ..web.layout import compute_layout, get_repo_info
from ..preprocessor.query_helper import annotate_edges_with_active


# 支持 macros 参数的工具集合（给 execute_tool 统一解析用）
_MACROS_AWARE_TOOLS = {
    "trace_calls", "get_references", "find_uniform_usage",
    "find_entry_points", "trace_stage_flow",
}


def _parse_macros(args: dict) -> Optional[dict]:
    """从工具参数里取 macros。

    args["macros"] 可以是 dict（直接用）或字符串 "KEY:1,KEY:0"（解析）。
    返回 None = 不算 active（向后兼容）；{} = 空 macros 算 active。
    """
    if "macros" not in args:
        return None
    m = args["macros"]
    if m is None:
        return None
    if isinstance(m, dict):
        return {k: int(v) if isinstance(v, (int, float)) else 1 for k, v in m.items()}
    if isinstance(m, str):
        out: dict[str, int] = {}
        for part in m.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, v = part.split(":", 1)
                try:
                    out[k.strip()] = int(v.strip())
                except ValueError:
                    out[k.strip()] = 1
            else:
                out[part] = 1
        return out
    return None


# ════════════════════════════════════════════════════════
# 工具定义
# ════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    {
        "name": "search_shader",
        "description": "搜索 shader 知识库节点。按名字/类型/文件路径过滤。返回匹配的节点列表（id/kind/name/file_path/line）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "节点名模糊匹配（如 'CalcWorldNormal'）"},
                "kind": {"type": "string", "description": "节点类型：Function/Struct/Uniform/Texture/SamplerState/Technique/CBuffer"},
                "file_pattern": {"type": "string", "description": "文件路径模糊匹配（如 'pbr' 或 'surface_functions'）"},
                "limit": {"type": "integer", "description": "返回上限，默认50", "default": 50},
            },
        },
    },
    {
        "name": "trace_calls",
        "description": "沿 CALLS 边 BFS 遍历调用链。支持指定方向（inbound=谁调用了我/outbound=我调用了谁/both）和深度。返回调用链子图。传 macros 参数时每条边带 active 字段（按 macros 算条件编译是否生效）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string", "description": "起始函数名"},
                "direction": {"type": "string", "description": "inbound/outbound/both", "default": "both"},
                "depth": {"type": "integer", "description": "BFS 深度（1-5）", "default": 3},
                "limit": {"type": "integer", "description": "返回节点上限", "default": 100},
                "macros": {"type": "object", "description": "条件编译宏配置（如 {\"USE_SEASON_ID\": 1, \"QUALITY_HIGH\": 0}），传了就给边标 active"},
            },
            "required": ["function_name"],
        },
    },
    {
        "name": "get_code_snippet",
        "description": "读取函数/结构体的源码。返回源码文本 + 行号范围。支持 context_lines 上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "integer", "description": "节点 ID（从 search_shader 拿到）"},
                "function_name": {"type": "string", "description": "函数名（如果不知道 node_id，按名字找第一个匹配的 Function 节点）"},
                "context_lines": {"type": "integer", "description": "上下文行数", "default": 0},
            },
        },
    },
    {
        "name": "get_definition",
        "description": "找符号的定义位置。返回 file_path + line + kind。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "符号名"},
                "kind": {"type": "string", "description": "限定类型（可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_references",
        "description": "找符号被引用的所有位置（CALLS 边的两端）。返回引用列表。传 macros 参数时每个引用带 active 字段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "符号名"},
                "limit": {"type": "integer", "description": "返回上限", "default": 50},
                "macros": {"type": "object", "description": "条件编译宏配置，传了就给引用标 active"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "find_entry_points",
        "description": "找所有 shader 入口函数（vs_main/ps_main/cs_main）+ 所属 technique。传 macros 参数时每个入口带 active 字段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "technique": {"type": "string", "description": "限定 technique 名（可选）"},
                "macros": {"type": "object", "description": "条件编译宏配置，传了就给入口标 active"},
            },
        },
    },
    {
        "name": "find_uniform_usage",
        "description": "找 uniform 被哪些函数使用（查 USES_UNIFORM 边）。返回使用位置列表。传 macros 参数时每个使用带 active 字段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uniform_name": {"type": "string", "description": "uniform 名"},
                "macros": {"type": "object", "description": "条件编译宏配置，传了就给使用标 active"},
            },
            "required": ["uniform_name"],
        },
    },
    {
        "name": "trace_stage_flow",
        "description": "找 VS 输出 semantic → PS 输入 semantic 的数据流（查 FLOWS_TO 边）。传 macros 参数时每个匹配带 active 字段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "semantic": {"type": "string", "description": "语义名（如 TEXCOORD2）"},
                "macros": {"type": "object", "description": "条件编译宏配置，传了就给匹配标 active"},
            },
            "required": ["semantic"],
        },
    },
    {
        "name": "get_material_files",
        "description": "找材质三件套文件（.nsf + _nodes.hlsl + _parameters.hlsl）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "material_name": {"type": "string", "description": "材质名（如 pbr_rock）"},
            },
            "required": ["material_name"],
        },
    },
    {
        "name": "get_architecture",
        "description": "仓库架构总览。按目录/kind 聚合统计。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "detect_changes",
        "description": "git diff → 影响范围。分析改了哪些文件/符号 + 爆炸半径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "git ref（如 HEAD~3）", "default": "HEAD~3"},
                "depth": {"type": "integer", "description": "影响传播深度", "default": 2},
            },
        },
    },
    {
        "name": "find_dead_code",
        "description": "找没人调用的函数（可选排除 entry point）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "exclude_entry_points": {"type": "boolean", "description": "排除入口函数", "default": True},
            },
        },
    },
    {
        "name": "index_status",
        "description": "查询索引状态、覆盖率、错误列表。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "incremental_update",
        "description": "触发增量更新（dirty 检测 + 局部重建）。shader 代码更新后调这个刷新图谱。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ════════════════════════════════════════════════════════
# 工具实现
# ════════════════════════════════════════════════════════

def execute_tool(conn: sqlite3.Connection, project: str, root_path: str,
                 name: str, arguments: dict) -> Any:
    """执行一个工具，返回结果 dict。"""

    if name == "search_shader":
        return _search_shader(conn, project, arguments)

    if name == "trace_calls":
        return _trace_calls(conn, project, arguments)

    if name == "get_code_snippet":
        return _get_code_snippet(conn, project, arguments)

    if name == "get_definition":
        return _get_definition(conn, project, arguments)

    if name == "get_references":
        return _get_references(conn, project, arguments)

    if name == "find_entry_points":
        return _find_entry_points(conn, project, arguments)

    if name == "find_uniform_usage":
        return _find_uniform_usage(conn, project, arguments)

    if name == "trace_stage_flow":
        return _trace_stage_flow(conn, project, arguments)

    if name == "get_material_files":
        return _get_material_files(conn, project, arguments)

    if name == "get_architecture":
        return _get_architecture(conn, project)

    if name == "detect_changes":
        return _detect_changes(conn, project, root_path, arguments)

    if name == "find_dead_code":
        return _find_dead_code(conn, project, arguments)

    if name == "index_status":
        return _index_status(conn, project)

    if name == "incremental_update":
        return _incremental_update(conn, project, root_path)

    return {"error": f"unknown tool: {name}"}


def _search_shader(conn, project, args):
    return search_nodes(
        conn, project,
        name_pattern=args.get("name"),
        kind=args.get("kind"),
        file_pattern=args.get("file_pattern"),
        limit=args.get("limit", 50),
    )


def _trace_calls(conn, project, args):
    func = args["function_name"]
    direction = args.get("direction", "both")
    depth = args.get("depth", 3)
    limit = args.get("limit", 100)
    macros = _parse_macros(args)
    result = get_subgraph(conn, project, func, depth, limit)

    # 按 direction 过滤
    if direction == "inbound":
        result["edges"] = [e for e in result["edges"] if e["target"] == func]
    elif direction == "outbound":
        result["edges"] = [e for e in result["edges"] if e["source"] == func]

    # 传了 macros 就给边标 active
    if macros is not None:
        # get_subgraph 的边存 source_file 在 "file" 字段（看 queries.py）
        # 需要 file_path + line 给 annotate_edges_with_active
        for e in result["edges"]:
            if "file_path" not in e:
                e["file_path"] = e.get("file") or ""
            if "line" not in e:
                e["line"] = e.get("line")
        annotate_edges_with_active(result["edges"], macros)
    return result


def _get_code_snippet(conn, project, args):
    node_id = args.get("node_id")
    func_name = args.get("function_name")
    context = args.get("context_lines", 0)

    if not node_id and func_name:
        # 按名字找第一个 Function 节点
        res = search_nodes(conn, project, name_pattern=func_name, kind="Function", limit=1)
        if res["nodes"]:
            node_id = res["nodes"][0]["id"]
        else:
            return {"error": f"function not found: {func_name}"}

    if not node_id:
        return {"error": "node_id or function_name required"}

    return get_source(conn, node_id, context)


def _get_definition(conn, project, args):
    symbol = args["symbol"]
    kind = args.get("kind")
    res = search_nodes(conn, project, name_pattern=symbol, kind=kind, limit=10)
    defs = []
    for n in res["nodes"]:
        defs.append({
            "name": n["name"],
            "kind": n["kind"],
            "file_path": n["file_path"],
            "line": n["line"],
            "node_id": n["id"],
        })
    return {"definitions": defs, "count": len(defs)}


def _get_references(conn, project, args):
    symbol = args["symbol"]
    limit = args.get("limit", 50)
    macros = _parse_macros(args)

    # CALLS 边的两端
    refs = []
    # 作为 caller
    cur = conn.execute(
        """SELECT target_name, source_file, source_line, conditional_signature
           FROM edges WHERE project = ? AND kind = 'CALLS' AND source_name = ?
           LIMIT ?""",
        (project, symbol, limit),
    )
    for row in cur:
        refs.append({
            "direction": "outbound",
            "target": row["target_name"],
            "file_path": row["source_file"],
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
        })
    # 作为 callee
    cur = conn.execute(
        """SELECT source_name, source_file, source_line, conditional_signature
           FROM edges WHERE project = ? AND kind = 'CALLS' AND target_name = ?
           LIMIT ?""",
        (project, symbol, limit),
    )
    for row in cur:
        refs.append({
            "direction": "inbound",
            "source": row["source_name"],
            "file_path": row["source_file"],
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
        })

    # 传了 macros 就标 active
    if macros is not None:
        annotate_edges_with_active(refs, macros)
    return {"references": refs, "count": len(refs)}


def _find_entry_points(conn, project, args):
    technique = args.get("technique")
    macros = _parse_macros(args)
    sql = """SELECT e.source_name, e.target_name, e.properties, e.source_file, e.source_line
             FROM edges e WHERE e.project = ? AND e.kind = 'IS_ENTRY_POINT'"""
    params = [project]
    if technique:
        sql += " AND e.source_name = ?"
        params.append(technique)
    cur = conn.execute(sql, params)
    entries = []
    for row in cur:
        props = json.loads(row["properties"]) if row["properties"] else {}
        entries.append({
            "technique": row["source_name"],
            "function": row["target_name"],
            "stage": props.get("stage"),
            "file_path": row["source_file"],
            "line": row["source_line"],
        })
    if macros is not None:
        annotate_edges_with_active(entries, macros)
    return {"entry_points": entries, "count": len(entries)}


def _find_uniform_usage(conn, project, args):
    uniform_name = args["uniform_name"]
    macros = _parse_macros(args)
    # 查 Uniform 节点定义
    cur = conn.execute(
        """SELECT file_path, line FROM nodes
           WHERE project = ? AND kind = 'Uniform' AND name = ?""",
        (project, uniform_name),
    )
    uniform_defs = [dict(r) for r in cur]
    if not uniform_defs:
        return {"error": f"uniform not found: {uniform_name}"}

    # 查 USES_UNIFORM 边：function → uniform（精确，不再用同文件兜底）
    cur = conn.execute(
        """SELECT source_name, source_file, source_line, conditional_signature
           FROM edges WHERE project = ? AND kind = 'USES_UNIFORM'
           AND target_name = ?""",
        (project, uniform_name),
    )
    usages = []
    for row in cur:
        usages.append({
            "function": row["source_name"],
            "file_path": row["source_file"],
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
        })

    if macros is not None:
        annotate_edges_with_active(usages, macros)
    return {
        "uniform": uniform_name,
        "definitions": uniform_defs,
        "usages": usages,
        "count": len(usages),
    }


def _trace_stage_flow(conn, project, args):
    semantic = args["semantic"].upper()
    macros = _parse_macros(args)
    # 查 FLOWS_TO 边：struct → semantic
    cur = conn.execute(
        """SELECT source_name, target_name, source_file, source_line,
                  conditional_signature, properties
           FROM edges WHERE project = ? AND kind = 'FLOWS_TO'""",
        (project,),
    )
    results = []
    for row in cur:
        sem = (row["target_name"] or "").upper()
        if semantic not in sem:
            continue
        props = json.loads(row["properties"]) if row["properties"] else {}
        results.append({
            "struct": row["source_name"],
            "field": props.get("field"),
            "field_type": props.get("field_type"),
            "semantic": row["target_name"],
            "file_path": row["source_file"],
            "line": row["source_line"],
            "conditional_signature": row["conditional_signature"],
        })
    if macros is not None:
        annotate_edges_with_active(results, macros)
    return {"semantic": args["semantic"], "matches": results, "count": len(results)}


def _get_material_files(conn, project, args):
    material = args["material_name"]
    # 搜 .nsf + _nodes.hlsl + _parameters.hlsl
    patterns = [material, f"{material}_nodes", f"{material}_parameters"]
    results = []
    for pat in patterns:
        res = search_nodes(conn, project, file_pattern=pat, limit=5)
        files = set()
        for n in res["nodes"]:
            files.add(n["file_path"])
        for f in files:
            results.append({"pattern": pat, "file_path": f})
    return {"material": material, "files": results}


def _get_architecture(conn, project):
    return get_overview(conn, project)


def _detect_changes(conn, project, root_path, args):
    since = args.get("since", "HEAD~3")
    depth = args.get("depth", 2)

    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since],
            capture_output=True, text=True, cwd=root_path, timeout=10,
        )
        changed_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except Exception as e:
        return {"error": f"git diff failed: {e}"}

    if not changed_files:
        return {"changed_files": [], "affected_nodes": [], "message": "no changes"}

    # 找受影响的节点
    affected = []
    for cf in changed_files:
        full_path = root_path + "/" + cf
        cur = conn.execute(
            "SELECT id, kind, name, line FROM nodes WHERE project = ? AND file_path LIKE ?",
            (project, f"%{cf}%"),
        )
        for row in cur:
            affected.append(dict(row))

    # 沿 CALLS 边传播（简化 1 层）
    impacted = list(affected)
    for node in affected:
        if node["name"]:
            cur = conn.execute(
                """SELECT DISTINCT source_name, source_file FROM edges
                   WHERE project = ? AND kind = 'CALLS' AND target_name = ?""",
                (project, node["name"]),
            )
            for row in cur:
                impacted.append({"kind": "Function", "name": row["source_name"],
                                 "file_path": row["source_file"], "via": "CALLS"})

    return {
        "changed_files": changed_files,
        "directly_affected": affected,
        "impacted_count": len(impacted),
        "impacted_sample": impacted[:20],
    }


def _find_dead_code(conn, project, args):
    exclude_entry = args.get("exclude_entry_points", True)

    # 找所有 Function 节点
    cur = conn.execute(
        "SELECT id, name, file_path, line FROM nodes WHERE project = ? AND kind = 'Function'",
        (project,),
    )
    all_funcs = [dict(r) for r in cur]

    # 找有入边的函数（被调用的）
    cur = conn.execute(
        """SELECT DISTINCT target_name FROM edges
           WHERE project = ? AND kind = 'CALLS'""",
        (project,),
    )
    called = {r["target_name"] for r in cur}

    # entry points
    entry_points = set()
    if exclude_entry:
        cur = conn.execute(
            """SELECT DISTINCT target_name FROM edges
               WHERE project = ? AND kind = 'IS_ENTRY_POINT'""",
            (project,),
        )
        entry_points = {r["target_name"] for r in cur}

    dead = []
    for f in all_funcs:
        if f["name"] not in called and f["name"] not in entry_points:
            dead.append(f)

    return {"dead_functions": dead[:50], "total_dead": len(dead)}


def _index_status(conn, project):
    ov = get_overview(conn, project)
    cur = conn.execute(
        "SELECT COUNT(*) FROM file_meta WHERE project = ? AND parsed_ok = 0",
        (project,),
    )
    error_files = cur.fetchone()[0]
    cur = conn.execute(
        """SELECT file_path, error_count FROM file_meta
           WHERE project = ? AND error_count > 0 ORDER BY error_count DESC LIMIT 10""",
        (project,),
    )
    errors = [dict(r) for r in cur]
    return {
        **ov,
        "error_files": error_files,
        "error_details": errors,
    }


def _incremental_update(conn, project, root_path):
    from ..store.incremental import incremental_update
    result = incremental_update(conn, project, root_path)
    return result


__all__ = ["TOOL_DEFINITIONS", "execute_tool"]
