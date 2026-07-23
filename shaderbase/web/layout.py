"""layout — 服务端图布局（2D 力导向，对齐 konwleage map/template.html 风格）。

放弃 3D 星系 + bloom 的酷炫效果，改用简洁的 2D 力向图：
- 节点用按 kind 上色的小圆点（type-dot 风格）
- 边用细线
- 服务端只算 degree/size/color/status，位置交给前端 Canvas 实时力导向算
  （前端力导向比 Python 服务端算快得多，且可交互拖拽）

输出契约（与 template.html 的 GRAPH_DATA 兼容）：
- nodes: [{id, label, name, file_path, qualified_name, start_line, end_line,
           size, color, in_calls, status}]
- edges: [{source, target, type}]   （source/target 是 node id）
- total_nodes
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict, deque
from typing import Optional


# ════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════

DEFAULT_MAX_NODES = 5000
HARD_MAX_NODES = 10_000_000


# ════════════════════════════════════════════════════════
# kind → 颜色（对齐 template.html typeColor + colors.ts LABEL_COLORS）
# 节点颜色按"语义类型"分，不按 degree 分（避免糊成一片白光）
# ════════════════════════════════════════════════════════

KIND_COLORS = {
    "Function":     "#58a6ff",   # 蓝色（主语义：函数）
    "Struct":       "#3fb950",   # 绿色（数据结构）
    "Texture":      "#d29922",   # 黄色（资源）
    "SamplerState": "#f85149",   # 红色（状态）
    "Uniform":      "#bc8cff",   # 紫色（参数）
    "Technique":    "#f97583",   # 粉红（technique/entry）
    "CBuffer":      "#c9d1d9",   # 灰白（容器）
}
DEFAULT_KIND_COLOR = "#8b949e"


def color_for_kind(kind: str) -> str:
    return KIND_COLORS.get(kind, DEFAULT_KIND_COLOR)


# 节点半径：按 in_calls 映射，但范围受限（5–16，对齐 template.html getR）
def node_radius(in_calls: int) -> float:
    return max(5, min(16, 3 + in_calls * 1.5))


# ════════════════════════════════════════════════════════
# dead-code 状态分类（简化版，对齐 layout3d.c）
# ════════════════════════════════════════════════════════

def classify_status(label: str, in_calls: int, properties: dict) -> str:
    is_fn = label in ("Function", "Method")
    if not is_fn:
        return "structural"
    if label == "Technique" or properties.get("is_entry") or properties.get("is_route"):
        return "entry"
    if properties.get("is_test"):
        return "test"
    if properties.get("is_exported"):
        return "exported"
    if in_calls == 0:
        return "dead"
    if in_calls == 1:
        return "single"
    return "normal"


# ════════════════════════════════════════════════════════
# 主布局入口
# ════════════════════════════════════════════════════════

def compute_layout(
    conn: sqlite3.Connection,
    project: str,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """返回 GraphData JSON（前端 Canvas 力向图渲染）。

    服务端不再算 x/y/z —— 位置交给前端实时力导向算
    （前端 JS 比 Python 快 100×，且可拖拽交互）。
    这里只算 degree/in_calls/status/color/radius。
    """
    if max_nodes <= 0:
        max_nodes = DEFAULT_MAX_NODES
    if max_nodes > HARD_MAX_NODES:
        max_nodes = HARD_MAX_NODES

    total_nodes = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE project = ?", (project,)
    ).fetchone()[0]
    if total_nodes == 0:
        return {"nodes": [], "edges": [], "total_nodes": 0}

    # 取节点
    cur = conn.execute(
        """SELECT id, kind, name, qualified_name, file_path,
                  line AS start_line, end_line, properties
           FROM nodes WHERE project = ?""",
        (project,),
    )
    all_db_nodes = [dict(row) for row in cur]

    # 全图入度：COUNT CALLS edges where target_name = node.name
    cur = conn.execute(
        """SELECT target_name, COUNT(*) AS c FROM edges
           WHERE project = ? AND kind = 'CALLS' AND target_name IS NOT NULL
           GROUP BY target_name""",
        (project,),
    )
    in_calls_by_name: dict[str, int] = {row["target_name"]: row["c"] for row in cur}

    # 按 in_calls 降序取 top-N（让 hub 优先入图）
    all_db_nodes.sort(key=lambda n: in_calls_by_name.get(n["name"] or "", 0), reverse=True)
    all_nodes = all_db_nodes[:max_nodes]
    if not all_nodes:
        return {"nodes": [], "edges": [], "total_nodes": total_nodes}

    node_id_set = {n["id"] for n in all_nodes}

    # 全图边（一次查完）
    cur = conn.execute(
        """SELECT source_name, target_name, source_file, kind
           FROM edges WHERE project = ?""",
        (project,),
    )
    all_edges_raw = list(cur)

    # name → node-ids（仅在 loaded 集合内，同名多节点）
    name_to_nodes: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for n in all_nodes:
        if n["name"]:
            name_to_nodes[n["name"]].append((n["id"], n["file_path"]))

    # 边过滤：两端都必须在 loaded 集合内
    edges_out: list[dict] = []
    degree: dict[int, int] = defaultdict(int)

    for row in all_edges_raw:
        src_name = row["source_name"]
        tgt_name = row["target_name"]
        if not src_name or not tgt_name:
            continue

        src_cands = name_to_nodes.get(src_name)
        if not src_cands:
            continue
        src_file = row["source_file"]
        src_id = None
        for nid, nfp in src_cands:
            if nfp == src_file:
                src_id = nid
                break
        if src_id is None:
            src_id = src_cands[0][0]
        if src_id not in node_id_set:
            continue

        tgt_cands = name_to_nodes.get(tgt_name)
        if not tgt_cands:
            continue
        tgt_id = tgt_cands[0][0]
        if tgt_id not in node_id_set:
            continue

        edges_out.append({
            "source": src_id,
            "target": tgt_id,
            "type": row["kind"],
        })
        degree[src_id] += 1
        degree[tgt_id] += 1

    # 组装输出（前端会自己算 x/y）
    out_nodes: list[dict] = []
    for n in all_nodes:
        full_in = in_calls_by_name.get(n["name"] or "", 0)
        props = json.loads(n["properties"]) if n["properties"] else {}
        status = classify_status(n["kind"], full_in, props)
        out_nodes.append({
            "id": n["id"],
            "label": n["kind"],
            "name": n["name"] or "<anonymous>",
            "file_path": n["file_path"],
            "qualified_name": n["qualified_name"],
            "start_line": n["start_line"],
            "end_line": n["end_line"],
            "size": node_radius(full_in),
            "color": color_for_kind(n["kind"]),
            "in_calls": full_in,
            "edge_count": degree.get(n["id"], 0),
            "status": status,
        })

    return {
        "nodes": out_nodes,
        "edges": edges_out,
        "total_nodes": total_nodes,
    }


# ════════════════════════════════════════════════════════
# git remote 元数据（GitHub deep-link 用）
# ════════════════════════════════════════════════════════

def get_repo_info(conn: sqlite3.Connection, project: str) -> dict:
    """从 projects 表查 root_path，读 .git/config 算 repo info。"""
    cur = conn.execute("SELECT root_path FROM projects WHERE name = ?", (project,))
    row = cur.fetchone()
    if not row or not row["root_path"]:
        return {"error": "project not found"}

    root_path = row["root_path"]
    git_config = root_path + "/.git/config"

    remote_url = ""
    branch = "main"
    try:
        with open(git_config, "r", encoding="utf-8", errors="replace") as f:
            in_origin = False
            in_branch = False
            for line in f:
                line = line.strip()
                if line == '[remote "origin"]':
                    in_origin = True
                    continue
                if line.startswith("[branch"):
                    in_branch = True
                    continue
                if line.startswith("["):
                    in_origin = False
                    in_branch = False
                    continue
                if in_origin and line.startswith("url ="):
                    remote_url = line.split("=", 1)[1].strip()
                if in_branch and line.startswith("merge ="):
                    parts = line.split("refs/heads/")
                    if len(parts) > 1:
                        branch = parts[1].strip()
    except Exception:
        pass

    web_base = ""
    blob_base = ""
    if remote_url:
        url = remote_url.replace("ssh://", "").replace("git@", "")
        url = url.replace(".git", "")
        if ":" in url.split("/")[0]:
            parts = url.split(":")
            url = parts[0] + "/" + "/".join(parts[1].split("/")[1:]) if len(parts) > 1 else url
        if url.startswith("https://"):
            web_base = url
            blob_base = url + "/blob/" + branch
        else:
            web_base = "https://" + url if not url.startswith("http") else url
            blob_base = web_base + "/blob/" + branch

    return {
        "root_path": root_path,
        "branch": branch,
        "remote_url": remote_url,
        "web_base": web_base,
        "blob_base": blob_base,
    }


__all__ = [
    "compute_layout", "get_repo_info",
    "color_for_kind", "node_radius", "classify_status",
    "DEFAULT_MAX_NODES", "HARD_MAX_NODES",
]
