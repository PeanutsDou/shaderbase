# Shaderbase 需求拆解与实现路径

> 基于 codebase-memory-mcp 与 nsp-intellision 两个参考项目的源码调研，把"为 G66 shader-source 构建专用 AI 知识库查询能力"这件事从底层拆开。
> 
> 本文档不回避术语，每个术语首次出现都会用大白话点一句；后续单独问的术语在独立小节展开。

---

## 0. 术语速查（按出现顺序）

| 术语                                       | 一句话                                                            | 详细小节 |
| ---------------------------------------- | -------------------------------------------------------------- | ---- |
| **AST**                                  | Abstract Syntax Tree，源码解析出来的语法树，节点是函数/语句/表达式                   | §3.1 |
| **lexer / tokenizer**                    | 把源码文本切成 token 序列（关键字/标识符/字面量/标点）                               | §3.1 |
| **parser**                               | 把 token 序列建构成 AST                                              | §3.1 |
| **tree-sitter**                          | 一个增量 parser 生成器，支持 158 种语言；codebase-memory 用它                  | §3.1 |
| **LSP**                                  | Language Server Protocol，VS Code 等 IDE 与语言服务端通讯的标准协议           | §4.2 |
| **MCP**                                  | Model Context Protocol，AI Agent 跟外部工具通讯的标准协议                   | §4.3 |
| **stdio JSON-RPC**                       | 通过进程的 stdin/stdout 收发 JSON 消息，按 `Content-Length` 头分包           | §4.2 |
| **符号表 / Symbol Table**                   | "符号名 → 出现位置列表"的倒排索引                                            | §5.2 |
| **知识图谱**                                 | 节点+边的图结构，节点是实体、边是实体间关系                                         | §5.3 |
| **调用图 / Call Graph**                     | 节点是函数、边是"A 调用了 B"                                              | §5.3 |
| **VS / PS**                              | Vertex Shader / Pixel Shader，GPU pipeline 的两个 stage            | §6.2 |
| **语义绑定 / Semantic**                      | shader 里 `float4 x : TEXCOORD0` 的 `: TEXCOORD0`，标识数据槽          | §6.2 |
| **uniform**                              | shader 声明、引擎每帧喂值的全局变量（投影矩阵/帧时间/材质参数等）                          | §6.3 |
| **cbuffer**                              | Constant Buffer，把一组 uniform 打包按 GPU 寄存器布局喂入                    | §6.3 |
| **annotation**                           | shader 变量后 `<>` 块里的元信息，给编辑器看（SasUiLabel/Min/Max）               | §6.3 |
| **`#art` 宏**                             | G66 特化的材质开关，`#art NAME "desc" "BOOL"` 在编辑器勾选即 `#define NAME 1` | §6.4 |
| **include 闭包 / include closure**         | 一个文件传递 `#include` 链能到达的所有文件集合                                  | §7.2 |
| **reverse include**                      | 反向：哪些文件 include 了我                                             | §7.2 |
| **条件编译 / conditional compilation**       | `#if/#ifdef/#ifndef/#elif/#else/#endif` 控制不同分支编译               | §7.3 |
| **PreprocessorView**                     | nsp-intellision 的预处理状态机，按行算"当前 profile 下这行是否编译"                | §7.3 |
| **branchSignatureKey / branchFamilyKey** | 行所在条件分支的签名/家族键，用于多分支 references                                | §7.3 |
| **epoch（文档 epoch）**                      | 文本版本的数字戳，文本一改 epoch++，缓存按 epoch 失效                             | §8.2 |
| **shard cache**                          | 每个文件一个独立磁盘缓存文件，按 hash 命名                                       | §8.3 |
| **增量索引 / incremental index**             | 只重做变更影响范围，不重建全量                                                | §8.1 |
| **CALLS 边**                              | 图里"A 调用 B"的有向边                                                 | §5.3 |
| **FLOWS_TO 边**                           | shader 特有：VS 输出语义 → PS 输入语义的数据流边                               | §6.5 |
| **trace_path**                           | codebase-memory 的工具，沿 CALLS 边 BFS 遍历调用链                        | §9.2 |
| **detect_changes**                       | codebase-memory 的工具，git diff → 影响符号 + 爆炸半径                     | §9.2 |
| **entry point**                          | shader 入口函数 `vs_main`/`ps_main`/`cs_main`，由 GPU pipeline 调用    | §6.5 |
| **technique**                            | shader effect 声明块，告诉引擎 effect 名 + VS/PS 入口                     | §6.6 |

---

## 1. 一句话需求陈述

**目标**：为 G66 的 `shader-source` 仓库构建一个专用的、轻量的、能实时更新的 shader 知识库，让公司 Agent 通过它理解/检索/生成符合 G66 特化格式的 shader 代码。

**三个硬约束**：

1. **专用**：只针对 G66 的 `.nsf`/`.hlsl`/`.fxh` 格式，不做通用语言
2. **实时更新**：shader-source 高频更新（手动触发的频率也不低），索引必须能增量跟上
3. **图谱而非符号表**：Agent 要回答"谁调用了 X / 改了 X 影响哪些地方"这种关系类问题，光有"符号→位置列表"不够

## 2. 两个参考项目对照

### 2.1 codebase-memory-mcp

**GitHub**：DeusData/codebase-memory-mcp（v0.9.0，纯 C，33k stars）
**本地**：`codebase-memory-mcp/`

**它的定位**：通用代码智能引擎，158 种语言，给 AI agent 用。把整个代码库索引成持久化知识图谱，Agent 提问时图查询，不碰源码。

**它给了什么精神**：

