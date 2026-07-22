# Shaderbase 开发方案 v1.0

> 本文档基于前期对 codebase-memory-mcp、nsp-intellision、shader-source 三个项目的深度调研，结合用户决策（自研 tree-sitter grammar + Python 重写 PreprocessorView），落地 shaderbase 阶段 1 的初步开发方案。
> 
> 配套文档：`SHADERBASE_DESIGN.md`（整体设计拆解）

---

## 0. 决策快照

| 决策项                | 选择                                                  |
| ------------------ | --------------------------------------------------- |
| 主语言                | Python                                              |
| Parser 框架          | tree-sitter（自研 grammar.js）                          |
| 预处理                | Python 重写 PreprocessorView（借鉴 nsp-intellision 算法思路） |
| 图存储                | SQLite（标准库，不自写页写入器）                                 |
| 接口                 | MCP（Python SDK）                                     |
| 增量触发               | 手动命令                                                |
| 复用 nsp-intellision | 借鉴算法思路 + 直接拷贝 resources JSON 数据，不依赖 C++ 代码          |
| Agent 目标           | ZCode/Claude Code（MCP 兼容）                           |

---

## 1. Grammar 测试流程设计

### 1.1 设计原则

Grammar 的核心矛盾：**G66 shader 是公司私有特化语法，没完整文档，必须用大量样例驱动迭代**。AI 辅助能显著加速写 grammar 规则和写测试，但**测试流程本身要工程化**，否则 AI 写出来的 grammar 跟实际 shader 不匹配，跑通也不可信。

测试流程设计原则：

1. **量化优先**——每个迭代有可测的数字指标（解析率/ERROR 率/抽取率），不能只靠肉眼判断
2. **真实样例驱动**——直接用 shader-source 仓库的真实 .nsf/.hlsl 文件当 fixture，不造数据
3. **分层覆盖**——从简单到复杂分层，先单语法后综合，每层有独立测试
4. **回归自动化**——`tree-sitter test` 一键跑全部 corpus，CI 守住不退化
5. **AI 辅助点明确**——AI 能帮写规则、能帮生成样例、能帮分析 ERROR，但不能替你判断"这个 AST 对不对"

### 1.2 仓库结构

```
g66-shader-grammar/                    ← 自研 grammar 独立项目
├── grammar.js                         ← 主 grammar 文件（手写 + AI 辅助）
├── package.json
├── binding.gyp
├── src/
│   ├── parser.c                       ← tree-sitter generate 自动生成
│   ├── parser.h
│   └── tree_sitter/
│       └── parser_tables.c
├── py-binding/
│   └── tree_sitter_g66_shader/
│       ├── __init__.py                ← 暴露 language() 函数
│       └── binding.c                  ← pybind11/cython 桥接
├── test/
│   ├── corpus/                        ← 回归测试 corpus（人工期望）
│   │   ├── 01_basic/                 ← 基础语法
│   │   │   ├── function.txt
│   │   │   ├── variable.txt
│   │   │   └── struct.txt
│   │   ├── 02_preproc/                ← 预处理指令
│   │   │   ├── include.txt
│   │   │   ├── define.txt
│   │   │   ├── ifdef.txt
│   │   │   └── art.txt
│   │   ├── 03_annotation/             ← annotation 块
│   │   │   ├── variable_annotation.txt
│   │   │   └── texture_annotation.txt
│   │   ├── 04_technique/              ← technique/pass
│   │   │   ├── basic.txt
│   │   │   └── pass_state.txt
│   │   ├── 05_semantic/               ← 语义绑定
│   │   │   ├── field_semantic.txt
│   │   │   └── function_semantic.txt
│   │   ├── 06_expression/             ← 表达式层
│   │   │   ├── call.txt
│   │   │   ├── member_access.txt
│   │   │   └── binary_op.txt
│   │   └── 07_cbuffer/                ← cbuffer
│   │       └── basic.txt
│   ├── fixtures/                      ← 真实 shader 片段（从 shader-source 拷贝/截取）
│   │   ├── minimal/
│   │   │   ├── pbr_default.nsf
│   │   │   ├── dissolve_ui.nsf
│   │   │   └── common.hlsl
│   │   └── full/
│   │       ├── ui/                    ← ui/ 全套 .nsf
│   │       ├── pbr/                   ← pbr/ 抽样
│   │       └── shaderlib/              ← shaderlib/ 抽样
│   └── snapshots/                     ← AST 快照（参考用，不强制全跑）
│       └── ...
├── scripts/
│   ├── coverage.py                    ← 量化覆盖率脚本（见 1.4）
│   ├── corpus_runner.py               ← 跑 corpus + 比对
│   ├── fixtures_runner.py             ← 跑 fixtures + 统计 ERROR
│   └── export_ast.py                  ← 把某文件的 AST 导出成可视化文本
└── docs/
    ├── grammar_rules.md               ← 所有规则的说明文档
    └── known_issues.md                ← 已知语法不覆盖的清单
```

### 1.3 测试分层

按语法结构分层，每层独立测试，**前层不通就不做后层**：

