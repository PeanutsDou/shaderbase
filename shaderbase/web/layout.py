"""layout — 服务端图布局算法 + 星等色 + size 计算。

对齐 codebase-memory layout3d.c 的输出契约：
- 节点带 x/y/z 坐标（服务端算，前端只渲染）
- 节点带 color（按 degree 映射 7 档星等色）
- 节点带 size（按 degree 映射）
- 边带 source/target/type

布局策略（简化版，不用 Barnes-Hut octree）：
1. 按顶层目录 cluster 放到环上
2. 按 kind 分 z 层（Technique 顶 → Function 中 → Uniform/Texture 底）
3. 力导向松弛（弹簧 + 斥力，固定迭代次数）
"""
from __future__ import annotations

import math
import json
import sqlite3
from collections import defaultdict
from typing import Optional


# ---- 星等色（照搬 codebase-memory STELLAR_LEGEND）----
# 按 degree（连接数）分 7 档：O 蓝巨 → M 红矮
STELLAR_COLORS = [
    (50, "#80a0ff"),   # O (Blue Giant)
    (26, "#c0d0ff"),   # B (Blue-White)
    (13, "#e8e8ff"),   # A (White)
    (7,  "#fff0c0"),   # F (Yellow-White)
    (4,  "#ffe080"),   # G (Yellow/Sun)
    (2,  "#ffa060"),   # K (Orange)
    (0,  "#ff6050"),   # M (Red Dwarf)
]


def stellar_color(degree: int) -> str:
    """按连接数映射星等色。"""
    for threshold, color in STELLAR_COLORS:
        if degree >= threshold:
            return color
    return STELLAR_COLORS[-1][1]


def node_size(degree: int) -> float:
    """按连接数映射节点大小。"""
    if degree >= 50:
        return 8.0
    if degree >= 26:
        return 6.0
    if degree >= 13:
        return 5.0
    if degree >= 7:
        return 4.0
    if degree >= 4:
        return 3.5
    if degree >= 2:
        return 3.0
    return 2.5


# ---- kind → z 层 ----
KIND_Z = {
    "Technique": 200,
    "CBuffer": 150,
    "Struct": 100,
    "Function": 0,
    "Texture": -100,
    "SamplerState": -150,
    "Uniform": -200,
}


def compute_layout(
    conn: sqlite3.Connection,
    project: str,
    max_nodes: int = 5000,
) -> dict:
    """计算图布局，返回 GraphData JSON。

    返回格式对齐 codebase-memory GraphData:
    {nodes: [{id,x,y,z,label,name,file_path,...,size,color}], edges: [{source,target,type}], total_nodes}
    """
    # 1. 查节点总数
    cur = conn.execute("SELECT COUNT(*) FROM nodes WHERE project = ?", (project,))
    total_nodes = cur.fetchone()[0]

    if total_nodes == 0:
        return {"nodes": [], "edges": [], "total_nodes": 0}

    # 2. 查所有节点（不带子查询，快）
    cur = conn.execute(
        """SELECT id, kind, name, file_path, line, end_line, qualified_name
           FROM nodes WHERE project = ? ORDER BY kind, name""",
        (project,),
    )
    all_db_nodes = [dict(row) for row in cur]

    # 3. 查所有边（一次查完）
    cur = conn.execute(
        """SELECT source_name, target_name, kind FROM edges
           WHERE project = ? AND kind IN ('CALLS','INCLUDES','HAS_MEMBER','IS_ENTRY_POINT')""",
        (project,),
    )
    all_edges_raw = list(cur)

    # 4. 用全量边算 degree（不用子查询）
    degree = defaultdict(int)
    name_set = {n["name"] for n in all_db_nodes if n["name"]}
    for row in all_edges_raw:
        if row["source_name"] in name_set:
            degree[row["source_name"]] += 1
        if row["target_name"] in name_set:
            degree[row["target_name"]] += 1

    # 5. 按 degree 降序取 top-N
    all_db_nodes.sort(key=lambda n: degree.get(n["name"], 0), reverse=True)
    all_nodes = all_db_nodes[:max_nodes]

    if not all_nodes:
        return {"nodes": [], "edges": [], "total_nodes": total_nodes}

    node_ids = {n["id"] for n in all_nodes}

    # 6. 构建 top-N 的边
    name_to_ids = defaultdict(list)
    for n in all_nodes:
        if n["name"]:
            name_to_ids[n["name"]].append(n["id"])

    edges = []
    for row in all_edges_raw:
        src_names = name_to_ids.get(row["source_name"], [])
        tgt_names = name_to_ids.get(row["target_name"], [])
        if not src_names or not tgt_names:
            continue
        src_id = src_names[0]
        tgt_id = tgt_names[0]
        if src_id in node_ids and tgt_id in node_ids:
            edges.append({"source": src_id, "target": tgt_id, "type": row["kind"]})

    # 4. 按顶层目录 cluster
    dir_clusters = defaultdict(list)
    for n in all_nodes:
        parts = n["file_path"].replace("\\", "/").split("/")
        # 取倒数第二段作为 cluster key（shader-source/xxx/yyy.hlsl → xxx）
        cluster = parts[-2] if len(parts) >= 2 else "root"
        dir_clusters[cluster].append(n)

    num_clusters = len(dir_clusters)
    cluster_angle = {}
    for i, cluster in enumerate(sorted(dir_clusters.keys())):
        cluster_angle[cluster] = (i / max(num_clusters, 1)) * 2 * math.pi

    # 5. 初始位置：环上按 cluster + z 按 kind
    positions = {}  # id → (x, y, z)
    for cluster, nodes_in_cluster in dir_clusters.items():
        angle = cluster_angle[cluster]
        cx = math.cos(angle) * 300
        cy = math.sin(angle) * 300
        for i, n in enumerate(nodes_in_cluster):
            # 在 cluster 内散开
            local_angle = (i / max(len(nodes_in_cluster), 1)) * 2 * math.pi
            radius = 30 + (i % 5) * 15
            x = cx + math.cos(local_angle) * radius
            y = cy + math.sin(local_angle) * radius
            z = float(KIND_Z.get(n["kind"], 0))
            positions[n["id"]] = [x, y, z]

    # 6. 力导向松弛（简化：弹簧 + 斥力，固定迭代）
    # 大图时跳过力导向（太慢），用初始环布局即可
    if len(all_nodes) <= 1000:
        _force_relax(positions, edges, id_to_idx, iterations=10)

    # 7. 组装输出
    out_nodes = []
    for n in all_nodes:
        nid = n["id"]
        pos = positions.get(nid, [0, 0, 0])
        deg = degree.get(nid, 0)
        out_nodes.append({
            "id": nid,
            "x": pos[0],
            "y": pos[1],
            "z": pos[2],
            "label": n["kind"],
            "name": n["name"] or "<anonymous>",
            "file_path": n["file_path"],
            "qualified_name": n["qualified_name"],
            "start_line": n["line"],
            "end_line": n["end_line"],
            "size": node_size(deg),
            "color": stellar_color(deg),
            "in_calls": deg,
        })

    return {
        "nodes": out_nodes,
        "edges": edges,
        "total_nodes": total_nodes,
    }


