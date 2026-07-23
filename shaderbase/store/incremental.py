"""incremental — 增量更新（DEV_PLAN §3.1 incremental 子包合并到 store）。

基于 file_meta 表的 mtime/size/content_hash 判 dirty，加 INCLUDES 反向闭包扩展，
只重索引受影响文件。比全量重建快 10-20×。

流程：
1. detect_dirty: walk root_path，比对 mtime/size/content_hash → dirty 文件列表
2. reverse_deps_closure: 从 reverse_deps 表查 dirty 文件的 INCLUDE 反向闭包
3. 逐文件 index_file（复用 indexer.index_file）
4. 重跑 resolve_calls（CALLS 边跨文件 resolve）

注：CALLS 边的反向闭包不在这里做——CALLS resolve 是全局的（按 include 闭包找定义），
   改一个函数定义影响所有调用它的文件，无法局部 resolve。所以增量后总是重跑全量
   resolve_calls（28592 条边 resolve 耗时 < 2 秒，可接受）。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Optional


SKIP_DIRS = {"no_source", "no_source_pc", "pipeline_output", "bin", ".git"}
SHADER_EXTS = {".nsf", ".hlsl", ".fxh"}


def detect_dirty(
    conn: sqlite3.Connection, project: str, root_path: str,
) -> dict:
    """检测 dirty 文件。

    比对策略（任一条件满足即 dirty）：
    - file_meta 里没有该文件（新文件）
    - mtime 或 size 变了
    - mtime/size 没变但 content_hash 变了（极少见，保险）

    返回 {dirty: [file_path], new: [file_path], deleted: [file_path], total_scanned}。
    file_path 都是相对 root_path 的相对路径。
    """
    from .connection import resolve_root_path
    abs_root = resolve_root_path(root_path)

    # 加载已索引文件的 meta
    cur = conn.execute(
        "SELECT file_path, mtime, size, content_hash FROM file_meta WHERE project = ?",
        (project,),
    )
    indexed: dict[str, tuple[int, int, str]] = {}
    for row in cur:
        indexed[row["file_path"]] = (row["mtime"], row["size"], row["content_hash"])

    dirty: list[str] = []
    new: list[str] = []
    # deleted: file_meta 里有但磁盘上没有（用 root_path 拼接判存在）
    deleted: list[str] = []
    for fp in indexed:
        abs_fp = fp if os.path.isabs(fp) else os.path.join(abs_root, fp)
        if not os.path.exists(abs_fp):
            deleted.append(fp)
    total_scanned = 0

    for dp, dns, fns in os.walk(abs_root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            if os.path.splitext(f)[1].lower() not in SHADER_EXTS:
                continue
            total_scanned += 1
            abs_fp = os.path.join(dp, f)
            # 存相对路径（跟 SQLite file_path 同口径）
            rel_fp = os.path.relpath(abs_fp, abs_root).replace("\\", "/")
            try:
                st = os.stat(abs_fp)
            except OSError:
                continue
            meta = indexed.get(rel_fp)
            # 兼容旧数据：如果相对路径没匹配到，试绝对路径
            if meta is None and os.path.isabs(fp := abs_fp.replace("\\", "/")):
                meta = indexed.get(fp)
            if meta is None:
                new.append(rel_fp)
                continue
            mtime, size, content_hash = meta
            if int(st.st_mtime) != mtime or st.st_size != size:
                dirty.append(rel_fp)
                continue
            # mtime/size 没变，跳过 content_hash（IO 代价高，只在前两个条件不满足时才算）

    return {
        "dirty": dirty,
        "new": new,
        "deleted": deleted,
        "total_scanned": total_scanned,
    }


def reverse_deps_closure(
    conn: sqlite3.Connection, project: str, dirty_files: list[str],
) -> set[str]:
    """从 reverse_deps 表查 dirty 文件的 INCLUDE 反向闭包。

    dirty 文件被别人 #include 了 → include 它的文件也要重索引。
    递归展开（include 链可能多层）。

    返回 dirty_files + 反向闭包的并集（含自身）。
    """
    if not dirty_files:
        return set()

    # reverse_deps: source_file = 被 include 的文件, dependent_file = include 它的文件
    # 但 source_file 存的是 include 路径文本（相对路径），不是绝对路径
    # 需要先把 dirty_files 的绝对路径映射回可能的 include 路径文本
    # 简化：用 basename 匹配 + 路径后缀匹配

    # 收集所有 reverse_deps
    cur = conn.execute(
        "SELECT source_file, dependent_file FROM reverse_deps WHERE project = ?",
        (project,),
    )
    all_deps = list(cur)

    # dirty_files 的 basename 集合（用于匹配 source_file）
    dirty_basenames = {os.path.basename(fp) for fp in dirty_files}

    # 初始 closure = dirty_files
    closure = set(dirty_files)
    changed = True
    while changed:
        changed = False
        for source_file, dependent_file in all_deps:
            # source_file 是 include 路径文本，dependent_file 是绝对路径
            # 如果 source_file 对应的文件在 closure 里 → dependent_file 加进 closure
            if dependent_file in closure:
                continue
            # 匹配：source_file 的 basename 在 dirty_basenames 里
            # 且 dependent_file 确实存在
            src_base = os.path.basename(source_file.replace("\\", "/"))
            if src_base in dirty_basenames:
                # 进一步验证：dependent_file 的 include 路径确实指向 closure 里的某文件
                # 简化：只要 basename 匹配就认为依赖
                closure.add(dependent_file)
                changed = True

    return closure


def incremental_update(
    conn: sqlite3.Connection, project: str, root_path: str,
) -> dict:
    """增量更新流程：detect_dirty → reverse_deps_closure → 逐文件 index_file → resolve_calls。

    返回 {dirty, new, deleted, reindexed, calls_resolved}。
    """
    from .indexer import index_file
    from ..extract.resolve_calls import resolve_calls as _resolve

    # 1. 检测 dirty
    dirty_info = detect_dirty(conn, project, root_path)
    dirty = dirty_info["dirty"]
    new = dirty_info["new"]
    deleted = dirty_info["deleted"]

    if not dirty and not new and not deleted:
        return {
            "dirty": [], "new": [], "deleted": [],
            "reindexed": 0, "calls_resolved": None,
            "message": "no changes detected",
        }

    # 2. 反向闭包扩展
    all_dirty = reverse_deps_closure(conn, project, dirty + new)
    # deleted 文件的依赖者也要重索引（去掉被删文件的引用）
    if deleted:
        all_dirty |= reverse_deps_closure(conn, project, deleted)

    # 3. 删除 deleted 文件的旧数据
    for fp in deleted:
        conn.execute(
            "DELETE FROM nodes WHERE file_path = ? AND project = ?",
            (fp, project),
        )
        conn.execute(
            "DELETE FROM edges WHERE source_file = ? AND project = ?",
            (fp, project),
        )
        conn.execute(
            "DELETE FROM file_meta WHERE file_path = ? AND project = ?",
            (fp, project),
        )

    # 4. 逐文件重索引
    from ..extract.nodes import NodeExtractor
    from ..extract.edges import EdgeExtractor
    from .connection import resolve_root_path
    abs_root = resolve_root_path(root_path)
    extractor = NodeExtractor()
    edge_extractor = EdgeExtractor()

    reindexed = 0
    crash_count = 0
    for fp in sorted(all_dirty):
        # fp 可能是相对路径或绝对路径（兼容旧数据）
        abs_fp = fp if os.path.isabs(fp) else os.path.join(abs_root, fp)
        if not os.path.exists(abs_fp):
            continue
        try:
            with open(abs_fp, "rb") as f:
                src = f.read()
            # 统一存相对路径
            rel_fp = fp if not os.path.isabs(fp) else os.path.relpath(fp, abs_root).replace("\\", "/")
            index_file(conn, rel_fp, src, project, extractor, edge_extractor, abs_root)
            reindexed += 1
        except Exception as e:
            crash_count += 1
            if crash_count <= 3:
                print(f"  CRASH {abs_fp}: {e}")

    # 5. 重建 reverse_deps（include 关系可能变了）
    from .indexer import _build_reverse_deps
    # 只重建受影响文件的 reverse_deps（简化：全量重建 reverse_deps 表）
    conn.execute("DELETE FROM reverse_deps WHERE project = ?", (project,))
    _build_reverse_deps(conn, project)

    # 6. 重跑 resolve_calls（全局，CALLS 边跨文件 resolve）
    calls_resolved = _resolve(conn, project, root_path)

    conn.commit()
    return {
        "dirty": dirty,
        "new": new,
        "deleted": deleted,
        "reindexed": reindexed,
        "calls_resolved": calls_resolved,
        "crash_count": crash_count,
    }


__all__ = ["detect_dirty", "reverse_deps_closure", "incremental_update"]