- 预先建图、查询不碰源码
- 多遍 pipeline（discover→structure→extract→resolve→post-pass→dump）
- 文件只 parse 一次，结果复用
- 不内嵌 LLM，让 agent 当翻译器
- MCP server 暴露给 agent
- crash 隔离（单文件崩不影响整体）
- 增量索引（基于 git diff 局部重建）
- 节点稳定性 + 边失效检测（增量正确性基础）
- 直接写 SQLite 页绕过 SQL parser（百万级边落盘快）
- worker pool（pthreads + 8MB 栈 + 原子 work-stealing index）

**它给的不通用**（90% 的通用包袱要砍）：

- 158 种 tree-sitter 语法（你只要 GLSL/HLSL 2 种）
- 11 套 Hybrid LSP 类型推断（shader 类型系统比通用语言简单）
- Cypher 查询引擎（直接用 SQL 就行）
- 自写 SQLite 页写入器（你规模小两个数量级，标准 sqlite3 库够用）
- Linux 内核级内存预算优化
- 3D 图可视化
- 43 个 agent 客户端适配（你只对接一个）

**它完全没给的**（shader 专有，是 shaderbase 的核心差异化）：

- VS→PS stage 数据流图
- uniform 声明↔使用关联
- 入口点 + stage 标记
- effect/material → shader 引用关系
- 语义绑定作为节点属性
- `#art` 材质开关建模
- cbuffer 布局建模

### 2.2 nsp-intellision

**GitHub**：yang137447/nsp-intellision（v1.0.2，C++ + TypeScript，公司同事作品）
**本地**：`nsp-intellision/`

**它的定位**：VS Code 扩展 + C++ LSP 服务端，为 G66 的 `.nsf`/`.hlsl` 提供编辑智能（补全/悬停/定义跳转/引用/重命名/语义高亮/诊断）。

**它的关键事实**（深度研读结论）：

- LSP server 是**自包含的标准 stdio JSON-RPC server**，对 VS Code 零依赖，可以独立跑
- 注册了标准 LSP 12 个 method + 自定义 `nsf/*` 15 个 method
- workspace index 抽的实体只有 5 类：变量/FX块（kind=8）、函数（kind=12）、cbuffer 字段（kind=13）、宏（kind=14）、聚合类型 struct/cbuffer/technique（kind=23）
- **不抽 typedef、不抽函数调用边、不抽类型引用边、不抽作用域层级**
- **"relation" 字段名误导**：实际是"符号出现位置"，不是"调用关系边"
- **本质是"符号倒排表 + 文件 include 邻接表"，不是知识图谱**
- PreprocessorView 把 G66 预处理吃透了：`#art`/include guard/多 profile/分支家族全有
- call_query 是**实时算，不持久化**，且只查 active unit 的 include 闭包，不做全工作区
- 增量更新机制专业：被动 file-watch + 后台 reindex + shard 化磁盘 cache + epoch 失效 + early snapshot + reverse include 影响范围

**它给的能复用的**（底层基础，价值极高）：

1. PreprocessorView（`#art`/`#if`/include guard/分支家族/多 profile 完整状态机）
2. workspace_index 符号倒排表 + shard 化 cache + epoch 失效
3. resources bundle（HLSL builtin/对象类型/对象方法数据）
4. server_parse 行级解析器（认 G66 特化语法）
5. LSP server 独立运行能力 + 自定义 method 机制

**它没给的**（shaderbase 必须自造的）：

1. 函数调用关系（CALLS 边持久化）
2. 调用链遍历（trace_path）
3. VS→PS 数据流（FLOWS_TO 边）
4. uniform 声明↔使用关联
5. 材质三件套语义单元（`<name>.nsf` + `nodes/<name>_parameters.hlsl` + `nodes/<name>_nodes.hlsl` 绑成一个逻辑单元）
6. technique → pipeline 引用关系（跨语言，shader→Python）
7. 死代码检测 / 影响爆炸半径（需要调用图）
8. MCP server 暴露给 Agent

## 3. 底层基础：从源码文本到结构化数据

### 3.1 源码解析三步：文本 → token → AST

源码本质是一串字符。要"理解"它，得把它从字符变成结构化数据。三层：

**第一层：lexer / tokenizer（词法分析）**
把字符流切成 token 序列。token 是最小不可分割单元：关键字（`if`/`struct`/`return`）、标识符（`CalcWorldNormal`）、字面量（`0.5`/`"hello"`）、标点（`{`/`(`/`;`）。

例子：

```
源码: float4 u_roughness = 0.5f;
tokens: [float4] [u_roughness] [=] [0.5f] [;]
```

**第二层：parser（语法分析）**
把 token 序列建构成 AST（Abstract Syntax Tree，抽象语法树）。AST 节点是语法结构：函数定义、语句、表达式、声明等。

例子：

```
源码: float4 u_roughness = 0.5f;
AST:
  VariableDecl
    type: float4
    name: u_roughness
    init: Literal(0.5f)
```

**第三层：semantic analysis（语义分析）**
在 AST 上加类型信息、作用域、引用关系——这一步才让"u_roughness 这个名字"变成"u_roughness 这个 float4 类型的全局变量"。

### 3.2 tree-sitter：现成的 parser 生成器

**tree-sitter** 是个开源 parser 生成器，支持 158 种语言。codebase-memory 把所有语法 vendored 编译进二进制。nsp-intellision 没用 tree-sitter，自己写了行级 lexer + parser（`server_parse.*`），因为它要处理 G66 特化的 `#art`/`technique`/`SamplerState{}`/annotation `<>` 这些 tree-sitter-hlsl 不认的语法。

**对 shaderbase 的选择**：

- **走 tree-sitter-hlsl**：优点是 GLSL/HLSL/WGSL/Slang 都有现成 grammar，省力；缺点是 tree-sitter 是通用 parser，不认 G66 特化语法，要后处理
- **走自研行级 parser**（仿 nsp-intellision）：优点是 G66 特化语法一手清；缺点是要重新造轮子
- **混合方案**：tree-sitter-hlsl 做骨架 + 行级扫描补特化语法

