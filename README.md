# shaderbase

G66 shader 知识图谱——把 shader-source 仓库索引成结构化知识图谱，让 AI Agent 通过 MCP 查询 shader 代码的关系结构。

## 核心能力

- **grammar 解析**：自研 tree-sitter grammar（fork tree-sitter-hlsl），覆盖 G66 特化语法（#art / technique / annotation / cbuffer / SamplerState / PLSLayout），99.51% 文件解析率
- **节点抽取**：7 类节点（Function / Struct / Uniform / Texture / SamplerState / Technique / CBuffer），1.6 万节点
- **边抽取**：9 类边（CALLS / INCLUDES / HAS_MEMBER / DECLARES_UNIFORM / USES_UNIFORM / FLOWS_TO / IS_ENTRY_POINT / EXPOSES_TECHNIQUE / CONDITIONAL_ON），4.9 万边
- **PreprocessorView**：条件编译双视图——索引时全分支签名，查询时 Agent 传 macros 算 active，每条边带 active 标记
- **增量更新**：mtime/size 检测 dirty + INCLUDE 反向闭包，改 1-3 文件 4.5 秒（非全量 22 秒）
- **Web 可视化**：2D Canvas 力向图，浏览器实时查看图谱 + 拖拽交互
- **MCP server**：15 个查询工具（5 个支持 macros 参数 + sync_repo 自动更新），AI Agent 通过 MCP 协议调用
- **路径可移植**：file_path 存相对路径，图谱 SQLite 可跨机器使用；shader-source 内化到项目子目录

## 部署

### 1. 环境准备

```bash
# Python 3.9+
py -3 --version

# 装 shaderbase 主包
pip install -e .

# 装 grammar Python 绑定（需要 C 编译器，如 Visual Studio Build Tools）
cd g66-shader-grammar
pip install -e . --no-build-isolation
cd ..

# 装 web/MCP 依赖
pip install fastapi uvicorn

# clone shader 源码到项目内子目录（不进 git，但目录位置固定）
git clone ssh://git@git-nebula.nie.netease.com:32200/g66/shader/shader-source.git shader-source
```

### 2. 建图（22 秒）

```bash
py -3 -c "
from shaderbase.store.connection import connect
from shaderbase.store.indexer import index_project
conn = connect()  # 默认 data/shaderbase.db
result = index_project(conn, 'shader-source', 'g66')
print(result)
"
```

### 3. 启动服务

```bash
# Web 可视化（可选，端口 8000）
py -3 -m shaderbase.web --port 8000
# 浏览器打开 http://127.0.0.1:8000

# MCP server（核心，端口 8001）
py -3 -m shaderbase.mcp_server --host 127.0.0.1 --port 8001
# MCP 端点：http://127.0.0.1:8001/mcp

# 给同事远程用（绑定 0.0.0.0）
py -3 -m shaderbase.mcp_server --host 0.0.0.0 --port 8001
# 放行防火墙：netsh advfirewall firewall add rule name="shaderbase MCP" dir=in action=allow protocol=TCP localport=8001
```

MCP server 支持两种协议端点：
- `/mcp`：Streamable HTTP（POST JSON-RPC，ZCode 新版用这个）
- `/sse`：SSE 流 + POST /messages（旧版客户端兼容）

### 4. 配置 ZCode 连接 MCP

编辑 `C:\Users\<用户名>\.zcode\cli\config.json`，在 `mcp.servers` 里加 `shaderbase`：

```json
{
  "mcp": {
    "servers": {
      "shaderbase": {
        "type": "http",
        "url": "http://127.0.0.1:8001/mcp",
        "enabled": true
      }
    }
  }
}
```

重启 ZCode，Agent 就能调 `mcp__shaderbase__search_shader` 等 15 个工具了。

### 5. 更新图谱

