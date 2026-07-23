"""python -m shaderbase.web 入口。"""
import argparse
import sys

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="shaderbase 知识图谱可视化")
    parser.add_argument("--db", default="shaderbase.db", help="SQLite 数据库路径")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址")
    parser.add_argument("--port", type=int, default=8000, help="端口")
    parser.add_argument("--project", default="g66", help="默认项目名")
    args = parser.parse_args()

    import uvicorn
    app = create_app(args.db, args.project)
    print(f"shaderbase web UI: http://{args.host}:{args.port}")
    print(f"  db: {args.db}")
    print(f"  project: {args.project}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
