#!/usr/bin/env python3
# coding: utf-8
"""定时 sync 脚本，给 cron/task scheduler 调用。

git pull shader-source + 增量更新图谱 + 打时间戳日志。

用法：
  py -3 scripts/cron_sync.py --project g66
  py -3 scripts/cron_sync.py --project g66 --log data/sync.log

Linux cron 配置（每小时跑一次）：
  0 * * * * cd /path/to/shaderbase && py -3 scripts/cron_sync.py --project g66 >> data/sync.log 2>&1

Windows Task Scheduler：
  schtasks /create /tn "shaderbase sync" /tr "py -3 C:\\path\\to\\scripts\\cron_sync.py --project g66" /sc hourly
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, '.')


def main():
    parser = argparse.ArgumentParser(description="shaderbase 定时 sync 脚本")
    parser.add_argument("--db", default="", help="SQLite 数据库路径（默认 data/shaderbase.db）")
    parser.add_argument("--project", default="g66", help="项目名")
    parser.add_argument("--log", default="", help="日志文件路径（默认 stdout）")
    args = parser.parse_args()

    def log(msg: str):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, flush=True)
        if args.log:
            with open(args.log, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    log("=" * 60)
    log("shaderbase sync 开始")

    from shaderbase.store.connection import connect, resolve_root_path
    from shaderbase.store.incremental import incremental_update

    conn = connect(args.db)

    # 查 root_path
    cur = conn.execute("SELECT root_path FROM projects WHERE name = ?", (args.project,))
    row = cur.fetchone()
    if not row or not row["root_path"]:
        log(f"ERROR: project '{args.project}' not found or root_path empty")
        return 1
    root_path = row["root_path"]
    abs_root = resolve_root_path(root_path)
    log(f"root_path: {root_path} → {abs_root}")

    # 1. git pull
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            capture_output=True, text=True, cwd=abs_root, timeout=120,
        )
    except Exception as e:
        log(f"git pull FAILED: {e}")
        return 1
    pull_time = time.perf_counter() - t0
    if result.returncode != 0:
        log(f"git pull FAILED (rc={result.returncode}): {result.stderr.strip()}")
        return 1
    log(f"git pull OK ({pull_time:.1f}s): {result.stdout.strip() or 'Already up to date.'}")

    # 2. 增量更新
    t1 = time.perf_counter()
    try:
        inc_result = incremental_update(conn, args.project, abs_root)
    except Exception as e:
        log(f"incremental_update FAILED: {e}")
        return 1
    inc_time = time.perf_counter() - t1

    if inc_result.get("message") == "no changes detected":
        log(f"incremental: no changes detected ({inc_time:.1f}s)")
    else:
        log(f"incremental OK ({inc_time:.1f}s): "
            f"dirty={len(inc_result.get('dirty',[]))} "
            f"new={len(inc_result.get('new',[]))} "
            f"deleted={len(inc_result.get('deleted',[]))} "
            f"reindexed={inc_result.get('reindexed',0)}")
        cr = inc_result.get("calls_resolved")
        if cr:
            log(f"  calls_resolved: total={cr['total']} resolved={cr['resolved']} "
                f"unresolved={cr['unresolved']} intrinsic={cr['intrinsic']}")

    total_time = time.perf_counter() - t0
    log(f"sync 完成，总耗时 {total_time:.1f}s")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