```bash
# 方式 1：MCP 工具（推荐，Agent 调 sync_repo）
# sync_repo = git pull + 增量更新，一条命令搞定

# 方式 2：命令行
cd shader-source && git pull --ff-only && cd ..
py -3 -c "
from shaderbase.store.connection import connect
from shaderbase.store.incremental import incremental_update
conn = connect()
print(incremental_update(conn, 'g66', 'shader-source'))
"

# 方式 3：定时 sync 脚本（给 cron/task scheduler 用）
py -3 scripts/cron_sync.py --project g66
```

### 6. 定时自动更新（服务器部署用）

```bash
# Linux cron（每小时跑一次）
crontab -e
# 0 * * * * cd /path/to/shaderbase && py -3 scripts/cron_sync.py --project g66 >> data/sync.log 2>&1

# Windows Task Scheduler
schtasks /create /tn "shaderbase sync" /tr "py -3 C:\path\to\scripts\cron_sync.py --project g66" /sc hourly
```

## 给同事用（远程连接）

同事不需要装 Python / grammar / clone shader-source / 建图——全用服务器的。查到的 `file_path` 是相对路径（如 `base/animated_grass.nsf`），`get_code_snippet` 在服务端读源码返回文本，同事不需要本地有源码。

同事只需在 ZCode 的 `C:\Users\<用户名>\.zcode\cli\config.json` 里加：

```json
{
  "mcp": {
    "servers": {
      "shaderbase": {
        "type": "http",
        "url": "http://服务器IP:8001/mcp",
        "enabled": true
      }
    }
  }
}
```

## MCP 工具清单（15 个）

### 查询类（带 ✨ 的支持 macros 参数算条件编译 active）

| 工具 | 用途 | 示例问题 |
|---|---|---|
| `search_shader` | 按名字/类型/文件搜节点 | "CalcWorldNormal 在哪定义？" |
| `trace_calls` ✨ | BFS 遍历调用链（inbound/outbound/both） | "谁调用了 CalcWorldNormal？" |
| `get_code_snippet` | 读函数源码 | "给我看 vs_main 的实现" |
| `get_definition` | 找符号定义位置 | "PS_INPUT 在哪定义？" |
| `get_references` ✨ | 找符号被引用的位置 | "u_frame_time 被谁用了？" |

### shader 语义类

| 工具 | 用途 | 示例问题 |
|---|---|---|
| `find_entry_points` ✨ | 找 vs_main/ps_main + technique | "这个 effect 的入口函数？" |
| `find_uniform_usage` ✨ | 找 uniform 被哪些函数用（查 USES_UNIFORM 边） | "u_roughness 被谁用了？" |
| `trace_stage_flow` ✨ | VS→PS semantic 数据流（查 FLOWS_TO 边） | "TEXCOORD2 流到哪些 PS 输入？" |
| `get_material_files` | 材质三件套 | "pbr_rock 的三个文件？" |

### 管理/架构类

| 工具 | 用途 | 示例问题 |
|---|---|---|
| `get_architecture` | 仓库架构总览 | "有多少函数？分布怎样？" |
| `detect_changes` | git diff → 影响范围 | "改了 common.hlsl 影响哪些？" |
| `find_dead_code` | 找没人调用的函数 | "有没有死代码？" |
| `index_status` | 索引状态/错误列表 | "索引建好了没？" |
| `incremental_update` | 触发增量更新（mtime/size 检测 + 反向闭包） | "shader 更新了，刷新图谱" |
| `sync_repo` | git pull + 增量更新（一条命令完成代码同步和图谱刷新） | "拉最新代码并更新图谱" |

### macros 参数用法（条件编译 active 标注）

5 个带 ✨ 的工具支持 `macros` 参数，传入条件编译宏配置后，每条返回的边/引用带 `active` 字段：

```python
# MCP 调用示例
trace_calls(
    function_name="GetSeasonColorMeadow",
    macros={"SEASON_SUPPORT": 1, "ENGINE_SEASON_SUPPORT": 1}
)
# 返回的每条边带 active=true/false：
#   #if SEASON_SUPPORT 分支内的边 → active=true
#   #else 分支内的边 → active=false
```