| 层             | 内容                               | 通过标准                                                         |
| ------------- | -------------------------------- | ------------------------------------------------------------ |
| 01_basic      | function/variable/struct 声明      | 所有样例解析无 ERROR，函数名/类型/字段抽取 100%                               |
| 02_preproc    | #include/#define/#ifdef/#art     | 所有指令正确分类，#art 节点字段抽取 100%                                    |
| 03_annotation | 变量/texture 的 `<>` annotation     | annotation 内 key=value 抽取 100%，跟模板参数 `Texture2D<float4>` 不混淆 |
| 04_technique  | technique/pass/state_assignment  | pass body 内 `VertexShader = vs_main` 抽取 100%                 |
| 05_semantic   | `: TEXCOORD0` 字段语义绑定             | 所有 semantic 抽取 100%，跨字段类型不串                                  |
| 06_expression | call_expression/member/binary_op | call_expression 抽取率 > 95%，member_expression 抽取率 > 95%        |
| 07_cbuffer    | cbuffer 块和 register 绑定           | cbuffer 名字和字段抽取 100%                                         |

### 1.4 量化指标脚本

`scripts/coverage.py` 跑整个 fixture 目录，统计：

```python
# 输出示例
==== Fixture coverage report ====
total_files:                1298
parsed_ok:                   1290   (99.4%)
crashed:                       8   (0.6%) ← 这些要修 grammar

total_nodes:               17227
error_nodes:                 142   (0.8%) ← 哪些节点没识别

function_definition:        7310   ← 抽出的函数数
call_expression:            8420   ← 抽出的调用数
struct_specifier:            1504
cbuffer_declaration:          45
preproc_art_directive:       2590
technique_block:              824
field_declaration:           3035

==== Per-file failure analysis ====
Top 10 files by ERROR node count:
  ui/floor_board_ui.nsf: 12 ERROR nodes (line 2342, ...)
  base/animated_grass.nsf: 8 ERROR nodes (line 156, ...)
  ...
```

每跑一次 grammar 改动，都跑这个脚本看数字变化，**数字改善才合并改动**。

### 1.5 Corpus 文件格式

`test/corpus/01_basic/function.txt` 的标准格式：

```
==========
函数定义 - 基础
==========
float4 ps_main(PS_INPUT input) : COLOR {
    return float4(1, 0, 0, 1);
}
---
(source_file
  (declaration
    (function_definition
      type: (type)
      name: (function_name)
      parameters: (parameter_list
        (parameter
          type: (type)
          name: (identifier)))
      semantic: (semantic_binding)
      body: (body
        (statement
          (return_statement
            (expression
              (call_expression
                callee: (type)
                args: (argument_list
                  (expression (number))
                  (expression (number))
                  (expression (number))
                  (expression (number)))))))))))
```

- 上半部分 `========` 后是测试名
- 中间是输入源码
- `---` 后是期望的 AST 文本

`npx tree-sitter test` 自动跑全部 corpus，diff 比对实际输出和期望。期望变了要人工 review，避免 grammar 静默退化。

### 1.6 迭代节奏

每轮迭代闭环：

1. **跑 `npx tree-sitter test`** 看哪些 corpus 失败 → 知道哪些规则坏了
2. **跑 `python scripts/coverage.py test/fixtures/full/`** 看真实 shader 解析率 → 知道跟实际 shader 差多远
3. **挑 ERROR 最多的文件** → AI 辅助分析 ERROR 节点上下文，判断是什么语法没覆盖
4. **改 grammar.js 加规则或修歧义** → AI 辅助写规则
5. **加 corpus 样例覆盖新语法** → AI 帮生成期望 AST
6. **重新跑 1-2** → 看数字改善没，没改善回退

每轮迭代 2-4 小时。预计 15-25 轮迭代收敛（AI 辅助能压缩到 5-7 个工作日）。

### 1.7 AI 辅助的具体用法（修订版）

**核心思路**：预先用 fixture 定义"什么是对的"，AI 只做"实际输出 vs 期望 fixture"的机械对照，不做主观判断。AI 带着写 grammar 时的偏见做主观判断不可靠；但跟外部 review 过的 fixture 比对，是机器干的事，不带偏见。

#### 1.7.1 工作分工（修正版）

| AI 帮做                 | AI 比对 fixture 后能自动判定            | 仍需用户拍板                                                     |
| --------------------- | ------------------------------- | ---------------------------------------------------------- |
| 写 grammar 规则的初始版本     | ✅ 实际 AST 跟 corpus 期望一致 → PASS   | 加什么规则、什么字段（工程决策，AI 不知道下游要建什么边）                             |
| 给一段源码生成候选期望 AST       | ✅ 实际节点跟 expected.yaml 一致 → PASS | 歧义怎么解（`<>` 模板 vs annotation 的 lexical precedence，影响下游抽取逻辑） |
| 分析 ERROR 节点的可能原因      | ✅ 解析率/抽取率数字改善 → 进步              | 新语法节点设计（比如 `technique_block` 该不该包外层 `declaration`）         |
| 跑 coverage.py + 报告数字  | ✅ 数字恶化 → 自动拒绝合并改动               | AST 深度选择（要不要深到表达式层）                                        |
| 跑 corpus 对照 + diff 报告 |                                 | 候选 fixture 入库前 review（避免把 AI 自己的偏见固化成"标准"）                 |

**关键原则**：

1. **fixture 是不可变的标准**——一旦入库，AI 后续只做对照，不能改
2. **AI 生成的候选 fixture 必须用户 review 入库**——避免 AI 把自己的偏见固化成"标准"（自我认证问题）
3. **简单语法（function/struct/call）AI 比对通过直接合并**；**复杂决策（歧义处理/节点设计）AI 给建议 + 用户 review**
4. **数字改善才合并**——AI 跑 coverage.py 报告，解析率/抽取率没改善回退改动