**建议**：混合方案。tree-sitter-hlsl 抽标准 HLSL 结构（function/struct/cbuffer/typedef/全局变量），shaderbase 自己写行级扫描抽 G66 特化的 `#art`/`technique`/annotation/`SamplerState{}`。

## 4. 服务接口：LSP vs MCP

### 4.1 两个协议的本质

**LSP（Language Server Protocol）**：Microsoft 提的，IDE 跟语言服务端通讯的标准。VS Code/Cursor/Neovim 都支持。消息是 stdio JSON-RPC。

**MCP（Model Context Protocol）**：Anthropic 提的，AI Agent 跟外部工具通讯的标准。Claude Code/ZCode/Cursor 都支持。消息也是 stdio JSON-RPC。

**关键区别**：LSP 服务的是"人用 IDE 写代码"（点查询为主），MCP 服务的是"AI Agent 调用工具"（批量/遍历查询为主）。

### 4.2 LSP stdio JSON-RPC 怎么工作

通信格式（标准 LSP spec）：

```
Content-Length: 1234\r\n
\r\n
{"jsonrpc":"2.0","id":1,"method":"textDocument/hover","params":{...}}
```

- `Content-Length` 头告诉对端 JSON 体多少字节
- `\r\n\r\n` 分隔头和体
- body 是一行 JSON-RPC 消息：request（带 id 等响应）、response（带对应 id）、notification（不等响应）

nsp-intellision 的 server 端实现就 71 行 `lsp_io.cpp`，纯 stdio，无 socket。

### 4.3 MCP 怎么工作

MCP 跟 LSP 几乎一样的消息格式（都是 JSON-RPC over stdio），但：

- 注册的不是 `textDocument/hover` 这种 LSP method，而是**自定义 tools**
- 每个 tool 有 inputSchema（JSON Schema 描述参数）
- Agent 调用：`tools/call` + tool 名 + 参数

shaderbase 要让 ZCode/Claude Code 用，就走 MCP。codebase-memory 走的就是这条。

### 4.4 shaderbase 的接口选择

**目标消费者**：公司 Agent（MCP 兼容）

**推荐方案**：shaderbase 做 **MCP server**，不直接做 LSP server。

- 如果完全自造，直接 MCP
- 如果分层用 nsp-intellision，给 nsp-intellision LSP server 包一层 MCP 壳：MCP 工具内部调 LSP method（通过 stdio 子进程交互）

## 5. 数据模型：符号表 vs 知识图谱

### 5.1 这是核心决策点

**符号表**：nsp-intellision 的方式。数据结构是 `unordered_map<symbolName, vector<出现位置>>`，回答"X 在哪定义/被引用"。

**知识图谱**：codebase-memory 的方式。数据结构是图，节点是实体（函数/类/文件），边是关系（CALLS/IMPORTS/DEFINES），回答"X 调用谁/被谁调用/改了影响谁"。

### 5.2 符号表的局限

nsp-intellision 的 `IndexedRelationOccurrence`：

```cpp
struct IndexedRelationOccurrence {
  std::string name;     // 符号名
  std::string uri;      // 文件
  int line, start, end; // 出现位置
  int kind;             // 这次出现是 Identifier/Declaration/FunctionDeclaration/...
};
```

它能回答"CalcWorldNormal 在 animated_grass.nsf:185 出现过"。但它**不记这次出现指向哪个其他符号**。函数调用 `foo(a)` 跟普通读 `int x = foo` 在 occurrence 表里没有结构区别——都是 kind=0。

### 5.3 知识图谱的本质

图 = 节点（entity）+ 边（relation）。每条边是 `A → B`，类型明确。

shader 知识图谱的节点和边（shaderbase 要建的核心数据）：

**节点类型**：
| 节点 | 例子 |
|---|---|
| Function | `CalcWorldNormal`、`vs_main`、`ps_main` |
| Struct | `VS_INPUT`、`PS_INPUT`、`PixelMaterialInputs` |
| Field | struct 的字段（`base_color`/`roughness`） |
| Uniform | `u_roughness`/`u_wind_param` |
| Texture | `Tex0`/`NormalMap`/`ParamMap` |
| CBuffer | `GlobalConstants1` |
| Macro | `#define FOO 1`、`#art DETAIL_NORMAL_ENABLE` |
| Material | `pbr_rock` 这个材质单元（三件套绑定） |
| File | 单个 .nsf/.hlsl 文件 |
| Technique | `technique TShader` 声明 |
| EntryPoint | `vs_main`/`ps_main` 标记成入口 |

**边类型**：
| 边 | 含义 |
|---|---|
| `CALLS` | 函数 A 调用函数 B |
| `DEFINES` | 文件 F 定义函数/结构 F |
| `INCLUDES` | 文件 A `#include` 文件 B |
| `REFERENCES` | 符号 S 在文件 F 第 L 行被引用 |
| `FLOWS_TO` | shader 特有：VS 输出语义 → PS 输入语义 |
| `DECLARES_UNIFORM` | shader 声明 uniform U |
| `USES_UNIFORM` | 函数 F 使用 uniform U |
| `HAS_MEMBER` | struct S 有字段 F |
| `BELONGS_TO_MATERIAL` | 文件 F 属于材质 M（三件套绑定） |
| `IS_ENTRY_POINT` | 函数 F 是 shader 入口（stage: VS/PS/CS） |
| `EXPOSES_TECHNIQUE` | 文件 F 声明 technique T |
| `CONDITIONAL_ON` | 节点 N 只在某 `#if` 分支下 active |

### 5.4 为什么必须图谱

Agent 写 shader 时会问：