不传 macros 时返回全部边（带 conditional_signature 但不算 active）。

## 项目结构

```
shader code konwledge/                ← 项目根
├── shaderbase/                      ← 主包
│   ├── parser/                      ← grammar 加载 + AST 遍历
│   ├── extract/                     ← 节点抽取 + 边抽取（9 类边）+ CALLS resolve
│   ├── preprocessor/                ← PreprocessorView（条件编译双视图 + macro_expander）
│   ├── store/                       ← SQLite 图存储 + 全量/增量索引 + 路径相对化
│   ├── web/                         ← Web 可视化（FastAPI + Canvas 2D 力向图）
│   └── mcp_server/                  ← MCP server（15 个工具，5 个支持 macros）
├── g66-shader-grammar/              ← grammar 子项目（fork tree-sitter-hlsl）
│   ├── grammar.js                   ← G66 特化规则
│   ├── bindings/python/             ← Python 绑定（tree_sitter_g66_shader）
│   ├── src/parser.c                 ← tree-sitter generate 产物
│   └── test/corpus/                 ← 回归测试 corpus（7 类 40 个样例）
├── shader-source/                   ← shader 源码（gitignore 忽略，clone 进来）
├── test/fixtures/                   ← 防退化 fixture（4 层 112 个）
│   ├── nodes/                       ← 层 2：节点抽取（53 个）
│   ├── edges/                       ← 层 3：边抽取（10 个）
│   └── resolve/                     ← 层 4：跨文件 resolve（3 个）
├── data/                            ← 生成物（gitignore 忽略）
│   ├── shaderbase.db                ← 图谱 SQLite（每个环境自己建图）
│   ├── shaderbase.db-wal
│   └── shaderbase.db-shm
├── docs/                            ← 设计/开发文档
│   ├── SHADERBASE_DESIGN.md
│   ├── SHADERBASE_DEV_PLAN.md
│   ├── SHADERBASE_GRAMMAR_DEV_PLAN.md
│   └── SHADERBASE_GRAMMAR_INVENTORY.md
├── scripts/                         ← fixture 校验 + 候选生成 + cron sync
├── pyproject.toml
├── shaderbase_mcp_连接提示词.txt      ← 给同事连接 MCP 用的提示词
└── README.md
```

## 测试

```bash
# 4 层 fixture 防退化（112 个全 PASS）
py -3 scripts/check_fixtures.py              # 53 节点 fixture
py -3 scripts/check_edge_fixtures.py         # 10 边 fixture
py -3 scripts/check_resolve_fixtures.py      # 3 跨文件 fixture
py -3 -m shaderbase.preprocessor.tests.check_pv_fixtures  # 46 PV fixture

# grammar corpus 回归（40 个全 PASS）
cd g66-shader-grammar && npx tree-sitter test

# PV 全量冒烟（1233 文件，0 crash）
py -3 -m shaderbase.preprocessor.bench_pv_full
```

## 性能

| 场景 | 耗时 |
|---|---|
| 全量建图（1233 文件） | 24 秒 |
| 全量 parse + PV | 3.8 秒 |
| 增量更新（改 1 文件） | 4.5 秒（含 resolve_calls） |
| dirty 检测（1233 文件） | 0.12 秒 |
| 单次 MCP 查询 | <50ms |
| MCP 查询 + macros 算 active | <200ms |

## 技术栈

- **语言**：Python 3.9+
- **解析**：tree-sitter（自研 grammar，fork tree-sitter-hlsl）
- **存储**：SQLite（标准库，WAL 模式）
- **预处理**：PreprocessorView（Python 重写，借鉴 nsp-intellision 算法）
- **Web**：FastAPI + Canvas 2D（vanilla JS，无构建链）
- **MCP**：FastAPI + SSE（支持远程连接）
- **查询**：SQL（SQLite 索引查询，毫秒级）

## License

MIT
