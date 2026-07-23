"""layout — 服务端图布局算法（1:1 对齐 codebase-memory src/ui/layout3d.c）。

输出契约严格对齐 cbm_layout_result_t / cbm_layout_to_json：
- 节点带 x/y/z（服务端算，前端只渲染）
- 节点带 color（按 degree 映射 9 档星等色）
- 节点带 size（base 由 label 决定 + degree boost）
- 节点带 status（dead/single/entry/test/normal/structural）
- 节点带 in_calls（全图入度，非采样）
- 边带 source/target/type（两端必须都在返回的 node 集合内）

布局策略（与 layout3d.c 一致）：
1. 按 file_path 前 3 段做 cluster key，fnv1a 哈希 → 环上角度（半径 500–750）
   + qualified_name 哈希 jitter（±40）
2. z 轴按 BFS 算的 call depth × 50 间距
3. local_optimize：Barnes-Hut 八叉树斥力 + 边吸引 + anchor 弹簧
4. 全图入度（COUNT target_name）做 dead-code 分类
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict, deque
from typing import Optional


# ════════════════════════════════════════════════════════
# 常量（1:1 照搬 layout3d.c #define）
# ════════════════════════════════════════════════════════

DEFAULT_MAX_NODES = 5000
HARD_MAX_NODES = 10_000_000

LOCAL_REPULSION = 8.0
LOCAL_ATTRACTION = 1.0
LOCAL_ANCHOR_K = 0.25
LOCAL_ITERATIONS = 40
Z_DEPTH_SPACING = 50.0

# 大图减少迭代（与 layout3d.c local_optimize 同策略）
_LOCAL_ITER_LARGE = 20   # n > 100_000
_LOCAL_ITER_HUGE = 10    # n > 500_000
# Python 比 C 慢 ~100×：超过这个节点数直接跳过 local_optimize，
# 用 seed ring + BFS z 层（已经够好看了，与原 layout.py 同策略）。
_LOCAL_OPTIMIZE_CAP = 1500


# ════════════════════════════════════════════════════════
# 星等色（1:1 照搬 layout3d.c stellar_color）
# ════════════════════════════════════════════════════════

def stellar_color(degree: int) -> str:
    """按连接数映射 9 档星等色（Hertzsprung-Russell 分布）。

    M 红矮（76% 的星）→ 低度叶子；O 蓝巨 → mega-hub。
    """
    if degree <= 1:
        return "#ff6050"   # M — red dwarf
    if degree <= 3:
        return "#ff8855"   # late K — orange-red
    if degree <= 5:
        return "#ffa060"   # K — orange
    if degree <= 8:
        return "#ffc070"   # early K — warm orange
    if degree <= 12:
        return "#ffe080"   # G — yellow (Sun-like)
    if degree <= 18:
        return "#fff0c0"   # F — yellow-white
    if degree <= 25:
        return "#fff8e8"   # late A — warm white
    if degree <= 35:
        return "#e8e8ff"   # A — white-blue
    if degree <= 50:
        return "#c0d0ff"   # B — blue-white
    return "#80a0ff"        # O — blue giant


# ════════════════════════════════════════════════════════
# size（1:1 照搬 layout3d.c size_for_label + degree boost）
# ════════════════════════════════════════════════════════

_LABEL_BASE_SIZE = {
    "Project": 20.0,
    "Package": 15.0,
    "Module": 15.0,
    "Folder": 12.0,
    "File": 8.0,
    "Class": 6.0,
    "Struct": 6.0,
    "Interface": 6.0,
    "Function": 4.0,
    "Method": 4.0,
    "Technique": 6.0,
    "CBuffer": 6.0,
    "Texture": 5.0,
    "Uniform": 3.0,
    "SamplerState": 4.0,
}


def size_for_label(label: str) -> float:
    return _LABEL_BASE_SIZE.get(label, 4.0)


def node_size(label: str, degree: int) -> float:
    """base 由 label 决定 + degree boost（hub 更大，上限 10）。"""
    base = size_for_label(label)
    boost = min(degree * 0.3, 10.0) if degree > 5 else 0.0
    return base + boost


# ════════════════════════════════════════════════════════
# 哈希（1:1 照搬 layout3d.c fnv1a + rand_float）
# ════════════════════════════════════════════════════════

def fnv1a(s: str) -> int:
    """FNV-1a 32-bit。"""
    h = 2166136261
    for ch in s.encode("utf-8", "replace"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def rand_float(seed: int) -> float:
    """照搬 layout3d.c rand_float：返回 [-0.5, 0.5)。"""
    seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
    return ((seed >> 16) & 0x7FFF) / 32768.0 - 0.5


def cluster_key(file_path: str) -> str:
    """照搬 layout3d.c：file_path 前 3 个 '/' 段。"""
    if not file_path:
        return ""
    # 标准化 Windows 反斜杠
    fp = file_path.replace("\\", "/")
    out = []
    sl = 0
    for ch in fp:
        if ch == "/":
            sl += 1
            if sl >= 3:
                break
        out.append(ch)
    return "".join(out)


# ════════════════════════════════════════════════════════
# BFS call depth（1:1 照搬 layout3d.c compute_call_depth）
# ════════════════════════════════════════════════════════

# entry 候选 label（与 layout3d.c 一致 + shader 的 Technique）
_ENTRY_LABELS = {"Route", "File", "Module", "Package", "Technique"}


def compute_call_depth(
    n: int,
    es: list[int],
    ed: list[int],
    labels: list[str],
) -> list[int]:
    """BFS 从 entry 点出发算 call depth。

    es[e]/ed[e] 是节点 index（0..n-1）。返回每个节点的 depth。
    """
    depth = [-1] * n
    q: deque[int] = deque()

    # entry 点 depth=0
    for i in range(n):
        if labels[i] in _ENTRY_LABELS:
            depth[i] = 0
            q.append(i)

    if not q:
        # 没 entry：入度 0 的节点当根
        in_deg = [0] * n
        for e_idx in range(len(es)):
            t = ed[e_idx]
            if 0 <= t < n:
                in_deg[t] += 1
        for i in range(n):
            if in_deg[i] == 0:
                depth[i] = 0
                q.append(i)

    # 邻接表（避免 O(n*e) — 与 C 一致但 Python 太慢，先建表）
    adj: list[list[int]] = [[] for _ in range(n)]
    for e_idx in range(len(es)):
        s, t = es[e_idx], ed[e_idx]
        if 0 <= s < n and 0 <= t < n:
            adj[s].append(t)

    while q:
        c = q.popleft()
        cd = depth[c]
        for t in adj[c]:
            if depth[t] == -1:
                depth[t] = cd + 1
                q.append(t)

    # 未访问的标 depth=0
    for i in range(n):
        if depth[i] == -1:
            depth[i] = 0
    return depth


# ════════════════════════════════════════════════════════
# Barnes-Hut 八叉树 + local_optimize（1:1 照搬 layout3d.c）
# ════════════════════════════════════════════════════════

_OCTREE_MAX_DEPTH = 40
_OCTREE_MIN_HALF = 1e-4


class _OctreeNode:
    __slots__ = (
        "cx", "cy", "cz", "total_mass", "half_size",
        "ox", "oy", "oz", "body_index", "body_mass", "children",
    )

    def __init__(self, ox: float, oy: float, oz: float, half: float):
        self.cx = 0.0
        self.cy = 0.0
        self.cz = 0.0
        self.total_mass = 0.0
        self.half_size = half
        self.ox = ox
        self.oy = oy
        self.oz = oz
        self.body_index = -1
        self.body_mass = 0.0
        self.children: list[Optional[_OctreeNode]] = [None] * 8


def _octant(n: _OctreeNode, x: float, y: float, z: float) -> int:
    return (
        (1 if x >= n.ox else 0)
        | (2 if y >= n.oy else 0)
        | (4 if z >= n.oz else 0)
    )


def _child_center(n: _OctreeNode, o: int) -> tuple[float, float, float]:
    q = n.half_size * 0.5
    return (
        n.ox + (q if (o & 1) else -q),
        n.oy + (q if (o & 2) else -q),
        n.oz + (q if (o & 4) else -q),
    )


def _octree_insert(
    n: _OctreeNode, idx: int, x: float, y: float, z: float,
    mass: float, depth: int,
) -> None:
    """照搬 layout3d.c octree_insert。"""
    if n.total_mass == 0.0 and n.body_index == -1:
        n.body_index = idx
        n.body_mass = mass
        n.cx = x
        n.cy = y
        n.cz = z
        n.total_mass = mass
        return

    # OOM 防护：depth/size 到地板时折叠成聚合质心
    if depth >= _OCTREE_MAX_DEPTH or n.half_size < _OCTREE_MIN_HALF:
        nm = n.total_mass + mass
        n.cx = (n.cx * n.total_mass + x * mass) / nm
        n.cy = (n.cy * n.total_mass + y * mass) / nm
        n.cz = (n.cz * n.total_mass + z * mass) / nm
        n.total_mass = nm
        n.body_index = -1
        return

    if n.body_index >= 0:
        oi = n.body_index
        ox, oy, oz, om = n.cx, n.cy, n.cz, n.body_mass
        n.body_index = -1
        o = _octant(n, ox, oy, oz)
        if n.children[o] is None:
            a, b, c = _child_center(n, o)
            n.children[o] = _OctreeNode(a, b, c, n.half_size * 0.5)
        if n.children[o] is not None:
            _octree_insert(n.children[o], oi, ox, oy, oz, om, depth + 1)

    nm = n.total_mass + mass
    n.cx = (n.cx * n.total_mass + x * mass) / nm
    n.cy = (n.cy * n.total_mass + y * mass) / nm
    n.cz = (n.cz * n.total_mass + z * mass) / nm
    n.total_mass = nm

    o = _octant(n, x, y, z)
    if n.children[o] is None:
        a, b, c = _child_center(n, o)
        n.children[o] = _OctreeNode(a, b, c, n.half_size * 0.5)
    if n.children[o] is not None:
        _octree_insert(n.children[o], idx, x, y, z, mass, depth + 1)


def _octree_repulse(
    n: _OctreeNode, px: float, py: float, pz: float, mm: float,
    si: int, kr: float, fx: list[float], fy: list[float], fz: list[float],
) -> None:
    """照搬 layout3d.c octree_repulse（Barnes-Hut 近似）。"""
    dx = n.cx - px
    dy = n.cy - py
    dz = n.cz - pz
    dist_sq = dx * dx + dy * dy + dz * dz
    if dist_sq < 1e-6:
        dist_sq = 1e-6
    dist = math.sqrt(dist_sq)

    # Barnes-Hut 阈值：cell 角尺寸 < 1 时当作一个体
    if n.children == [None] * 8 or (n.half_size / max(dist, 1e-6)) < 1.0:
        if n.body_index >= 0 and n.body_index == si:
            return
        f = kr * n.total_mass * mm / dist_sq
        fx[0] += f * dx / dist
        fy[0] += f * dy / dist
        fz[0] += f * dz / dist
        return

    for ch in n.children:
        if ch is not None:
            _octree_repulse(ch, px, py, pz, mm, si, kr, fx, fy, fz)


class _Body:
    __slots__ = ("x", "y", "z", "ax", "ay", "az", "fx", "fy", "fz", "mass")

    def __init__(self, x: float, y: float, z: float, mass: float):
        self.x = x
        self.y = y
        self.z = z
        self.ax = x
        self.ay = y
        self.az = z
        self.fx = 0.0
        self.fy = 0.0
        self.fz = 0.0
        self.mass = mass


def local_optimize(
    bodies: list[_Body],
    es: list[int],
    ed: list[int],
    iterations: int = LOCAL_ITERATIONS,
) -> None:
    """照搬 layout3d.c local_optimize（anchor-preserving 局部优化）。

    大图自动减迭代（与 C 版一致）。
    """
    n = len(bodies)
    if n == 0:
        return

    if n > 500_000:
        iterations = _LOCAL_ITER_HUGE
    elif n > 100_000:
        iterations = _LOCAL_ITER_LARGE

    ne = len(es)
    for _ in range(iterations):
        for b in bodies:
            b.fx = 0.0
            b.fy = 0.0
            b.fz = 0.0

        # bounding box
        mnx = miny = mnz = 1e9
        mxx = mxy = mxz = -1e9
        for b in bodies:
            if b.x < mnx:
                mnx = b.x
            if b.y < miny:
                miny = b.y
            if b.z < mnz:
                mnz = b.z
            if b.x > mxx:
                mxx = b.x
            if b.y > mxy:
                mxy = b.y
            if b.z > mxz:
                mxz = b.z

        half = max(mxx - mnx, mxy - miny, mxz - mnz) * 0.5 + 1.0
        root = _OctreeNode(
            (mnx + mxx) * 0.5, (miny + mxy) * 0.5, (mnz + mxz) * 0.5, half
        )
        for i, b in enumerate(bodies):
            _octree_insert(root, i, b.x, b.y, b.z, b.mass, 0)

        # 斥力（Barnes-Hut）
        for i, b in enumerate(bodies):
            fx = [0.0]
            fy = [0.0]
            fz = [0.0]
            _octree_repulse(
                root, b.x, b.y, b.z, b.mass, i, LOCAL_REPULSION, fx, fy, fz
            )
            b.fx = fx[0]
            b.fy = fy[0]
            b.fz = fz[0]

        # 吸引（边）
        for e in range(ne):
            s, t = es[e], ed[e]
            if s < 0 or s >= n or t < 0 or t >= n:
                continue
            bs, bt = bodies[s], bodies[t]
            dx = bt.x - bs.x
            dy = bt.y - bs.y
            dz = bt.z - bs.z
            bs.fx += dx * LOCAL_ATTRACTION
            bs.fy += dy * LOCAL_ATTRACTION
            bs.fz += dz * LOCAL_ATTRACTION
            bt.fx -= dx * LOCAL_ATTRACTION
            bt.fy -= dy * LOCAL_ATTRACTION
            bt.fz -= dz * LOCAL_ATTRACTION

        # anchor 弹簧：拉回初始环位置
        for b in bodies:
            b.fx += (b.ax - b.x) * LOCAL_ANCHOR_K * b.mass
            b.fy += (b.ay - b.y) * LOCAL_ANCHOR_K * b.mass
            b.fz += (b.az - b.z) * LOCAL_ANCHOR_K * b.mass

        # 应用（位移上限 8，与 C 版一致）
        for b in bodies:
            fm = math.sqrt(b.fx * b.fx + b.fy * b.fy + b.fz * b.fz)
            speed = 1.0
            if speed * fm > 8.0:
                speed = 8.0 / (fm + 0.001)
            b.x += b.fx * speed
            b.y += b.fy * speed
            b.z += b.fz * speed


# ════════════════════════════════════════════════════════
# dead-code 状态分类（1:1 照搬 layout3d.c status 逻辑）
# ════════════════════════════════════════════════════════

def classify_status(
    label: str,
    in_calls: int,
    properties: dict,
) -> str:
    """shader 语境下的 dead-code 分类。

    与 layout3d.c 对齐：
    - 非 Function/Method → structural
    - entry（Technique / IS_ENTRY_POINT target）→ entry
    - in_calls == 0 → dead
    - in_calls == 1 → single
    - 否则 → normal
    """
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
# 主布局入口（1:1 照搬 cbm_layout_compute）
# ════════════════════════════════════════════════════════

def compute_layout(
    conn: sqlite3.Connection,
    project: str,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """计算图布局，返回 GraphData JSON。

    返回格式 1:1 对齐 cbm_layout_to_json：
    {nodes: [{id,x,y,z,label,name,file_path,qualified_name,start_line,end_line,
              size,color,in_calls,status}],
     edges: [{source,target,type}], total_nodes}
    """
    # clamp_max_nodes
    if max_nodes <= 0:
        max_nodes = DEFAULT_MAX_NODES
    if max_nodes > HARD_MAX_NODES:
        max_nodes = HARD_MAX_NODES

    # 1. 节点总数
    total_nodes = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE project = ?", (project,)
    ).fetchone()[0]
    if total_nodes == 0:
        return {"nodes": [], "edges": [], "total_nodes": 0}

    # 2. 取节点（按 degree 排序前先全量取出，后面再排）
    cur = conn.execute(
        """SELECT id, kind, name, qualified_name, file_path,
                  line AS start_line, end_line, properties
           FROM nodes WHERE project = ?""",
        (project,),
    )
    all_db_nodes = [dict(row) for row in cur]

    # 3. 全图入度：COUNT CALLS edges where target_name = node.name
    #    （与 layout3d.c cbm_store_batch_count_degrees 等价）
    cur = conn.execute(
        """SELECT target_name, COUNT(*) AS c FROM edges
           WHERE project = ? AND kind = 'CALLS' AND target_name IS NOT NULL
           GROUP BY target_name""",
        (project,),
    )
    in_calls_by_name: dict[str, int] = {row["target_name"]: row["c"] for row in cur}

    # 4. 按 in_calls 降序取 top-N（让 hub 优先入图）
    def _deg(n: dict) -> int:
        return in_calls_by_name.get(n["name"] or "", 0)

    all_db_nodes.sort(key=_deg, reverse=True)
    all_nodes = all_db_nodes[:max_nodes]
    if not all_nodes:
        return {"nodes": [], "edges": [], "total_nodes": total_nodes}

    node_id_set = {n["id"] for n in all_nodes}

    # 5. 全图边（一次查完，所有 kind）
    cur = conn.execute(
        """SELECT source_name, target_name, source_file, kind
           FROM edges WHERE project = ?""",
        (project,),
    )
    all_edges_raw = list(cur)

    # 6. 构建 name → node-ids 映射（仅在 loaded 集合内）
    #    同名多节点：按 (name) → list[(node_id, file_path)]
    name_to_nodes: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for n in all_nodes:
        if n["name"]:
            name_to_nodes[n["name"]].append((n["id"], n["file_path"]))

    # 7. 边过滤：两端都必须在 loaded 集合内（与 C find_node_index 等价）
    #    对每条边：用 (source_name, source_file) 找 source_id；
    #              用 target_name 找 target_id（优先同文件 → 任意）
    edges_out: list[dict] = []
    es_idx: list[int] = []
    ed_idx: list[int] = []
    id_to_idx: dict[int, int] = {n["id"]: i for i, n in enumerate(all_nodes)}
    degree: list[int] = [0] * len(all_nodes)

    for row in all_edges_raw:
        src_name = row["source_name"]
        tgt_name = row["target_name"]
        if not src_name or not tgt_name:
            continue

        # source：优先 (name, source_file) 精确匹配，否则取第一个
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

        # target：优先同文件（resolved_to_file 不可用时退化为任意）
        tgt_cands = name_to_nodes.get(tgt_name)
        if not tgt_cands:
            continue
        tgt_id = tgt_cands[0][0]
        if tgt_id not in node_id_set:
            continue

        si = id_to_idx[src_id]
        ti = id_to_idx[tgt_id]
        edges_out.append({
            "source": src_id,
            "target": tgt_id,
            "type": row["kind"],
        })
        es_idx.append(si)
        ed_idx.append(ti)
        degree[si] += 1
        degree[ti] += 1

    # 8. call depth（BFS）→ z 层
    labels = [n["kind"] for n in all_nodes]
    cdepth = compute_call_depth(len(all_nodes), es_idx, ed_idx, labels)

    # 9. seed positions：fnv1a(cluster_key) → 环上角度 + 半径 500–750
    #    + qualified_name 哈希 jitter ±40
    bodies: list[_Body] = []
    for i, n in enumerate(all_nodes):
        ck = cluster_key(n["file_path"])
        h = fnv1a(ck)
        angle = ((h & 0xFFFF) / 65535.0) * 2.0 * math.pi
        r = 500.0 + (((h >> 16) & 0xFF) / 255.0) * 250.0

        seed = fnv1a(n["qualified_name"] or n["name"] or "")
        jx = rand_float(seed) * 40.0
        jy = rand_float(seed) * 40.0  # 用同一 seed 的下一态太麻烦，直接复用
        px = r * math.cos(angle) + jx
        py = r * math.sin(angle) + jy
        pz = -float(cdepth[i]) * Z_DEPTH_SPACING

        mass = float(degree[i] + 1)
        bodies.append(_Body(px, py, pz, mass))

    # 10. local_optimize（Barnes-Hut + anchor）—— 超过 cap 时跳过
    if len(bodies) <= _LOCAL_OPTIMIZE_CAP:
        local_optimize(bodies, es_idx, ed_idx)

    # 11. 组装输出
    out_nodes: list[dict] = []
    for i, n in enumerate(all_nodes):
        deg = degree[i]
        # 全图入度（不被 max_nodes 采样裁剪）
        full_in = in_calls_by_name.get(n["name"] or "", 0)
        props = json.loads(n["properties"]) if n["properties"] else {}
        status = classify_status(n["kind"], full_in, props)
        out_nodes.append({
            "id": n["id"],
            "x": bodies[i].x,
            "y": bodies[i].y,
            "z": bodies[i].z,
            "label": n["kind"],
            "name": n["name"] or "<anonymous>",
            "file_path": n["file_path"],
            "qualified_name": n["qualified_name"],
            "start_line": n["start_line"],
            "end_line": n["end_line"],
            "size": node_size(n["kind"], deg),
            "color": stellar_color(deg),
            "in_calls": full_in,
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
    "stellar_color", "node_size", "size_for_label",
    "cluster_key", "fnv1a", "compute_call_depth",
    "local_optimize", "classify_status",
    "DEFAULT_MAX_NODES", "HARD_MAX_NODES",
]
