"""connection — SQLite 连接管理。

单文件 SQLite，标准库 sqlite3，不自写页写入器（DEV_PLAN §12 选型）。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 项目根目录（shaderbase 包的上级，含 pyproject.toml / shader-source/ 等）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def repo_root() -> str:
    """返回项目根目录绝对路径。

    projects.root_path 存相对路径（如 'shader-source'）时，
    用 os.path.join(repo_root(), root_path) 解析成绝对路径。
    """
    return str(_REPO_ROOT)


def resolve_root_path(root_path: str) -> str:
    """把 root_path 解析成绝对路径。

    - 绝对路径 → 原样返回（兼容旧数据）
    - 相对路径 → 相对项目根目录解析
    """
    if not root_path:
        return ""
    if os.path.isabs(root_path):
        return root_path
    return os.path.normpath(os.path.join(str(_REPO_ROOT), root_path))


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
