# shaderbase

G66 shader 知识图谱——把 shader-source 仓库索引成结构化知识图谱，让 AI Agent 通过 MCP 查询 shader 代码的关系结构。

## 核心能力

- **grammar 解析**：自研 tree-sitter grammar（fork tree-sitter-hlsl），覆盖 G66 特化语法（#art / technique / annotation / cbuffer / SamplerState），99.67% 文件解析率
- **节点抽取**：7 类节点（Function / Struct / Uniform / Texture / SamplerState / Technique / CBuffer），1.6 万节点
- **边抽取**：CALLS（跨文件 resolve，85% 召回率）/ INCLUDES / HAS_MEMBER / IS_ENTRY_POINT / EXPOSES_TECHNIQUE，3.7 万边
- **PreprocessorView**：条件编译双视图——索引时全分支签名，查询时按 Agent 传入的 macros 算 active
- **Web 可视化**：3D 星系图（three.js + Bloom 辉光），浏览器实时查看图谱
- **MCP server**：14 个查询工具，AI Agent 通过 MCP 协议调用

## 快速开始

### 1. 环境准备

```bash
# Python 3.9+
py -3 --version

# 装 shaderbase 主包
cd "shader code konwledge"
pip install -e .

# 装 grammar Python 绑定（需要 C 编译器）
cd g66-shader-grammar
pip install -e . --no-build-isolation
cd ..

# 装 web/MCP 依赖
pip install fastapi uvicorn
```

### 2. 建图（19 秒）

```bash
py -3 -c "
from shaderbase.store.connection import connect
from shaderbase.store.indexer import index_project
conn = connect('shaderbase.db')
result = index_project(conn, r'D:\douzhongjun\work\shader\shader-source', 'g66')
print(result)
"
```

### 3. 启动 Web 可视化

```bash
py -3 -m shaderbase.web --db shaderbase.db --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

### 4. 启动 MCP server

```bash
py -3 -m shaderbase.mcp_server --db shaderbase.db --host 0.0.0.0 --port 8001
# MCP 端点：http://你的IP:8001/sse
```

### 5. 配置 ZCode 连接 MCP

编辑 `C:\Users\<用户名>\.zcode\v2\config.json`，在 `mcp.servers` 里加：

```json
{
  "mcp": {
    "servers": {
      "shaderbase": {
        "type": "http",
        "url": "http://127.0.0.1:8001/sse",
        "enabled": true
      }
    }
  }
}
```

重启 ZCode，Agent 就能调 `mcp__shaderbase__search_shader` 等工具了。

## 给同事用（远程连接）

### 服务器端

```bash
# 1. 启动 MCP server，绑定 0.0.0.0（允许远程）
py -3 -m shaderbase.mcp_server --db shaderbase.db --host 0.0.0.0 --port 8001

# 2. 放行防火墙端口（管理员权限）
netsh advfirewall firewall add rule name="shaderbase MCP" dir=in action=allow protocol=TCP localport=8001
```

### 同事端

同事在 ZCode 的 `config.json` 里加：

```json
{
  "mcp": {
    "servers": {
      "shaderbase": {
        "type": "http",
        "url": "http://服务器IP:8001/sse",
        "enabled": true
      }
    }
  }
}
```

**同事不需要装 Python / grammar / clone shader-source / 建图**——全用服务器的。

## MCP 工具清单（14 个）

### 查询类（最常用）

| 工具 | 用途 | 示例问题 |
|---|---|---|
| `search_shader` | 按名字/类型/文件搜节点 | "CalcWorldNormal 在哪定义？" |
| `trace_calls` | BFS 遍历调用链（inbound/outbound/both） | "谁调用了 CalcWorldNormal？" "改了 X 影响哪些 effect？" |
| `get_code_snippet` | 读函数源码 | "给我看 vs_main 的实现" |
| `get_definition` | 找符号定义位置 | "PS_INPUT 在哪定义？" |
| `get_references` | 找符号被引用的位置 | "u_frame_time 被谁用了？" |

### shader 语义类

| 工具 | 用途 | 示例问题 |
|---|---|---|
| `find_entry_points` | 找 vs_main/ps_main + technique | "这个 effect 的入口函数？" |
| `find_uniform_usage` | 找 uniform 被哪些函数用 | "u_roughness 被谁用了？" |
| `trace_stage_flow` | VS→PS semantic 数据流 | "TEXCOORD2 流到哪些 PS 输入？" |
| `get_material_files` | 材质三件套 | "pbr_rock 的三个文件？" |

### 管理/架构类

| 工具 | 用途 | 示例问题 |
|---|---|---|
| `get_architecture` | 仓库架构总览 | "有多少函数？分布怎样？" |
| `detect_changes` | git diff → 影响范围 | "改了 common.hlsl 影响哪些？" |
| `find_dead_code` | 找没人调用的函数 | "有没有死代码？" |
| `index_status` | 索引状态/错误列表 | "索引建好了没？" |
| `incremental_update` | 触发增量更新 | "shader 更新了，刷新图谱" |

## 项目结构

```
shader code konwledge/                ← 仓库根（git: PeanutsDou/shaderbase）
├── g66-shader-grammar/              ← grammar 子项目（fork tree-sitter-hlsl）
│   ├── grammar.js                   ← G66 特化规则
│   ├── bindings/python/             ← Python 绑定（tree_sitter_g66_shader）
│   └── src/parser.c                 ← tree-sitter generate 产物
├── shaderbase/                      ← 主包
│   ├── parser/                      ← grammar 加载 + AST 遍历
│   ├── extract/                     ← 节点抽取 + 边抽取 + CALLS resolve
│   ├── preprocessor/                ← PreprocessorView（条件编译双视图）
│   ├── store/                       ← SQLite 图存储 + 全量/增量索引
│   ├── web/                         ← Web 可视化（FastAPI + three.js）
│   └── mcp_server/                  ← MCP server（14 个工具）
├── test/fixtures/nodes/             ← 节点抽取 fixture（53 个 expected.yaml）
├── pyproject.toml
└── SHADERBASE_DEV_PLAN.md           ← 开发方案
```

## 技术栈

- **语言**：Python 3.9+
- **解析**：tree-sitter（自研 grammar，fork tree-sitter-hlsl）
- **存储**：SQLite（标准库，WAL 模式）
- **预处理**：PreprocessorView（Python 重写，借鉴 nsp-intellision 算法）
- **Web**：FastAPI + three.js（vendored，无 npm 构建）
- **MCP**：FastAPI + SSE（支持远程连接）
- **查询**：SQL（SQLite 索引查询，毫秒级）

## 性能

| 场景 | 耗时 |
|---|---|
| 全量建图（1227 文件） | 19 秒 |
| 全量 parse + PV | 3.5 秒 |
| 增量更新（改 1-3 文件） | 1-2 秒 |
| 单次 MCP 查询 | <50ms |

## License

MIT
