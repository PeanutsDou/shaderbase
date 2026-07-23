"""server — MCP server（HTTP/SSE 模式）。

用 FastAPI 实现 MCP 协议的 SSE 端点，Agent 通过 HTTP 连接。
跟 Web UI 同机部署，共用 shaderbase.db。

启动：
  python -m shaderbase.mcp_server --db shaderbase.db --host 0.0.0.0 --port 8001
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


def create_mcp_app(db_path: str = "shaderbase.db", default_project: str = "g66") -> FastAPI:
    """创建 MCP server FastAPI 应用。"""
    app = FastAPI(title="shaderbase MCP", version="0.1.0")
    conn = connect(db_path)
    app.state.conn = conn
    app.state.default_project = default_project

    # 查 root_path
    cur = conn.execute("SELECT root_path FROM projects WHERE name = ?", (default_project,))
    row = cur.fetchone()
    app.state.root_path = row["root_path"] if row else ""

    # ---- MCP 协议端点 ----

    @app.get("/sse")
    async def sse_endpoint(request: Request):
        """SSE 端点 — 建立连接，发送 initialize 响应。

        简化实现：用 SSE 发一个初始化消息，然后保持连接。
        实际 MCP SSE 协议更复杂，这里用 POST /messages 处理工具调用。
        """
        session_id = str(uuid.uuid4())

        async def event_stream():
            # 发初始化事件
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

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/messages")
    @app.post("/")
    async def messages_endpoint(request: Request):
        """处理 MCP JSON-RPC 请求。

        同时挂载 /messages 和 /（根路径），兼容不同 MCP client 的请求路径。
        """
        body = await request.json()
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

    # ---- 健康检查 ----
    @app.get("/health")
    async def health():
        return {"status": "ok", "tools": len(TOOL_DEFINITIONS)}

    return app


def main():
    parser = argparse.ArgumentParser(description="shaderbase MCP server")
    parser.add_argument("--db", default="shaderbase.db", help="SQLite 数据库路径")
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