| 问题                                     | 符号表能答吗                | 图能答吗                                   |
| -------------------------------------- | --------------------- | -------------------------------------- |
| "CalcWorldNormal 在哪定义"                 | ✅                     | ✅                                      |
| "CalcWorldNormal 被谁调用"                 | ❌（只有出现位置，不是调用边）       | ✅（沿 CALLS 反向走）                         |
| "改了 SampleColorTextureBias 影响 100 个地方" | ❌                     | ✅（CALLS BFS 遍历）                        |
| "VS 输出 TEXCOORD2 流到哪些 PS 输入"           | ❌（无 stage 数据流概念）      | ✅（沿 FLOWS_TO）                          |
| "u_roughness 在哪些函数里被使用"                | ❌（符号表里有出现位置但不区分函数内/外） | ✅（DECLARES + USES_UNIFORM 边）           |
| "pbr_rock 这个材质由哪三个文件组成"                | ❌（按文件索引）              | ✅（Material 节点 + BELONGS_TO_MATERIAL 边） |
| "找所有没人调用的函数（死代码）"                      | ❌（没法统计 in-degree）     | ✅（CALLS in-degree=0）                   |

**结论**：shaderbase 必须建图，不能只做符号表。

## 6. shader 特有的语义建模

### 6.1 这部分是 shaderbase 的真正差异化

codebase-memory 完全没碰 shader 特有语义。nsp-intellision 碰了一些（uniform 当全局变量、struct 当 IndexedStruct）但没建关系边。**这块是 shaderbase 必须自造的核心**。

### 6.2 GPU Pipeline Stage 与 VS/PS 契约

GPU 渲染管线有几个 stage，shader 里最常用的是：

- **VS（Vertex Shader）**：每个顶点跑一次，做位置变换
- **PS（Pixel Shader / Fragment Shader）**：每个像素跑一次，算最终颜色
- **CS（Compute Shader）**：通用计算，跑在 compute 单元上

VS 算完顶点数据，要把数据传给 PS。靠**语义绑定（semantic binding）**匹配——shader 独有的机制。

看 `shaderlib/ps_input_extend.hlsl`：

```hlsl
struct PS_INPUT {
    float4 final_position: SV_Position;   // 光栅器用
    half2 texcoord0: TEXCOORD0;             // VS 输出 TEXCOORD0 → PS 在这读
    float4 world_position: TEXCOORD2;
    half3 world_normal: TEXCOORD3;
};
```

VS 写 `output.world_normal = v.world_normal`，GPU 按 `TEXCOORD3` 语义槽在三角形上**插值**，PS 拿到当前像素的插值结果。**靠同名语义匹配，不靠变量名**。

### 6.3 uniform 的本质

shader 是跑在 GPU 上的小程序，GPU 自己不知道：

- 相机投影矩阵 → `CC_PMatrix`
- 当前帧时间 → `FrameTime`
- 材质粗糙度 → `u_roughness`
- 这张贴图长什么样 → `Tex0`

这些数据都得**引擎侧 C++ 代码在每帧渲染前喂给 GPU**，shader 才能用。这种"被外部喂进来的变量"叫 uniform——"在这个 shader 一次执行里，所有像素/顶点都看到同一个值"。

shader 里只声明不赋值：

```hlsl
float u_roughness < ... > = 0.5f;  // 声明 + 编辑器默认值
```

**uniform 的三层来源**（详见之前讨论）：
| 层 | 内容 | shaderbase 能做吗 |
|---|---|---|
| L1 声明（在哪声明、什么类型） | `float u_roughness` | ✅ 必做 |
| L2 元信息（annotation、语义绑定、默认值、UI 元信息） | `<> SasUiLabel/Min/Max/TextureFile` | ✅ 应做（codebase-memory 没抽） |
| L3 运行时赋值（引擎每帧喂什么值） | `SetUniform("u_roughness", 0.5f)` | ❌ 不做（不在 shader-source 仓库内） |

### 6.4 `#art` 宏：材质可配置开关

```hlsl
#art DETAIL_NORMAL_ENABLE "开启细节法线图" "BOOL"
#ifndef DETAIL_NORMAL_ENABLE
    #define DETAIL_NORMAL_ENABLE 0
#endif
```

`#art` 是 G66 特化宏，不是 HLSL 标准。编辑器勾选 → `#define DETAIL_NORMAL_ENABLE 1`，没勾 → 走 `#ifndef` 兜底 `0`。

nsp-intellision 的 PreprocessorView 已经把 `#art` 吃透：

- workspace 索引阶段扫出 `# art NAME "..." "BOOL"|"INT"`
- companion enum 常量（同参数块里紧邻的 `#define NAME <int>`），冲突全丢弃
- 注入到预处理状态：default zero 全局生效，companion 按 include 闭包作用域化

**shaderbase 直接复用**这套机制，不需要自造。

### 6.5 shader 入口点

shader 入口函数 `vs_main`/`ps_main`/`cs_main` 是**被 GPU pipeline 调用**的，不被项目内代码调用。

codebase-memory 把 `vs_main` 当普通函数索引，结果在死代码检测里被误报为"没人调用的死函数"——因为它的"调用者"是 GPU pipeline，不在项目代码里。

shaderbase 必须标记 `vs_main`/`ps_main`/`cs_main` 为 entry point，并标注 stage（vertex/pixel/compute）。这样：

- 死代码检测时跳过 entry point
- 能回答"这个材质有几个 VS/PS 入口"
- 能回答"这个入口属于哪个 technique"

### 6.6 technique：effect 名 + 入口绑定

每个 .nsf 文件尾部有：

```hlsl
technique TShader < string Description = "pbr_rock"; string SupportDeferredShading = "1"; >
{
    pass p0 {
        VertexShader = vs_main;
        PixelShader = ps_main;
    }
}
```

technique 块告诉引擎：

- 这个 effect 叫 `pbr_rock`（引擎按这名字引用）
- VS 入口是 `vs_main`，PS 入口是 `ps_main`
- 渲染状态（模板/混合/深度）