def _force_relax(
    positions: dict,
    edges: list,
    id_to_idx: dict,
    iterations: int = 30,
) -> None:
    """简化力导向：弹簧吸引 + 斥力排斥。"""
    if not positions or not edges:
        return

    ids = list(positions.keys())
    n = len(ids)
    if n > 5000:
        iterations = 10  # 大图减少迭代
    if n > 10000:
        iterations = 5

    repulsion = 500.0
    spring_length = 80.0
    spring_strength = 0.05
    damping = 0.8

    for _ in range(iterations):
        forces = {nid: [0.0, 0.0, 0.0] for nid in ids}

        # 斥力（O(n²) — 大图采样）
        step = max(1, n // 500)
        sampled = list(range(0, n, step))
        for idx_i, i in enumerate(sampled):
            nid_i = ids[i]
            pi = positions[nid_i]
            for j in sampled[idx_i + 1:]:
                nid_j = ids[j]
                pj = positions[nid_j]
                dx = pi[0] - pj[0]
                dy = pi[1] - pj[1]
                dz = pi[2] - pj[2]
                dist_sq = dx * dx + dy * dy + dz * dz + 0.01
                dist = math.sqrt(dist_sq)
                force = repulsion / dist_sq
                fx = force * dx / dist
                fy = force * dy / dist
                fz = force * dz / dist
                forces[nid_i][0] += fx
                forces[nid_i][1] += fy
                forces[nid_i][2] += fz
                forces[nid_j][0] -= fx
                forces[nid_j][1] -= fy
                forces[nid_j][2] -= fz

        # 弹簧（边）
        for e in edges:
            src = e["source"]
            tgt = e["target"]
            if src not in positions or tgt not in positions:
                continue
            ps = positions[src]
            pt = positions[tgt]
            dx = pt[0] - ps[0]
            dy = pt[1] - ps[1]
            dz = pt[2] - ps[2]
            dist = math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01
            displacement = dist - spring_length
            fx = spring_strength * displacement * dx / dist
            fy = spring_strength * displacement * dy / dist
            fz = spring_strength * displacement * dz / dist
            forces[src][0] += fx
            forces[src][1] += fy
            forces[src][2] += fz
            forces[tgt][0] -= fx
            forces[tgt][1] -= fy
            forces[tgt][2] -= fz

        # 应用力
        for nid in ids:
            f = forces[nid]
            positions[nid][0] += f[0] * damping
            positions[nid][1] += f[1] * damping
            positions[nid][2] += f[2] * damping


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

    # 算 web_base / blob_base
    web_base = ""
    blob_base = ""
    if remote_url:
        # ssh://git@host:port/org/repo.git → host/org/repo
        # https://github.com/org/repo.git → github.com/org/repo
        url = remote_url.replace("ssh://", "").replace("git@", "")
        url = url.replace(".git", "")
        if ":" in url.split("/")[0]:
            # host:port → host
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


__all__ = ["compute_layout", "get_repo_info", "stellar_color", "node_size"]