#### 1.7.2 fixture 分层标准（关键设计）

光 AST 层 corpus 不够，shaderbase 最终要建节点和边，每层都要有"对的标准"。

**层 1：AST 结构标准（corpus 覆盖）**

见 §1.5 corpus 文件格式。`npx tree-sitter test` 自动比对。AI 维护：拿 shader 片段生成候选期望 AST，用户 review 入库。

**层 2：节点抽取标准（expected.yaml）**

新增 `test/fixtures/nodes/<file>.expected.yaml` 格式：

```yaml
# test/fixtures/nodes/pbr_default.expected.yaml
input: pbr/pbr_default.nsf
expected_nodes:
  - kind: Function
    name: vs_main
    file: pbr/pbr_default.nsf
    line: 30
  - kind: Function
    name: ps_main
    file: pbr/pbr_default.nsf
    line: 35
  - kind: Struct
    name: VS_INPUT
    fields: [position, normal, texcoord0]
  - kind: Uniform
    name: u_roughness
    type: float
    default: 0.5
    annotation:
      SasUiLabel: "粗糙度"
      SasUiMin: 0
      SasUiMax: 1
```

shaderbase 跑抽取器 → 拿实际节点 → 跟 yaml 比对 → 报差异。AI 只对照不判断。

**层 3：边抽取标准（最关键，新增）**

`test/fixtures/edges/<file>.expected.yaml`：

```yaml
# test/fixtures/edges/pbr_default_calls.expected.yaml
input: pbr/pbr_default.nsf
expected_edges:
  - kind: CALLS
    source: pbr_default.PixelNodesBasedGraph
    target: shaderlib.surface_functions_shared.CalcWorldNormal
    line: 142
  - kind: CALLS
    source: pbr_default.PixelNodesBasedGraph
    target: shaderlib.texture.SampleColorTextureBias
    line: 80
  - kind: FLOWS_TO
    source: shaderlib.vs_input_extend.position  # VS 输出字段
    target: shaderlib.ps_input_extend.texcoord0 # PS 输入字段
    semantic: TEXCOORD0
  - kind: USES_UNIFORM
    source: pbr_default.PixelNodesBasedGraph
    target: builtin_uniforms.u_frame_time
    line: 95
  - kind: IS_ENTRY_POINT
    source: pbr_default.vs_main
    target: pbr_default.TShader
    stage: vertex
```

这个最难写（CALLS 边涉及跨文件 resolve），但写出来后每次改 resolve 算法都跑这套 fixture，回归守住。

**层 4：跨文件解析标准**

`test/fixtures/resolve/<file>.expected.yaml`：

```yaml
# test/fixtures/resolve/pbr_rock.expected.yaml
input: pbr/pbr_rock.nsf
expected_include_closure:
  - shaderlib/common.hlsl
  - shaderlib/vs_input_extend.hlsl
  - pbr/nodes/pbr_rock_parameters.hlsl
  - pbr/nodes/pbr_rock_nodes.hlsl
expected_resolved_calls:
  # pbr_rock 调 CalcWorldNormal，应该 resolve 到 shaderlib/surface_functions_shared.hlsl:130
  - caller: pbr_rock.PixelNodesBasedGraph
    callee: CalcWorldNormal
    resolved_to: shaderlib/surface_functions_shared.hlsl:130
```

#### 1.7.3 fixture 规模

按 80/20 法则：

- **AST 层 corpus**：7 个语法分类，每类 5-10 个样例，约 50 个 corpus
- **节点层 expected.yaml**：10-20 个代表性 shader 文件
- **边层 expected.yaml**：5-10 个 shader 文件（CALLS/FLOWS_TO/USES_UNIFORM 各覆盖）
- **跨文件层 expected.yaml**：3-5 个 include 链样例

**总共约 70-100 个 fixture**。AI 帮生成候选 + 标注每条"为什么期望是这个"，用户 review 入库——一周能搞定。后续 grammar/抽取器改动，跑这套 fixture 几秒出结果，AI 报 diff 不做主观判断。

#### 1.7.4 fixture 维护规则

- **入库不可变**：一旦 fixture 入库，期望字段不能为迎合 grammar 改动而改。grammar 必须迁就 fixture，不是反过来。
- **例外：grammar 演进允许改期望**：当 grammar 节点设计确实要演进（比如 `technique_block` 从外层 `declaration` 包装改成独立节点），AI 必须标注"这是结构演进不是退化"，用户 review 后才能改期望。
- **新增 fixture 容易，删 fixture 要审批**：删 fixture 等于降低标准，必须用户确认。

#### 1.7.5 AI 在 fixture 流程里的真实角色

```
[选 shader 片段] → AI 选代表性片段（按 ERROR 多/调用多/特化语法多挑）
        ↓
[跑当前 grammar/抽取器] → AI 拿实际输出
        ↓
[AI 把实际输出当候选期望] → AI 标注每条"为什么期望是这个"
        ↓
[用户 review] ← 你看 AI 标注，对的入库当 fixture
        ↓
[入库后不可变] → 后续 grammar 改动跑这套 fixture
        ↓
[AI 比对实际 vs 期望] → AI 只报 diff 不做主观判断
        ↓
[数字改善 + 抽样 review] → 合并改动
```