shaderbase 要建 `EXPOSES_TECHNIQUE` 边：File → Technique；建 `IS_ENTRY_POINT` 边：Technique → Function（带 stage 标签）。

### 6.7 材质三件套

PBR 材质走规整模式，每个材质三个配套文件：

| 文件                                 | 内容                                                                |
| ---------------------------------- | ----------------------------------------------------------------- |
| `pbr/<name>.nsf`                   | 主文件：组装 + technique                                                |
| `pbr/nodes/<name>_parameters.hlsl` | 参数文件：材质开关 `#art` + 贴图 + uniform                                   |
| `pbr/nodes/<name>_nodes.hlsl`      | 节点文件：两个钩子函数（`VertexDataNodesBasedGraph` + `PixelNodesBasedGraph`） |

shaderbase 建 Material 节点 + `BELONGS_TO_MATERIAL` 边把三个文件绑成一个逻辑单元。这样 Agent 能回答"pbr_rock 这个材质由哪三个文件组成"。

### 6.8 pipeline_source：shader 与渲染管线的桥梁

`pipeline_source/` 是 G66 自带的 Python 渲染管线 DSL，编译成 XML 给引擎。它的 `modules/ssr.py` 定义 SSR 这个 pass 用哪些 `.fx`（编译后的 shader effect）。

`Command.technique = 'common/pipeline/ssr_ray_marching.fx::TShader'` 这条字符串就是 CPU 侧引用 GPU 侧 effect 的桥梁。

**是否纳入 shaderbase 扫描范围**：决策 1.5。建议阶段 1 不扫，阶段 2 再做（Python DSL 解析复杂度另起一档）。

## 7. 预处理与跨文件解析

### 7.1 这是最难的工程问题

shader 跟普通代码最大的不同：大量 `#include`、大量 `#if/#ifdef`、大量 `#define` 宏。这些 C 预处理器特性让"看代码"变成"看代码 + 模拟预处理器"。

### 7.2 include 闭包

一个 .nsf 文件传递 `#include` 链能到达的所有文件集合，叫 include 闭包（include closure）。

```
animated_grass.nsf
  ├ #include "../shaderlib/vs_input_extend.hlsl"
  ├ #include "../shaderlib/common.hlsl"
  │   ├ #include "./const_macros.hlsl"
  │   ├ #include "./builtin_uniforms.hlsl"
  │   └ ...
  └ #include "./nodes/pbr_rock_nodes.hlsl"
```

include 闭包 = `animated_grass.nsf` + 所有间接 include 的文件。

**reverse include**：反向问"哪些文件 include 了我"。改了 `common.hlsl` 要通知所有 include 它的文件——这就是增量索引的影响范围计算基础。

nsp-intellision 的 reverse include 做得很好：`reverseIncludeByTarget` 是个内存 map，`buildReverseIncludes` 从 `filesByPath` 派生。

### 7.3 条件编译与 PreprocessorView

G66 shader 大量用条件编译：

```hlsl
#if API_PC_HIGH_QUALITY
    // 高画质路径
    CalcWorldNormal(...)
#else
    // 低画质路径
    CalcSimpleNormal(...)
#endif
```

同一份源码在不同 profile（PC/移动/低配）下编译出不同版本。shaderbase 必须能回答"在某 profile 下这行代码是不是 active"。

nsp-intellision 的 PreprocessorView 把这件事做透了：

- 按行算 `lineActive`（这行是否被编译）+ `branchSigs`（这行在哪个分支家族）
- 宏优先级链路：`#art` companion → `#art` default zero → compiler private constant → `nsf.preprocessorMacros` → compiler macro snapshot → `nsf.defines` → 源码 `#define/#undef`
- inactive 分支用隔离 probe，不污染 active 状态但保留 metadata
- `branchSignatureKey`（精确到哪一支）+ `branchFamilyKey`（同 `#if/#elif/#else` 共享）
- 6 个 `source*` 标记让宏来源可追溯

**shaderbase 直接复用**这套机制。

## 8. 索引与增量更新

### 8.1 增量索引的核心问题

shader-source 高频更新，每次改 3 个文件，不可能全工作区重建索引。必须**只重做变更影响范围**。

### 8.2 节点稳定性 + epoch 失效

**节点稳定性原则**：一个文件没变，它内部定义的所有节点（函数/结构体/uniform）就不变——不用重解析。

**epoch 失效**：每个打开的文档有个 epoch（版本号），文本一改 epoch++。缓存按 `(fingerprint, uri, epoch)` 三元组做 key，epoch 不一致即 miss。

nsp-intellision 的 SemanticSnapshot 用这套机制，比 mtime 比对更适合实时编辑（LSP 端的文本可能是未保存的 dirty buffer）。

### 8.3 shard 化磁盘 cache

nsp-intellision 的磁盘 cache 布局：

```
<workspace>/.vscode/nsp/
  index_v2_<hash>/
    manifest.json
    files/
      <fnv1a64(path) hex16>.json   ← 每个文件一个 shard
```

保存时比对 mtime+size，没变的 shard 完全不重写。比"一个超大 JSON"健壮得多。

shaderbase 直接抄这套。

### 8.4 增量流程

```
git diff → 变更文件列表
    ↓
影响范围 = 变更文件
        ∪ 直接 include 它们的文件（reverse include）
        ∪ 调用它们的函数所在文件（CALLS 边反向）
    ↓
对影响范围内的文件重解析、重建节点和边
    ↓
SQLite 局部 UPDATE/DELETE/INSERT
    ↓
未在影响范围内的文件，保留旧节点和边
```

### 8.5 触发方式

nsp-intellision 用 VS Code 推送的 `workspace/didChangeWatchedFiles`，被动接收，server 端零 fs watchdog。

shaderbase 独立跑（无 LSP 客户端），拿不到这种推送。**手动触发**是已确认的方案：

