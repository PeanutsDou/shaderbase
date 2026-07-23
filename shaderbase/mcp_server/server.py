"""server — MCP server（HTTP/SSE 模式）。

用 FastAPI 实现 MCP 协议的 SSE 端点，Agent 通过 HTTP 连接。
跟 Web UI 同机部署，共用 data/shaderbase.db。

启动：
  python -m shaderbase.mcp_server --db data/shaderbase.db --host 0.0.0.0 --port 8001
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..store.connection import connect
from .tools import TOOL_DEFINITIONS, execute_tool


def create_mcp_app(db_path: str = "", default_project: str = "g66") -> FastAPI:
    """创建 MCP server FastAPI 应用。"""
    app = FastAPI(title="shaderbase MCP", version="0.1.0")
    conn = connect(db_path)
    app.state.conn = conn
    app.state.default_project = default_project

    # 查 root_path
    cur = conn.execute("SELECT root_path FROM projects WHERE name = ?", (default_project,))
    row = cur.fetchone()
    app.state.root_path = row["root_path"] if row else ""

    # ---- MCP 协议端点（Streamable HTTP + SSE 双兼容）----
    # ZCode 新版用 Streamable HTTP：直接 POST JSON-RPC 到 url。
    # 旧版用 SSE：GET /sse 建流，POST /messages 调用。
    # 这里让 /sse、/messages、/ 三个路径都接受 GET 和 POST，
    # GET 走 SSE 初始化流，POST 走 JSON-RPC 处理。

    async def _sse_init_stream():
        """SSE 初始化流（旧版客户端用 GET 建流）。"""
        init_msg = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "shaderbase", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            },
        }
        yield f"event: message\ndata: {json.dumps(init_msg)}\n\n"

    async def _handle_jsonrpc(body: dict) -> JSONResponse:
        """处理 MCP JSON-RPC 请求，返回 JSON 响应。"""
        method = body.get("method")
        req_id = body.get("id")
        params = body.get("params", {})

        # ---- initialize ----
        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "shaderbase", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            })

        # ---- tools/list ----
        if method == "tools/list":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "inputSchema": t["inputSchema"],
                        }
                        for t in TOOL_DEFINITIONS
                    ]
                },
            })

        # ---- tools/call ----
        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            try:
                result = execute_tool(
                    app.state.conn,
                    app.state.default_project,
                    app.state.root_path,
                    tool_name,
                    tool_args,
                )
                # MCP 工具返回格式：content 数组
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                        ]
                    },
                })
            except Exception as e:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": str(e)},
                })

        # ---- 未知方法 ----
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        })

    # /sse：GET 走 SSE 流，POST 走 JSON-RPC（旧版 SSE 客户端兼容）
    @app.get("/sse")
    async def sse_get(request: Request):
        return StreamingResponse(_sse_init_stream(), media_type="text/event-stream")

    @app.post("/sse")
    async def sse_post(request: Request):
        body = await request.json()
        return await _handle_jsonrpc(body)

    # /mcp：Streamable HTTP 端点（POST JSON-RPC）
    @app.post("/mcp")
    async def mcp_post(request: Request):
        body = await request.json()
        return await _handle_jsonrpc(body)

    @app.get("/mcp")
    async def mcp_get(request: Request):
        return {"status": "ok", "server": "shaderbase", "tools": len(TOOL_DEFINITIONS)}

    # /messages：旧版 SSE 客户端的 POST 路径
    @app.post("/messages")
    async def messages_post(request: Request):
        body = await request.json()
        return await _handle_jsonrpc(body)

    # /：根路径，Streamable HTTP 主端点（POST JSON-RPC）
    @app.post("/")
    async def root_post(request: Request):
        body = await request.json()
        return await _handle_jsonrpc(body)

    @app.get("/")
    async def root_get(request: Request):
        return {"status": "ok", "server": "shaderbase", "tools": len(TOOL_DEFINITIONS)}

    # ---- 健康检查 ----
    @app.get("/health")
    async def health():
        return {"status": "ok", "tools": len(TOOL_DEFINITIONS)}

    return app


def main():
    parser = argparse.ArgumentParser(description="shaderbase MCP server")
    parser.add_argument("--db", default="", help="SQLite 数据库路径（默认 data/shaderbase.db）")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址（0.0.0.0 = 允许远程）")
    parser.add_argument("--port", type=int, default=8001, help="端口")
    parser.add_argument("--project", default="g66", help="默认项目名")
    args = parser.parse_args()

    import uvicorn
    app = create_mcp_app(args.db, args.project)
    print(f"shaderbase MCP server: http://{args.host}:{args.port}")
    print(f"  SSE:  http://{args.host}:{args.port}/sse")
    print(f"  POST: http://{args.host}:{args.port}/messages")
    print(f"  tools: {len(TOOL_DEFINITIONS)}")
    print(f"  db: {args.db}")
    print(f"  project: {args.project}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