这套流程下，AI 不需要"判断 AST 对不对"——它只需要做机器最擅长的事：机械比对。判断对错的责任前移到了 fixture 入库时的人工 review，那时候你看着 shader 源码 + 实际 AST + AI 标注，是真正的"判断"，不是 AI 自我认证。

### 1.8 不做的测试

- **不做语法校验测试**——grammar 不报错不等于代码对，shader 校验交给引擎侧 shadercompiler
- **不做完整 AST 快照回归**——AST 结构会随 grammar 演进变，快照太脆；用 §1.7.2 的分层 fixture 替代
- **不做表达式求值测试**——grammar 只做语法识别，求值在 PreprocessorView
- **不做跨文件 include 闭包测试**（grammar 层）——include 解析在 shaderbase 主项目测，grammar 只测单文件；但 shaderbase 主项目有 §1.7.2 层 4 的跨文件 fixture

---

## 2. PreprocessorView Python 转译方案

### 2.1 转译原则（v2 修订：砍 IDE 专属，加查询时算 active）

**核心决策变更**（基于对 nsp 调研的修正）：

- **不做** nsp 的 IDE 专属部分：
  - 不做 expanded_source（把 inactive 行挖空给下游解析器）——shaderbase 不是 IDE，不需要实时诊断
  - 不做 active 视图给诊断用——没有实时诊断需求
  - 不做 unit_macro_profile_provider / gimlocalvariants.json 解析——shaderbase 不接 shadercompiler profile
- **照搬** nsp 的全分支索引模式：
  - 索引时用空 defines 建 PreprocessorView，给所有节点/边算 `branch_signature` + `branch_family`
  - 所有分支的节点都存，带签名——**信息不少一分**
- **新增** shaderbase 专属的查询时算 active：
  - Agent 查询时传宏配置 `macros={"USE_SEASON_ID": 1, "QUALITY_HIGH": 0}`
  - shaderbase 用 PreprocessorView 算这组配置下每条边 active 与否
  - 返回结果带 `[active=true/false, conditional_sig="#if USE_SEASON_ID:branch0"]`

**转译原则**：
- 算法思路完全借鉴 nsp，代码用 Python 重写
- 数据结构对齐 Python 习惯（list/dict/dataclass，不用 arena/指针）
- 输入契约吃 tree-sitter AST 的 `preproc_if` 节点树（不引 nsp 的 ConditionalAst 中间表示）
- 不依赖 C++ 编译产物——shaderbase 是纯 Python 项目

### 2.2 双视图架构（照搬 nsp 的双视图模式）

```
┌─────────────────────────────────────────────────────────┐
│  索引阶段（offline，跑一次 + 增量更新）                    │
│                                                          │
│  对每个文件用空 defines 建全分支视图：                      │
│    build_preprocessor_view(ast, defines={})              │
│  输出：                                                   │
│    - branch_sigs[line]  ← 每行的条件分支签名              │
│    - branch_family[node] ← 节点的分支家族键               │
│  写入 SQLite:                                            │
│    nodes.conditional_signature                          │
│    edges.conditional_signature                          │
│  不算 active——所有分支的节点都存，带签名                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  查询阶段（online，Agent 每次查询时）                      │
│                                                          │
│  Agent 传宏配置：                                         │
│    trace_calls("FuncX", macros={"USE_X":1,"QUALITY":0})  │
│  shaderbase 用 PreprocessorView 重算 active：             │
│    build_preprocessor_view(ast, defines=macros)          │
│    → line_active[]                                       │
│  按 line_active 过滤边：                                 │
│    edge 在 active 行 → 返回，标 [active=true]            │
│    edge 在 inactive 行 → 返回，标 [active=false, sig=...]│
│  Agent 拿到带 active 标记的结果                           │
└─────────────────────────────────────────────────────────┘
```

### 2.3 输入契约

```python
# tree-sitter AST 里的 #if 节点（grammar 已解析为 preproc_if/preproc_elif/preproc_else/preproc_endif）

# Python 版 PreprocessorView 入口（双视图共用同一函数，靠 defines 区分）
def build_preprocessor_view(
    root_node: tree_sitter.Node,       # tree-sitter AST 根节点
    source_text: bytes,                # 源码
    defines: dict[str, int],           # 索引阶段传 {}；查询阶段传 Agent 的 macros
    art_macros: list[ArtMacro],        # workspace 抽出的 #art 宏（默认 0 注入）
    configured_macros: dict[str, str], # 用户配置的宏替换（可选，索引阶段不用）
) -> PreprocessorView:
    ...
```

**契约差异**：
- nsp 走两步：先建 `ConditionalAst` 树，再解释
- shaderbase 走一步：直接遍历 tree-sitter AST，状态机推进
- nsp 索引用空 defines、查询用活动单元 effectiveDefines
- shaderbase 索引用空 defines、查询用 Agent 传入的 macros——**架构一致，输入源不同**

### 2.4 数据结构对照