- 提供"重建索引"命令（全量）
- 提供"增量更新"命令（基于 git diff 或文件 mtime 比对）

### 8.6 反向依赖图：失效传染

改了被广泛 include 的头文件（比如 `shaderlib/common.hlsl`），所有 includer 的派生语义（SemanticSnapshot）都要失效。

nsp-intellision 的 `rootUrisByDependencyUri`（semantic_cache.cpp L62-90）做这个反向失效：A 文件改了，所有把 A 纳入 include 闭包的 root 的 snapshot 全部失效。

shaderbase 必须有类似的反向依赖图，否则改一个核心头会让所有下游 snapshot 漂移。

## 9. 查询能力：Agent 能问什么

### 9.1 必备查询（符号级）

| 查询                  | 说明                       |
| ------------------- | ------------------------ |
| `search_shader`     | 按名字正则 / 类型 / 文件过滤找节点     |
| `get_definition`    | 某符号的定义位置                 |
| `get_references`    | 某符号被引用的所有位置              |
| `get_code_snippet`  | 读某函数的源码                  |
| `get_struct_fields` | 某结构体的字段列表                |
| `get_uniform_info`  | 某 uniform 的类型/默认值/UI 元信息 |

### 9.2 图遍历查询（关系级，shaderbase 的核心差异化）

| 查询                    | 说明                                     | 需要的边                            |
| --------------------- | -------------------------------------- | ------------------------------- |
| `trace_calls`         | 谁调用 X / X 调用了谁，BFS depth=N             | CALLS                           |
| `detect_changes`      | git diff → 影响符号 + 爆炸半径                 | CALLS                           |
| `find_dead_code`      | 没人调用的函数（排除 entry point）                | CALLS                           |
| `trace_stage_flow`    | VS 输出 TEXCOORD2 → 哪些 PS 输入             | FLOWS_TO                        |
| `find_uniform_usage`  | u_roughness 在哪些函数里被使用                  | DECLARES_UNIFORM + USES_UNIFORM |
| `get_material_files`  | pbr_rock 材质的三件套文件                      | BELONGS_TO_MATERIAL             |
| `find_entry_points`   | 找所有 vs_main/ps_main 入口 + stage         | IS_ENTRY_POINT                  |
| `find_technique_refs` | technique TShader 被哪些 pipeline pass 引用 | EXPOSES_TECHNIQUE + 跨语言         |

### 9.3 架构级查询

| 查询                       | 说明                          |
| ------------------------ | --------------------------- |
| `get_architecture`       | 仓库整体架构：模块/热点/边界/聚类          |
| `get_hotspots`           | fan_in 最高的函数                |
| `find_similar_shaders`   | 近克隆 shader（MinHash LSH）     |
| `get_materials_overview` | 所有材质列表 + 每个材质的入口/贴图/uniform |

### 9.4 codebase-memory 的工具对照

| codebase-memory 工具     | shaderbase 对应              |
| ---------------------- | -------------------------- |
| `index_repository`     | `index_shader_source`      |
| `search_graph`         | `search_shader`            |
| `trace_path`           | `trace_calls`              |
| `detect_changes`       | `detect_changes`（基于 CALLS） |
| `query_graph`（Cypher）  | `query_sqlite`（直接 SQL）     |
| `get_code_snippet`     | `get_code_snippet`         |
| `get_architecture`     | `get_architecture`         |
| `search_code`          | `search_code`（grep 兜底）     |
| `list_projects`        | `list_projects`            |
| `delete_project`       | `delete_project`           |
| `index_status`         | `index_status`             |
| `manage_adr`           | 不做（shader 不需要 ADR）         |
| `ingest_traces`        | 不做（运行时 trace 不在范围）         |
| `check_index_coverage` | `check_coverage`           |

## 10. 完整工作分解（按层次）

### 10.1 层次 1：发现 + 解析（基础）

| 工作     | 实现选项                                                   | 建议                         |
| ------ | ------------------------------------------------------ | -------------------------- |
| 文件发现   | Python `os.walk` + `.cbmignore`/`.gitignore` 解析        | 自造，简单                      |
| 后缀映射   | `.nsf → hlsl`、`.fxh → hlsl`、`.glsl → glsl`             | 配置文件，复用 codebase-memory 思路 |
| 语法解析   | tree-sitter-hlsl + 行级扫描补 `#art`/`technique`/annotation | 混合方案                       |
| 预处理状态机 | **直接复用 nsp-intellision PreprocessorView**（或重写借鉴思路）     | 复用                         |

### 10.2 层次 2：节点抽取

| 节点         | 抽取方式                                              | 借鉴                        |
| ---------- | ------------------------------------------------- | ------------------------- |
| Function   | tree-sitter `function_definition` 节点              | codebase-memory + nsp     |
| Struct     | tree-sitter `class_specifier`/`struct_specifier`  | codebase-memory + nsp     |
| Field      | struct body 内 `declaration`                       | nsp `IndexedStructMember` |
| Uniform    | 顶层 `declaration` + 识别 `float/int/float4` 等类型      | nsp（kind=8）               |
| Texture    | `texture` 关键字声明                                   | 自造                        |
| CBuffer    | `cbuffer NAME : register(bN) { ... }`             | nsp（kind=23）              |
| Macro      | `#define NAME value`                              | nsp（kind=14）              |
| Material   | 三件套模式识别                                           | **自造**（核心差异化）             |
| Technique  | `technique TShader <...> { pass p0 {...} }`       | **自造**                    |
| EntryPoint | `vs_main`/`ps_main`/`cs_main` 函数 + technique 入口绑定 | **自造**                    |

### 10.3 层次 3：边抽取

