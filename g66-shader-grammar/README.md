# g66-shader-grammar

G66 shader 的 tree-sitter grammar，基于 [tree-sitter-hlsl](https://github.com/tree-sitter-grammars/tree-sitter-hlsl) fork，扩展支持 G66 特化语法。

## 上游来源

- **base**: `tree-sitter-grammars/tree-sitter-hlsl` v0.2.0 (commit `bab9111`)
- **base 的 base**: `tree-sitter/tree-sitter-cpp`（tree-sitter-hlsl 又是基于 tree-sitter-cpp 扩展的）

继承关系：`tree-sitter-c` → `tree-sitter-cpp` → `tree-sitter-hlsl` → `g66-shader-grammar`

## 上游已支持的 HLSL 语法

继承自 tree-sitter-hlsl：

- `hlsl_attribute`：`[unroll]` `[numthreads(8,8,1)]` 等
- `semantics`：`: TEXCOORD0` / `: SV_Position` 等
- `discard_statement`：`discard;`
- `qualifiers`：`precise`/`shared`/`groupshared`/`uniform`/`row_major`/`snorm`/`unorm`/`nointerpolation` 等
- `cbuffer_specifier`：`cbuffer NAME { ... }`
- 参数方向修饰符：`in`/`out`/`inout`
- 函数/参数/变量声明后跟 `semantics`
- `if`/`for` 前带 `hlsl_attribute`

## 待补的 G66 特化语法（基于 `SHADERBASE_GRAMMAR_INVENTORY.md`）

| 语法 | 频次 | 状态 |
|---|---|---|
| `#art NAME "..." "BOOL"/"INT"` | 804 | 待补 |
| `technique TShader <...> { pass p0 {...} }` | 867 | 待补 |
| `texture NAME : Semantic <annotation>` | 2120 | 部分支持，annotation 待补 |
| `SamplerState NAME { Filter=...; }` 状态块 | 2227 | 部分支持，状态块待补 |
| `float u_x < SasUiLabel="..."; > = 0.5f` annotation | ~8000 | 待补 |
| `#excludefromtemptech NAME` | 15 | 待补 |

## 开发流程

见 `../SHADERBASE_GRAMMAR_DEV_PLAN.md` 的 5 阶段规划。

## 测试

```bash
# 装依赖
npm install

# 重新生成 parser（grammar.js 改动后必跑）
npx tree-sitter generate

# 跑现有 corpus 测试
npx tree-sitter test

# 解析单个文件
npx tree-sitter parse path/to/file.nsf
```

## 依赖

- Node.js（tree-sitter CLI 运行时）
- Python 3.12+（阶段 4 Python 绑定）
- `tree-sitter-c` + `tree-sitter-cpp`（npm devDependencies 自动装）

## 目录结构

```
g66-shader-grammar/
├── grammar.js                     ← 主 grammar 源（继承自 tree-sitter-hlsl，加 G66 特化）
├── package.json                   ← npm 配置
├── binding.gyp                     ← Node 绑定编译配置
├── setup.py                        ← Python 包构建
├── pyproject.toml                 ← Python 项目配置
├── tree-sitter.json                ← tree-sitter 配置
├── Makefile                        ← 构建规则
├── LICENSE                         ← MIT（继承自上游）
├── .gitignore
├── src/                            ← 自动生成 + scanner
│   ├── parser.c                    ← tree-sitter generate 产物
│   ├── scanner.c                   ← 外部扫描器（raw string）
│   ├── grammar.json                ← grammar 序列化产物
│   ├── node-types.json             ← 节点类型元数据
│   └── tree_sitter/                 ← tree-sitter 运行时头文件
├── test/
│   ├── corpus/                     ← 回归测试 corpus
│   │   └── basic.txt               ← 继承自上游的 baseline corpus
│   └── fixtures/                   ← 真实 shader 当 fixture
│       ├── minimal/                ← 简单 shader 片段
│       └── full/                   ← 完整 shader 文件（指向 shader-source）
├── scripts/
│   ├── coverage.py                 ← 量化覆盖率脚本（待写）
│   ├── corpus_runner.py            ← 跑 corpus 自动比对（待写）
│   ├── fixtures_runner.py          ← 跑 fixtures + 统计 ERROR（待写）
│   └── export_ast.py               ← 导出 AST 可视化（待写）
└── docs/
    └── known_issues.md             ← 已知未覆盖语法（待写）
```