| nsp C++ 版                                                               | shaderbase Python 版                                           |
| ----------------------------------------------------------------------- | ------------------------------------------------------------- |
| `PreprocessorView.lineActive: vector<char>`                             | `line_active: list[bool]`（查询阶段才算，索引阶段不算）            |
| `PreprocessorView.branchSigs: vector<PreprocBranchSig>`                 | `branch_sigs: list[list[tuple[int, int]]]`（索引+查询都算）     |
| `PreprocessorView.conditionDiagnostics: vector<...>`                    | `condition_diagnostics: list[ConditionDiagnostic]`（精简，只做未定义宏） |
| `PreprocessorView.macroEvents: vector<PreprocessorMacroEvent>`          | `macro_events: list[MacroEvent]`（索引阶段存，给宏来源追溯用）     |
| `PreprocessorView.initialMacroReplacements: unordered_map<string, ...>` | `initial_macros: dict[str, MacroReplacement]`                 |
| `PreprocessorView.branchMerges`                                         | `branch_merges: list[BranchMergeInfo]`                        |
| `PreprocessorMacroReplacement` 6 个 source* 布尔                           | Python dataclass + MacroSource enum                           |
| `PreprocBranchSig: vector<pair<int,int>>`                               | `list[tuple[int, int]]`                                       |
| `ConditionalAst` arena 模式                                               | 不需要——直接读 tree-sitter AST                                      |
| `buildCodeMaskForLine`（注释/字符串挖空）                                        | `re` 正则 + `in_block_comment` 状态机                              |

### 2.5 核心算法借鉴点

从 nsp `preprocessor_view.cpp`（约 2680 行）借鉴的核心算法：

1. **6 级宏优先级链**（`seedInitialPreprocessorMacros:860-929`）——Python 用 `OrderedDict` 按序覆盖
2. **`#art` 默认 0 注入 + companion constant 按 include 闭包作用域化**（`scopeArtCompanionConstantsToView:502-549`）
3. **`#if/#elif` 表达式求值**（`PreprocessorExprParser:931-1446`）——Python 重写，支持 `defined()`/`&&`/`||`/算术/位运算
4. **function-like macro 展开**（`expandFunctionLikeMacro:1030-1136`）——`##` token paste + `#` 字符串化
5. **inactive 分支隔离 probe**（`interpretInactiveBranchProbe:2072-2121`）——`speculativeInactive` 模式，不污染 active 状态
6. **6 个 `source*` 标记**让宏来源可追溯
7. **branchSignatureKey + branchFamilyKey**（`workspace_index_relations.cpp:17-35`）——索引阶段算，存 SQLite
8. **`#ifndef` include guard 识别**（`extractDefaultGuardMacroName:1669-1710`）
9. **`#if` 嵌套递归解释**（`interpretNodeList` + `interpretConditionalNode`）
10. **数值上下文未定义宏合成 0**（`evaluateMacro:1222-1236`）+ 诊断

### 2.6 模块结构

```
shaderbase/
└── shaderbase/
    └── preprocessor/
        ├── __init__.py
        ├── view.py              ← PreprocessorView 主类（输出结构）
        ├── interpreter.py       ← 状态机解释器（核心算法）
        ├── expr_parser.py       ← #if 表达式求值器
        ├── macro_expander.py    ← function-like macro 展开
        ├── branch_signature.py  ← branchSignatureKey / branchFamilyKey（索引阶段算）
        ├── art_macros.py        ← #art 宏识别和注入
        ├── include_guard.py    ← #ifndef guard 识别
        ├── code_mask.py         ← 注释/字符串挖空
        └── tests/
            ├── test_interpreter.py
            ├── test_expr_parser.py
            ├── test_art_macros.py
            ├── fixtures/         ← 跟 nsp 同款 shader 片段
            └── regression/       ← 跟 nsp 输出对照的回归用例
```

### 2.7 验证策略

**关键验证手段**：用同一组 shader 源码分别跑 nsp 的 C++ 版和 shaderbase 的 Python 版，比对输出。

具体步骤：

1. 从 shader-source 抽 20-50 个有代表性的 .nsf/.hlsl 文件
2. 用 nsp-intellision 的 `nsf/_debugBuildDiagnostics` 或类似 method 拿 C++ 版的 lineActive/branchSigs 输出
3. 用 shaderbase Python 版跑同样文件，拿输出
4. 逐行比对 line_active 和 branch_signature，差异即 bug
5. 重点测：
   - 简单 `#ifdef/#ifndef/#if 0`
   - 嵌套 `#if/#if/#endif/#endif`
   - `#elif` 多分支
   - `#art` 宏注入
   - include guard
   - function-like macro 在 `#if` 里展开
   - 数值上下文未定义宏
   - **空 defines 全分支视图**（索引阶段的核心场景，nsp 也这么做）

### 2.8 不做的微调（v2 修订）

- **不引入 nsp 的 ConditionalAst 中间表示**——直接吃 tree-sitter AST，省一层
- **不做 expanded_source**——shaderbase 不是 IDE，不需要把 inactive 行挖空给下游解析器
- **不做 active 视图给诊断用**——没有实时诊断需求，active 只在查询时按 Agent 传入的 macros 算
- **不做 compiler_macro_snapshot_provider**——shadercompiler 不在范围，shaderbase 只吃 `#art` 默认值 + Agent 传入的 macros
- **不做 unit_macro_profile_provider**——不接 gimlocalvariants.json，profile 由 Agent 传入
- **不做完整 diagnostics**——只做"未定义宏"和"#if 表达式语法错"两类，其他诊断交给 grammar 层
- **不做 IDE 的活动单元选择**——shaderbase 不是 IDE，没有实时选 active unit 的交互

### 2.9 在最终 AI 知识图谱上的呈现

**节点/边新增字段**（SQLite schema 增量）：