| 边                                   | 抽取方式                                                       | 难度               |
| ----------------------------------- | ---------------------------------------------------------- | ---------------- |
| `INCLUDES`                          | 解析 `#include` 路径                                           | 易                |
| `DEFINES`                           | File → Function/Struct                                     | 易                |
| `CALLS`                             | 函数体内 `call_expression` 节点 + 跨文件 resolve（include 闭包 + 重载消歧） | **难**（核心）        |
| `REFERENCES`                        | 所有 identifier token + 位置                                   | 中                |
| `FLOWS_TO`                          | VS 输出语义 → PS 输入语义同名匹配                                      | **中**（shader 专有） |
| `DECLARES_UNIFORM` / `USES_UNIFORM` | uniform 声明 + 函数内引用                                         | 中                |
| `HAS_MEMBER`                        | struct body → field                                        | 易                |
| `BELONGS_TO_MATERIAL`               | 文件名模式 `pbr/<name>.nsf` + `nodes/<name>_*.hlsl`             | 易                |
| `IS_ENTRY_POINT`                    | 函数名 `vs_main`/`ps_main`/`cs_main` + technique 入口           | 易                |
| `EXPOSES_TECHNIQUE`                 | 解析 `technique` 块                                           | 易                |
| `CONDITIONAL_ON`                    | PreprocessorView 的 branchSignatureKey                      | 复用 nsp           |

### 10.4 层次 4：图存储

| 选项                              | 优点         | 缺点                    | 建议     |
| ------------------------------- | ---------- | --------------------- | ------ |
| SQLite + 标准库                    | 成熟、单文件、好部署 | 1.7 万节点规模标准 INSERT 够用 | **推荐** |
| SQLite + 自写页（仿 codebase-memory） | 极快         | 工程量大，不值得              | 不做     |
| networkx 内存图                    | Python 友好  | 持久化麻烦                 | 不做     |
| neo4j                           | 原生图数据库     | 部署重                   | 不做     |

**推荐 schema**：

```sql
-- 节点表
CREATE TABLE nodes (
  id INTEGER PRIMARY KEY,
  kind TEXT,           -- Function/Struct/Uniform/...
  name TEXT,
  qualified_name TEXT,  -- 全限定名
  file_path TEXT,
  line INTEGER,
  properties JSON      -- 类型/默认值/UI 元信息等
);

-- 边表
CREATE TABLE edges (
  id INTEGER PRIMARY KEY,
  kind TEXT,           -- CALLS/INCLUDES/FLOWS_TO/...
  source_id INTEGER,
  target_id INTEGER,
  properties JSON     -- 调用位置/条件分支签名等
);

-- 索引
CREATE INDEX idx_nodes_name ON nodes(name);
CREATE INDEX idx_nodes_qname ON nodes(qualified_name);
CREATE INDEX idx_nodes_kind ON nodes(kind);
CREATE INDEX idx_edges_kind ON edges(kind);
CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
```

### 10.5 层次 5：查询引擎

| 查询类型           | 实现                                            |
| -------------- | --------------------------------------------- |
| 符号查询           | SQL `SELECT FROM nodes WHERE name LIKE ...`   |
| 图遍历            | SQL 递归 CTE（`WITH RECURSIVE`）或 Python networkx |
| 调用链 trace_path | BFS 沿 `CALLS` 边走                              |
| 影响爆炸半径         | git diff 变更文件 → 找变更符号 → 沿 `CALLS` 反向走         |
| 死代码            | `CALLS` in-degree = 0 且不是 entry point         |

### 10.6 层次 6：增量更新

| 工作                                             | 借鉴                                          |
| ---------------------------------------------- | ------------------------------------------- |
| 文件 mtime/size 比对（dirty 校验）                     | nsp-intellision `buildAll` cache validate   |
| shard 化磁盘 cache（每文件一 JSON）                     | nsp-intellision `workspace_index_cache.cpp` |
| epoch 失效                                       | nsp-intellision SemanticSnapshot            |
| reverse include 影响范围                           | nsp-intellision `reverseIncludeByTarget`    |
| 反向依赖图（语义 snapshot 失效）                          | nsp-intellision `rootUrisByDependencyUri`   |
| 手动触发命令（`rebuild_index` / `incremental_update`） | 自造                                          |

### 10.7 层次 7：MCP server

| 工作             | 实现                                           |
| -------------- | -------------------------------------------- |
| MCP Python SDK | 官方 `mcp` 包                                   |
| tool 注册        | `@mcp.tool()` 装饰器                            |
| stdio 传输       | MCP 默认                                       |
| 配置             | `~/.zcode/cli/config.json` 加 `shaderbase` 条目 |

## 11. 阶段划分

### 阶段 1（MVP，1-2 周）

**目标**：能索引、能查函数定位、能查调用关系

**范围**：

- 文件发现 + .nsf/.hlsl 后缀映射
- tree-sitter-hlsl 解析
- 抽 Function / Struct / Field / Uniform / Macro / CBuffer 节点
- 抽 INCLUDES / DEFINES / CALLS / REFERENCES 边
- SQLite 存储
- MCP 工具：`search_shader` / `trace_calls` / `get_code_snippet` / `get_definition` / `get_references`

**完成标志**：能回答"CalcWorldNormal 在哪定义、被谁调用"

### 阶段 2（shader 语义，2-4 周）

**目标**：建 shader 特有的关系边

**范围**：

- EntryPoint + stage 标记
- Technique + `EXPOSES_TECHNIQUE` 边
- Material 三件套 + `BELONGS_TO_MATERIAL` 边
- FLOWS_TO 边（VS→PS 语义绑定）
- DECLARES_UNIFORM + USES_UNIFORM 边
- uniform L2 元信息（annotation）
- 复用 PreprocessorView（`#art`/`#if`/include guard/分支家族）

**完成标志**：能回答"改了 SampleColorTextureBias 影响哪些 effect"、"VS 输出 TEXCOORD2 流到哪些 PS 输入"

### 阶段 3（增量索引，1-2 周）

