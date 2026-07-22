"""resolve_calls — CALLS 边跨文件 resolve。

CALLS 边抽取时只知道 callee 名字（如 "CalcWorldNormal"），
不知道定义在哪个文件。本模块用 include 闭包 resolve：

1. 收集所有 Function 节点 → {name: [(file, line)]} 定义索引
2. 对每条 CALLS 边，从 caller 所在文件出发，沿 include 闭包找同名函数定义
3. 找到 → 填 target_file + target_id
   找不到 → 保留 target_name，标 unresolved（可能是 intrinsic 或外部）

接受 70-80% 召回率（DEV_PLAN §3.5）。
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections import defaultdict
from typing import Optional


# HLSL intrinsic 列表（不全，但覆盖常见）——这些不 resolve
HLSL_INTRINSICS = {
    "abs", "acos", "all", "any", "asin", "atan", "atan2", "ceil", "clamp",
    "clip", "cos", "cosh", "cross", "degrees", "determinant", "distance",
    "dot", "dst", "exp", "exp2", "faceforward", "floor", "fmod", "frac",
    "frexp", "fwidth", "isfinite", "isinf", "isnan", "ldexp", "length",
    "lerp", "lit", "log", "log10", "log2", "mad", "max", "min", "modf",
    "mul", "normalize", "pow", "radians", "reflect", "refract", "round",
    "rsqrt", "saturate", "sign", "sin", "sincos", "sinh", "smoothstep",
    "sqrt", "step", "tan", "tanh", "tex1D", "tex2D", "tex3D", "texCUBE",
    "transpose", "trunc",
    "ddx", "ddy", "ddx_coarse", "ddy_coarse", "ddx_fine", "ddy_fine",
    "asfloat", "asint", "asuint", "asdouble",
    "firstbithigh", "firstbitlow", "countbits", "reversebits",
    "f16tof32", "f32tof16", "uaddbcarry", "usubborrow", "umulExtended", "imulExtended",
    "msad4", "msad4", "r128", "wfmad",
    "D3DCOLORtoUBYTE4", "D3DCOLORtoUBYTEn",
    "printf", "errorf",
    "tex2Dlod", "tex2Dproj", "tex2Dbias",
    "texCUBEproj", "tex3Dproj",
    "Gather", "GatherRed", "GatherGreen", "GatherBlue", "GatherAlpha",
    "Sample", "SampleBias", "SampleCmp", "SampleCmpLevelZero", "SampleGrad",
    "SampleLevel", "Load", "Load2", "Load3", "Load4",
    "GetDimensions", "CalculateLevelOfDetail", "CalculateLevelOfDetailUnclamped",
    "GetSamplePosition", "gather", "gatherRed",
    "InterlockedAdd", "InterlockedAnd", "InterlockedCompareExchange",
    "InterlockedCompareStore", "InterlockedExchange", "InterlockedMax",
    "InterlockedMin", "InterlockedOr", "InterlockedXor",
    "allmemorybarrier", "devicememorybarrier", "groupmemorybarrier",
    "DeviceMemoryBarrier", "GroupMemoryBarrier", "GroupMemoryBarrierWithGroupSync",
    "AllMemoryBarrier", "AllMemoryBarrierWithGroupSync",
}


def build_function_index(conn: sqlite3.Connection, project: str) -> dict[str, list[tuple]]:
    """收集所有 Function 节点 → {name: [(node_id, file_path, line)]}。

    同名函数（重载）会有多个条目。
    """
    out: dict[str, list[tuple]] = defaultdict(list)
    cur = conn.execute(
        "SELECT id, name, file_path, line FROM nodes WHERE project = ? AND kind = 'Function'",
        (project,),
    )
    for row in cur:
        if row["name"]:
            out[row["name"]].append((row["id"], row["file_path"], row["line"]))
    return dict(out)


def build_include_closure(
    conn: sqlite3.Connection, project: str, root_path: str,
) -> dict[str, set[str]]:
    """构建 include 闭包：file → 它能 include 到的所有文件集合（含自身）。

    从 INCLUDES 边重建，解析 include 路径。
    """
    # 收集所有 INCLUDES 边
    includes: dict[str, list[str]] = defaultdict(list)
    cur = conn.execute(
        """SELECT DISTINCT source_file, target_name FROM edges
           WHERE project = ? AND kind = 'INCLUDES'""",
        (project,),
    )
    for row in cur:
        includes[row["source_file"]].append(row["target_name"])

    # 解析 include 路径 → 绝对路径
    resolved: dict[str, set[str]] = {}
    for src_file, inc_list in includes.items():
        closure = set()
        closure.add(src_file)
        for inc in inc_list:
            resolved_inc = _resolve_include_path(src_file, inc, root_path)
            if resolved_inc:
                closure.add(resolved_inc)
        # 递归展开（include 的 include）
        changed = True
        while changed:
            changed = False
            for f in list(closure):
                for inc in includes.get(f, []):
                    r = _resolve_include_path(f, inc, root_path)
                    if r and r not in closure:
                        closure.add(r)
                        changed = True
        resolved[src_file] = closure
    return resolved


def _resolve_include_path(
    source_file: str, include_path: str, root_path: str,
) -> Optional[str]:
    """解析 #include "xxx" 的相对路径 → 绝对路径。

    G66 的 include 路径是相对 source_file 所在目录的。
    也尝试相对 root_path。
    """
    inc = include_path.replace("\\", "/")
    # 1. 相对 source_file 目录
    src_dir = os.path.dirname(source_file)
    cand = os.path.normpath(os.path.join(src_dir, inc))
    if os.path.exists(cand):
        return cand.replace("\\", "/")
    # 2. 相对 root_path
    cand2 = os.path.normpath(os.path.join(root_path, inc))
    if os.path.exists(cand2):
        return cand2.replace("\\", "/")
    # 3. 在 root_path 下递归找 basename
    base = os.path.basename(inc)
    for dp, _, fns in os.walk(root_path):
        if base in fns:
            cand3 = os.path.join(dp, base)
            # 优先匹配路径后缀跟 include 一致的
            if cand3.replace("\\", "/").endswith(inc):
                return cand3.replace("\\", "/")
    # 4. 任意匹配 basename
    for dp, _, fns in os.walk(root_path):
        if base in fns:
            return os.path.join(dp, base).replace("\\", "/")
    return None


def resolve_calls(
    conn: sqlite3.Connection, project: str, root_path: str,
    func_index: Optional[dict] = None,
    include_closure: Optional[dict] = None,
) -> dict:
    """resolve 所有 CALLS 边的 target_file + target_id。

    返回 {total, resolved, unresolved, intrinsic}。
    """
    func_index = func_index or build_function_index(conn, project)
    include_closure = include_closure or build_include_closure(conn, project, root_path)

    # 收集所有 CALLS 边
    cur = conn.execute(
        """SELECT id, source_file, source_line, target_name,
                  conditional_signature, properties
           FROM edges WHERE project = ? AND kind = 'CALLS'""",
        (project,),
    )
    calls = list(cur)

    resolved = 0
    unresolved = 0
    intrinsic = 0

    for row in calls:
        callee = row["target_name"]
        if not callee:
            unresolved += 1
            continue
        if callee in HLSL_INTRINSICS:
            intrinsic += 1
            conn.execute(
                "UPDATE edges SET properties = ? WHERE id = ?",
                (json.dumps({"resolved": "intrinsic"}, ensure_ascii=False), row["id"]),
            )
            continue
        # 找定义
        candidates = func_index.get(callee, [])
        if not candidates:
            unresolved += 1
            continue
        # 在 include 闭包里找
        caller_file = row["source_file"]
        closure = include_closure.get(caller_file, {caller_file})
        best = None
        for fid, fpath, fline in candidates:
            if fpath in closure:
                best = (fid, fpath, fline)
                break
        if not best:
            # 闭包外，选第一个（退化策略）
            best = candidates[0]
        conn.execute(
            "UPDATE edges SET target_name = ?, properties = ? WHERE id = ?",
            (callee, json.dumps({
                "resolved_to_file": best[1],
                "resolved_to_line": best[2],
                "resolved_to_id": best[0],
            }, ensure_ascii=False), row["id"]),
        )
        resolved += 1

    conn.commit()
    return {
        "total": len(calls),
        "resolved": resolved,
        "unresolved": unresolved,
        "intrinsic": intrinsic,
    }


import json  # noqa: E402

__all__ = [
    "build_function_index", "build_include_closure",
    "resolve_calls", "HLSL_INTRINSICS",
]