| 字段 | 没有 PreprocessorView | 有 PreprocessorView |
|---|---|---|
| `nodes.conditional_signature` | NULL | `"#if USE_SEASON_ID:branch0"` |
| `nodes.branch_family` | NULL | 分支家族键（同家族的节点可一起查） |
| `edges.conditional_signature` | NULL | CALLS 边所在分支签名 |
| `edges.is_active`（查询时算） | 无法算 | 按 Agent 传入的 macros 算 true/false |

**新增边类型**：
- `CONDITIONAL_ON`：连接"函数"和"`#art` 开关"——"这个函数只在 USE_SEASON_ID 开启时编译"
- `BRANCH_FAMILY`：连接同分支家族的节点——支持"查这个分支家族里所有符号"

**查询能力差异**（以"改了 SampleColorTextureBias 影响哪些 effect"为例）：

| 没有 PreprocessorView | 有 PreprocessorView |
|---|---|
| 返回 3 条 CALLS 边，分不清哪条 active | 返回 3 条边，标 active/inactive + 分支签名 |
| Agent 可能误报"影响 common_snow" | Agent 能回答"当前配置下只影响 pbr_rock；common_snow 的调用在 #if !RAINFALL_ENABLE 里，开启时才影响" |

---

## 3. 整体开发文档

### 3.1 项目结构

```
shaderbase/                            ← 主项目
├── pyproject.toml
├── README.md
├── shaderbase/
│   ├── __init__.py
│   ├── cli.py                         ← 命令行入口（index/query）
│   ├── config.py                     ← 配置加载
│   ├── discover/
│   │   ├── __init__.py
│   │   ├── walker.py                  ← 文件发现（仿 nsp workspace_index_scan）
│   │   └── ignore.py                  ← .gitignore/.cbmignore 解析
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── tree_sitter_loader.py      ← 加载自研 grammar
│   │   └── ast_utils.py               ← AST 遍历 helper
│   ├── preprocessor/
│   │   └── ...                        ← 见 §2.5
│   ├── extract/
│   │   ├── __init__.py
│   │   ├── nodes.py                   ← 抽 Function/Struct/Uniform/...
│   │   ├── edges.py                   ← 抽 CALLS/FLOWS_TO/USES_UNIFORM/...
│   │   ├── resolve_calls.py           ← CALLS 边跨文件 resolve（核心难点）
│   │   └── shader_semantics.py        ← shader 特有语义（technique/material/entry_point）
│   ├── store/
│   │   ├── __init__.py
│   │   ├── schema.sql                 ← SQLite schema
│   │   ├── connection.py              ← SQLite 连接池
│   │   └── shard_cache.py             ← shard 化磁盘 cache（借鉴 nsp）
│   ├── incremental/
│   │   ├── __init__.py
│   │   ├── dirty.py                   ← 文件 mtime/size 比对
│   │   ├── reverse_deps.py            ← 反向依赖图（include + CALLS）
│   │   └── update.py                  ← 增量更新流程
│   ├── mcp_server/
│   │   ├── __init__.py
│   │   └── tools.py                   ← MCP 工具定义
│   └── tests/
│       └── ...
├── resources/                         ← 直接拷贝 nsp 的 JSON bundle
│   ├── builtins/
│   │   └── intrinsics/                ← HLSL intrinsic 列表（139 个）
│   ├── types/
│   │   ├── object_types/              ← Texture2D/SamplerState/...
│   │   └── ...
│   └── methods/
│       └── object_methods/            ← Sample/Load/...
└── g66-shader-grammar/               ← 自研 grammar 子项目（独立维护）
    └── ...（见 §1.2）
```

### 3.2 SQLite Schema

```sql
-- 节点表
CREATE TABLE nodes (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                  -- Function/Struct/Uniform/Texture/CBuffer/Macro/Material/Technique/EntryPoint/File
  name TEXT,
  qualified_name TEXT,
  file_path TEXT,
  line INTEGER,
  start_col INTEGER,
  end_line INTEGER,
  end_col INTEGER,
  properties JSON,                     -- 类型/默认值/UI 元信息/语义/stage 等
  conditional_signature TEXT,          -- 所在 #if 分支签名（active 时为 NULL）
  project TEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 边表
CREATE TABLE edges (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                  -- CALLS/INCLUDES/DEFINES/REFERENCES/FLOWS_TO/DECLARES_UNIFORM/USES_UNIFORM/HAS_MEMBER/BELONGS_TO_MATERIAL/IS_ENTRY_POINT/EXPOSES_TECHNIQUE
  source_id INTEGER NOT NULL,
  target_id INTEGER NOT NULL,
  properties JSON,                     -- 调用位置/条件分支签名/语义槽位等
  project TEXT NOT NULL,
  FOREIGN KEY (source_id) REFERENCES nodes(id),
  FOREIGN KEY (target_id) REFERENCES nodes(id)
);

-- 索引
CREATE INDEX idx_nodes_name ON nodes(name);
CREATE INDEX idx_nodes_qname ON nodes(qualified_name);
CREATE INDEX idx_nodes_kind ON nodes(kind);
CREATE INDEX idx_nodes_file ON nodes(file_path);
CREATE INDEX idx_nodes_project ON nodes(project);
CREATE INDEX idx_edges_kind ON edges(kind);
CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
CREATE INDEX idx_edges_project ON edges(project);

-- 文件元数据（增量索引用）
CREATE TABLE file_meta (
  file_path TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  mtime INTEGER,
  size INTEGER,
  content_hash TEXT,                   -- 内容 hash，跟 mtime/size 配合判 dirty
  node_count INTEGER,
  edge_count INTEGER,
  parsed_ok BOOLEAN,
  error_count INTEGER,
  last_indexed_at TIMESTAMP
);

-- 反向依赖图（include + CALLS 反向）
CREATE TABLE reverse_deps (
  source_file TEXT NOT NULL,           -- 被依赖的文件
  dependent_file TEXT NOT NULL,         -- 依赖它的文件
  dep_kind TEXT NOT NULL,              -- INCLUDE/CALLS/USES_UNIFORM
  project TEXT NOT NULL,
  PRIMARY KEY (source_file, dependent_file, dep_kind)
);

-- 项目元数据
CREATE TABLE projects (
  name TEXT PRIMARY KEY,
  root_path TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);
```

