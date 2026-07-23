"""app — FastAPI 应用 + 路由定义。"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import queries
from .layout import compute_layout, get_repo_info
from ..store.connection import connect

_STATIC_DIR = Path(__file__).parent / "static"


def _parse_macros_query(macros_str: Optional[str]) -> Optional[dict]:
    """解析 ?macros=KEY:1,KEY:0 查询参数成 dict。

    None / 空串 → None（不算 active，向后兼容）。
    非空 → 解析成 {KEY: int}。
    """
    if not macros_str:
        return None
    out: dict[str, int] = {}
    for part in macros_str.split(","):
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
    return out if out else None


def create_app(db_path: str = "shaderbase.db", default_project: str = "g66") -> FastAPI:
    """创建 FastAPI 应用。

    db_path: SQLite 数据库路径
    default_project: 默认项目名
    """
    app = FastAPI(title="shaderbase 知识图谱", version="0.1.0")
    conn = connect(db_path)
    app.state.conn = conn
    app.state.default_project = default_project

    # ---- 页面 ----

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = _STATIC_DIR / "index.html"
        if not html_path.exists():
            return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    # 静态文件
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ---- API ----

    @app.get("/api/overview")
    async def api_overview(project: Optional[str] = None):
        proj = project or app.state.default_project
        return queries.get_overview(app.state.conn, proj)

    @app.get("/api/search")
    async def api_search(
        name: Optional[str] = Query(None),
        kind: Optional[str] = Query(None),
        file: Optional[str] = Query(None),
        limit: int = Query(50, le=200),
        offset: int = Query(0),
        project: Optional[str] = None,
    ):
        proj = project or app.state.default_project
        return queries.search_nodes(
            app.state.conn, proj, name, kind, file, limit, offset
        )

    @app.get("/api/node/{node_id}")
    async def api_node(node_id: int):
        node = queries.get_node(app.state.conn, node_id)
        if not node:
            raise HTTPException(404, "node not found")
        return node

    @app.get("/api/node/{node_id}/connections")
    async def api_connections(node_id: int, project: Optional[str] = None):
        proj = project or app.state.default_project
        return queries.get_connections(app.state.conn, node_id, proj)

    @app.get("/api/neighbors/{node_id}")
    async def api_neighbors(
        node_id: int,
        limit: int = Query(50, le=200),
        project: Optional[str] = None,
    ):
        proj = project or app.state.default_project
        return queries.get_neighbors(app.state.conn, node_id, proj, limit)

    @app.get("/api/subgraph")
    async def api_subgraph(
        function: str = Query(...),
        depth: int = Query(3, le=5),
        limit: int = Query(100, le=500),
        macros: Optional[str] = Query(None, description="条件编译宏，格式 KEY:1,KEY:0"),
        project: Optional[str] = None,
    ):
        proj = project or app.state.default_project
        macros_dict = _parse_macros_query(macros)
        return queries.get_subgraph(app.state.conn, proj, function, depth, limit, macros_dict)

    @app.get("/api/source/{node_id}")
    async def api_source(node_id: int, context: int = Query(0, le=20)):
        return queries.get_source(app.state.conn, node_id, context)

    @app.post("/api/index")
    async def api_index(
        root_path: str = "",
        mode: str = "full",
        project: Optional[str] = None,
    ):
        """触发建图。mode=full（默认）全量重建，mode=incremental 增量更新。"""
        proj = project or app.state.default_project
        if not root_path:
            cur = app.state.conn.execute(
                "SELECT root_path FROM projects WHERE name = ?", (proj,)
            )
            row = cur.fetchone()
            if not row or not row["root_path"]:
                raise HTTPException(400, "root_path required")
            root_path = row["root_path"]
        if mode == "incremental":
            from ..store.incremental import incremental_update
            result = incremental_update(app.state.conn, proj, root_path)
        else:
            from ..store.indexer import index_project
            result = index_project(app.state.conn, root_path, proj)
        return result

    @app.get("/api/layout")
    async def api_layout(
        max_nodes: int = Query(5000, le=100000),
        project: Optional[str] = None,
    ):
        """图布局数据（对齐 codebase-memory GraphData 格式）。"""
        proj = project or app.state.default_project
        return compute_layout(app.state.conn, proj, max_nodes)

    @app.get("/api/repo-info")
    async def api_repo_info(project: Optional[str] = None):
        """git remote 元数据（GitHub/内部 Git 深链用）。"""
        proj = project or app.state.default_project
        return get_repo_info(app.state.conn, proj)

    return app