**目标**：跟上高频更新

**范围**：

- shard 化磁盘 cache（仿 nsp-intellision）
- 手动触发 `rebuild_index` / `incremental_update` 命令
- reverse include 影响范围计算
- 反向依赖图失效传染
- `CONDITIONAL_ON` 边（条件分支标记）

**完成标志**：改 3 个文件后跑 `incremental_update`，几秒内完成，图状态正确

### 阶段 4（高级，可选）

**目标**：增强查询能力

**范围**：

- 相似 shader 检测（MinHash LSH，仿 codebase-memory `SIMILAR_TO` 边）
- 死代码检测（CALLS in-degree=0 + entry point 排除）
- 影响爆炸半径（detect_changes）
- 架构分析（聚类/热点/模块边界）
- pipeline_source Python DSL 扫描（technique → pipeline pass 引用）

## 12. 技术选型汇总

| 组件     | 选型                                      | 理由                                                            |
| ------ | --------------------------------------- | ------------------------------------------------------------- |
| 实现语言   | **Python**                              | AI 协作效率高；1.7 万节点 Python 够快；tree-sitter/sqlite3/MCP SDK 都有成熟绑定 |
| 语法解析   | **tree-sitter-hlsl + 行级扫描**             | 标准 HLSL 用 tree-sitter，G66 特化用行级扫描补                            |
| 预处理状态机 | **复用 nsp-intellision PreprocessorView** | 自造要几周，复用零成本                                                   |
| 图存储    | **SQLite（标准库）**                         | 1.7 万节点规模够，部署简单                                               |
| 查询接口   | **MCP Python SDK**                      | 接 ZCode/Claude Code                                           |
| 图算法    | **networkx**（按需）                        | BFS 遍历、社区检测都有现成                                               |
| 配置     | **TOML**                                | 项目配置 + ignore 规则                                              |
| 增量触发   | **手动命令**                                | 已确认，不做 hook/watcher                                           |

## 13. 关键决策点（需用户确认）

| 决策                    | 选项                                                                              | 影响                               |
| --------------------- | ------------------------------------------------------------------------------- | -------------------------------- |
| 1. 预处理状态机             | A. 复用 nsp-intellision PreprocessorView（要协商加自定义 method 接口） / B. 借鉴思路自造           | A 省 2-3 周，但要联系同事                 |
| 2. call_query 复用      | A. 让 nsp-intellision 加 `nsf/queryAllCallsites` 接口返回全工作区调用点 / B. shaderbase 自己解析 | A 省很多工作，但要联系同事                   |
| 3. pipeline_source 扫描 | A. 阶段 1 不扫，阶段 2 再做 / B. 阶段 1 就扫                                                 | B 工作量翻倍                          |
| 4. 是否做相似 shader 检测    | A. 阶段 4 做 / B. 不做                                                               | A 借鉴 codebase-memory MinHash LSH |
| 5. 是否做死代码检测           | A. 阶段 4 做 / B. 不做                                                               | A 需要 entry point 标记              |
| 6. 数据库                | A. SQLite / B. 别的                                                               | A 推荐                             |

## 14. 不做的事（明确排除）

| 不做                 | 理由                         |
| ------------------ | -------------------------- |
| L3 uniform 运行时赋值追踪 | 不在 shader-source 仓库内       |
| 3D 图可视化            | 锦上添花，非核心                   |
| Cypher 查询引擎        | 直接用 SQL                    |
| 自写 SQLite 页        | 规模小不值得                     |
| 158 种语言            | 只要 GLSL/HLSL               |
| 43 个 agent 客户端适配   | 只对接一个                      |
| 内嵌 LLM             | 让 Agent 当翻译器               |
| Hybrid LSP 类型推断    | shader 类型系统比通用语言简单，阶段 1 不做 |

## 15. 路径选择总览

### 路 A：完全自造 shaderbase

- 从零用 Python + tree-sitter + SQLite + MCP
- 不依赖 nsp-intellision 任何代码
- 工程量：阶段 1+2+3 约 2-3 人月

### 路 B：完全用 nsp-intellision 包壳

- 给 nsp-intellision LSP server 包 MCP 壳
- 不自造图
- 工程量：1-2 人周
- **局限**：Agent 没有"调用图遍历"能力，trace_path/detect_changes 全干不了

### 路 C（推荐）：分层用

- 底层用 nsp-intellision 的成熟部分（PreprocessorView/workspace_index/resources/server_parse）
- 上层自造图模型（CALLS/FLOWS_TO/uniform 关联/材质单元）
- 自造 MCP server 暴露给 Agent
- 工程量：阶段 1+2+3 约 1-2 人月
- **前提**：能联系到 nsp-intellision 同事，协商加自定义 method（比如 `nsf/queryAllCallsites`）

## 16. 待用户确认的问题

在进 plan mode 写详细方案前，必须确认：

1. **能联系到写 nsp-intellision 的同事吗？** 决定走路 A 还是路 C。
2. **Agent 真的需要"调用图"吗？** 如果主要查"X 在哪定义/怎么用"，路 B 够；如果真要 trace_path/detect_changes，必须图。
3. **公司愿意投入多少人力？** 路 C 1-2 人月可行；路 B 1-2 人周；路 A 2-3 人月。
4. **`pipeline_source` Python DSL 扫不扫？** 阶段 1 不扫 vs 阶段 2 扫。
5. **相似 shader 检测做不做？** 借鉴 codebase-memory MinHash LSH。
6. **死代码检测做不做？** 需要 entry point 标记。
7. **目标 Agent 是哪个？** ZCode / Claude Code / 公司自研？影响 MCP 接口具体细节。

---

**文档版本**：v1.0
**生成日期**：2026-07-21
**基于**：codebase-memory-mcp v0.9.0 + nsp-intellision v1.0.2 源码深度调研