### 3.3 MCP 工具签名

```python
# shaderbase/mcp_server/tools.py

@mcp.tool()
def index_shader_source(
    repo_path: str,
    project_name: str | None = None,
    mode: str = "full"  # full/moderate/fast
) -> dict:
    """索引一个 shader 仓库，建图。"""

@mcp.tool()
def incremental_update(project: str) -> dict:
    """手动触发增量更新（基于 mtime/size 比对）。"""

@mcp_tool()
def rebuild_index(project: str, clear_cache: bool = False) -> dict:
    """全量重建索引。"""

@mcp.tool()
def search_shader(
    project: str,
    name_pattern: str | None = None,
    kind: str | None = None,
    file_pattern: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """按名字/类型/文件过滤搜节点。"""

@mcp.tool()
def trace_calls(
    project: str,
    function_name: str,
    direction: str = "both",  # inbound/outbound/both
    depth: int = 3,
    limit: int = 100,
) -> dict:
    """沿 CALLS 边 BFS 遍历调用链。"""

@mcp.tool()
def get_code_snippet(
    project: str,
    qualified_name: str,
    context_lines: int = 0,
) -> dict:
    """读某函数/结构体的源码。"""

@mcp.tool()
def get_definition(project: str, symbol: str) -> dict:
    """找符号的定义位置。"""

@mcp.tool()
def get_references(project: str, symbol: str) -> dict:
    """找符号被引用的所有位置（active 分支）。"""

@mcp.tool()
def trace_stage_flow(
    project: str,
    semantic: str,  # 比如 "TEXCOORD2"
) -> dict:
    """找 VS 输出 semantic → 哪些 PS 输入。"""

@mcp.tool()
def find_uniform_usage(project: str, uniform_name: str) -> dict:
    """找 uniform 被哪些函数使用。"""

@mcp.tool()
def get_material_files(project: str, material_name: str) -> dict:
    """找材质三件套文件。"""

@mcp.tool()
def find_entry_points(project: str, technique: str | None = None) -> dict:
    """找所有 vs_main/ps_main 入口 + 所属 technique。"""

@mcp.tool()
def get_architecture(
    project: str,
    aspects: list[str] | None = None,  # overview/hotspots/boundaries/clusters
) -> dict:
    """仓库架构总览。"""

@mcp.tool()
def detect_changes(
    project: str,
    since: str = "HEAD~3",
    scope: str = "impact",  # files/impact
    depth: int = 2,
) -> dict:
    """git diff → 影响爆炸半径。"""

@mcp.tool()
def find_dead_code(project: str, exclude_entry_points: bool = True) -> dict:
    """找没人调用的函数（可选排除 entry point）。"""

@mcp.tool()
def list_projects() -> dict:
    """列出所有已索引项目。"""

@mcp.tool()
def delete_project(project: str) -> dict:
    """删除项目及图数据。"""

@mcp.tool()
def index_status(project: str) -> dict:
    """查询项目索引状态、覆盖率、错误列表。"""
```

### 3.4 工程量评估（AI 辅助下）

按用户指示重新评估——大部分代码和测试是 AI 跑，速度比人工快得多。重新拆解：

