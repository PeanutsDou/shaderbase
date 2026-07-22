"""connection — SQLite 连接管理。

单文件 SQLite，标准库 sqlite3，不自写页写入器（DEV_PLAN §12 选型）。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: str = "shaderbase.db") -> sqlite3.Connection:
    """打开/创建 SQLite 数据库，自动建表。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """执行 schema.sql 建表（IF NOT EXISTS，安全幂等）。"""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def drop_project(conn: sqlite3.Connection, project: str) -> None:
    """删除项目的所有数据（节点/边/文件元数据/反向依赖）。"""
    conn.executemany("DELETE FROM nodes WHERE project = ?", [(project,)])
    conn.executemany("DELETE FROM edges WHERE project = ?", [(project,)])
    conn.executemany("DELETE FROM file_meta WHERE project = ?", [(project,)])
    conn.executemany("DELETE FROM reverse_deps WHERE project = ?", [(project,)])
    conn.executemany("DELETE FROM projects WHERE name = ?", [(project,)])
    conn.commit()


__all__ = ["connect", "drop_project"]