| 阶段                                 | 工作                                         | AI 辅助下     | 纯人工       |
| ---------------------------------- | ------------------------------------------ | ---------- | --------- |
| 1.1 项目骨架                           | 目录结构 + pyproject + schema.sql + MCP 壳      | 0.5 天      | 1 天       |
| 1.2 grammar 最小版本                   | grammar.js 跑通 function/variable/struct     | 1 天        | 3 天       |
| 1.3 grammar 深化                     | 加参数/函数体/表达式/call/member                    | 2-3 天      | 2 周       |
| 1.4 G66 特化语法                       | #art/technique/annotation/semantic         | 1-2 天      | 1 周       |
| 1.5 grammar 测试迭代                   | 用 shader-source 全量跑迭代收敛                    | 3-4 天      | 2 周       |
| 1.6 Python 绑定                      | tree_sitter_g66_shader 包                   | 0.5 天      | 1 天       |
| **阶段 1 grammar 小计**                |                                            | **8-11 天** | **5-6 周** |
| 2.1 PreprocessorView 框架            | 数据结构 + 主类 + 入口                             | 1 天        | 2 天       |
| 2.2 状态机解释器                         | 核心算法 Python 重写                             | 2-3 天      | 1 周       |
| 2.3 表达式求值器                         | #if 表达式                                    | 1-2 天      | 3 天       |
| 2.4 function-like macro 展开         | `##` + `#` + 递归保护                          | 1-2 天      | 3 天       |
| 2.5 #art 宏处理                       | 识别 + 优先级链 + companion                      | 1 天        | 2 天       |
| 2.6 include guard + inactive probe | 隔离 probe + 分支签名                            | 1-2 天      | 3 天       |
| 2.7 跟 nsp 对照测试                     | 用同款 shader 比对输出                            | 2-3 天      | 1 周       |
| **阶段 2 预处理小计**                     |                                            | **9-14 天** | **4-5 周** |
| 3.1 节点抽取器                          | AST 遍历 → SQLite nodes 表                    | 1-2 天      | 3 天       |
| 3.2 边抽取器                           | INCLUDES/DEFINES/HAS_MEMBER 等              | 1-2 天      | 3 天       |
| 3.3 CALLS 边 resolve                | 跨文件 resolve + 重载消歧（最难）                     | 3-4 天      | 1-2 周     |
| 3.4 shader 语义边                     | FLOWS_TO/USES_UNIFORM/entry_point/material | 2-3 天      | 1 周       |
| 3.5 shard cache                    | 借鉴 nsp 实现                                  | 1-2 天      | 3 天       |
| **阶段 3 抽取 + 存储小计**                 |                                            | **8-13 天** | **4-5 周** |
| 4.1 MCP 工具实现                       | 全部 14 个工具                                  | 2-3 天      | 1 周       |
| 4.2 ZCode 接入测试                     | 在 ZCode 实测                                 | 1 天        | 2 天       |
| **阶段 4 MCP 小计**                    |                                            | **3-4 天**  | **1-2 周** |
| 5.1 增量更新流程                         | dirty 检测 + 反向依赖图 + 局部重建                    | 2-3 天      | 1 周       |
| 5.2 增量测试                           | 改文件验证                                      | 1-2 天      | 3 天       |
| **阶段 5 增量小计**                      |                                            | **3-5 天**  | **2 周**   |

**总工程量**：

| 维度                    | AI 辅助下 | 纯人工  |
| --------------------- | ------ | ---- |
| 阶段 1（MVP：能索引+查函数+查调用） | 3-4 周  | 3 个月 |
| 阶段 1+2（+ shader 语义）   | 5-7 周  | 5 个月 |
| 阶段 1+2+3（+ 增量）        | 6-8 周  | 6 个月 |

**用户判断"两周最多了"对应阶段 1 的 MVP 范围**——8-11 天 grammar + 9-14 天预处理 + 8-13 天抽取存储 + 3-4 天 MCP，纯加起来 28-42 天（4-6 周）。**两周不够完整阶段 1**，但能拿到"能跑的最小版本"（grammar 跑通 + CALLS 边能建 + MCP 能查）。

建议里程碑：

- **2 周里程碑**：grammar 跑通常见语法 + PreprocessorView 框架 + 节点抽取 + MCP 三个核心工具（search/trace/snippet），其他边暂存 raw 表不 resolve
- **4 周里程碑**：CALLS 边 resolve 完成 + shader 语义边（FLOWS_TO/USES_UNIFORM）+ 全部 MCP 工具
- **6 周里程碑**：PreprocessorView 跟 nsp 对照测试通过 + 增量更新 + ZCode 实测

### 3.5 关键风险

| 风险                                    | 概率  | 应对                       |
| ------------------------------------- | --- | ------------------------ |
| grammar 歧义（`<>` 模板 vs annotation）卡住   | 高   | 外部扫描器 lexical precedence |
| CALLS 边跨文件 resolve 漏的多（nsp 也漏 30%）    | 中   | 接受 70-80% 召回，跑通比完美重要     |
| PreprocessorView Python 版跟 C++ 版行为不一致 | 中   | 用同款 shader 对照测试          |
| tree-sitter Python 绑定在 Windows 装不上    | 低   | 提供预编译 wheel              |
| shader-source 里某些边界语法 grammar 不认      | 中   | 接受 ERROR 节点，grammar 迭代覆盖 |
| MCP server 跟 ZCode 兼容性问题              | 低   | codebase-memory 同样方案已验证  |

### 3.6 不做的事

- 不做 Cython/C 扩展——纯 Python，简化部署
- 不做完整 diagnostics——grammar 只识别不校验，诊断交给引擎侧
- 不做 compiler_macro_snapshot_provider——依赖 shadercompiler
- 不做 unit_macro_profile_provider——同上
- 不做 3D 可视化——非核心
- 不做 Cypher 查询——直接 SQL
- 不做近克隆检测（阶段 4 再说）
- 不做 L3 uniform 反向追踪（运行时赋值）

---

## 4. 下一步

1. **用户确认本文档**——方案有没有需要改的
2. **进 plan mode 写阶段 1 详细实施方案**——含每个文件的代码骨架、grammar.js 初始版本、PreprocessorView Python 版接口签名
3. **建项目目录**——`shaderbase/` + `g66-shader-grammar/` 骨架
4. **从最小可跑的 grammar 开始迭代**

---

**文档版本**：v1.0
**生成日期**：2026-07-21
**配套**：`SHADERBASE_DESIGN.md`（设计拆解）、`codebase-memory-mcp/`（参考项目1）、`nsp-intellision/`（参考项目2）、`shader-source/`（目标仓库）
