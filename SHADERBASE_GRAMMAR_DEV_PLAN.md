# G66 Shader Grammar 开发规划

> 本文档规划 shaderbase 自研 tree-sitter grammar 的完整开发流程——基于 **fork tree-sitter-hlsl + baseline 驱动特化** 路线。
>
> 核心思路：**先拿现成的 tree-sitter-hlsl 跑全库 baseline，用失败清单驱动 G66 特化补全**——避免从零写基础 HLSL 语法的重复劳动。

---

## 0. 前置条件与决策

- 已完成 `SHADERBASE_GRAMMAR_INVENTORY.md`——所有要覆盖的语法点清单（47 类，按频次排序）
- 已确定走 **tree-sitter 自研 grammar** 路线
- 已确定 **PreprocessorView 后续 Python 转译**（本规划不含）

### 0.1 已敲定的决策

| 决策项 | 选择 | 理由 |
|---|---|---|
| grammar 起点 | **fork tree-sitter-hlsl** | 基础 HLSL 语法（函数/struct/表达式/swizzle 等）社区已写好，省 5-7 天 |
| 开发方法 | **baseline 驱动特化** | 先跑全库看哪些解析失败，按失败清单精准补 G66 特化语法 |
| tree-sitter 版本 | 0.22+ 最新版 | 跟 Python 包 `tree_sitter` 最新版对齐 |
| incremental parsing | 阶段 4 后再说 | grammar 阶段先全量 parse，shader 文件不大够快 |
| `<>` 歧义处理 | 阶段 2 看失败清单再定 | 如果 baseline 跑下来歧义影响大，用 external scanner；影响小就简化处理 |

---

## 1. 流程总览（5 阶段，7-11 天）

```
阶段 0: 搭环境 + fork tree-sitter-hlsl（1 天）
    ↓
阶段 1: 跑全库 baseline（1 天）—— 不改 grammar，先测覆盖率
    ↓
阶段 2: 失败分析 + 分类（1-2 天）—— 输出特化清单
    ↓
阶段 3: 逐类补 G66 特化（3-5 天）—— 按清单补 grammar
    ↓
阶段 4: 收敛 + Python 绑定 + 抽取器对接（2-3 天）
```

下面每阶段详细讲。

---

## 2. 阶段 0：搭环境 + fork（1 天）

### 2.1 做什么

1. **装工具链**
   - Node.js（tree-sitter CLI 是 Node 工具）
   - Python 3.12+ + tree-sitter Python 包
   - tree-sitter CLI：`npm install -g tree-sitter-cli` 或 `npx tree-sitter`

2. **选 tree-sitter-hlsl fork**
   - 调研社区 `tree-sitter-hlsl` 项目（GitHub 搜）
   - 评估标准：**最近 commit 日期、star 数、issue 活跃度、tree-sitter 版本兼容性**
   - 候选可能有多个，选维护最活跃的
   - 如果都不理想，考虑基于 `tree-sitter-c` 改造（C 跟 HLSL 语法近，社区维护活跃）

3. **建项目目录骨架**

```
g66-shader-grammar/                ← 自研 grammar 项目（fork 后改造）
├── grammar.js                     ← 主 grammar 文件（从 fork 拿，后续改）
├── package.json                   ← npm 配置
├── binding.gyp                     ← Python 绑定编译配置
├── src/                            ← 自动生成
├── test/
│   ├── corpus/                     ← 回归测试 corpus（后续补）
│   └── fixtures/                   ← 真实 shader 当 fixture
│       ├── minimal/                ← 简单 shader 片段
│       └── full/                   ← 完整 shader 文件（指向 shader-source）
├── scripts/
│   ├── coverage.py                 ← 量化覆盖率脚本
│   ├── corpus_runner.py             ← 跑 corpus 自动比对
│   ├── fixtures_runner.py          ← 跑 fixtures + 统计 ERROR
│   └── export_ast.py               ← 导出 AST 可视化
└── docs/
    └── known_issues.md              ← 已知未覆盖语法
```

### 2.2 关键动作

- **不改 grammar.js**——直接用 fork 现成的
- **先验证工具链**：generate / parse / test 三条命令都能跑

### 2.3 通过标准

- `npx tree-sitter generate` 不报错
- `npx tree-sitter parse test.nsf` 能解析一段标准 HLSL：
  ```hlsl
  float4 ps_main() {
      return float4(1, 0, 0, 1);
  }
  ```

---

## 3. 阶段 1：跑全库 baseline（1 天）

### 3.1 做什么

把 shader-source 全库（1298 个 .nsf/.hlsl/.fxh）喂给**未改的 tree-sitter-hlsl**，看覆盖率。这是 baseline——后续每改一次 grammar 都跟这个数字对比，看改善。

### 3.2 步骤

1. **准备 fixtures**：把 shader-source 的代表性文件软链或拷到 `test/fixtures/full/`
   - 全库 1298 个文件（跳过 no_source/no_source_pc/pipeline_output/bin/.git）
   - 也可以先抽样 200 个看趋势，再扩到全库

2. **写 `scripts/coverage.py`**：
   - 遍历 fixtures 目录
   - 对每个文件调 `tree-sitter parse`
   - 统计：
     - 解析成功/失败文件数 + 百分比
     - ERROR 节点数 / 总节点数
     - 各类节点（function_definition / call_expression / struct_specifier / preproc_if 等）抽取数量
   - 输出 top 10 ERROR 最多的文件 + 行号 + ERROR 上下文

3. **跑一遍，拿 baseline 数字**

### 3.3 输出示例

```python
==== Baseline coverage report (tree-sitter-hlsl unchanged) ====
total_files:                1298
parsed_ok:                   850   (65.5%)
crashed:                     12   (0.9%)
partial_error:              436   (33.6%)

total_nodes:               17227
error_nodes:               5200   (30.2%)

node distribution:
  function_definition:        6800   (期望 7310, 抽取率 93%)
  call_expression:            5800   (期望 8420, 抽取率 69%) ← 低，G66 特化没认
  struct_specifier:           1450
  preproc_if:                 4200
  preproc_art_directive:      0      ← 期望 804，全 ERROR
  technique_block:            0      ← 期望 867，全 ERROR
  metadata_block:             0      ← 期望 8541，全 ERROR

==== Top 10 files by ERROR count ====
  ui/floor_board_ui.nsf: 1240 ERROR (line 2342, ...)
  base/animated_grass.nsf: 980 ERROR (line 156, ...)
  ...
```

### 3.4 预期结果（基于前期调研）

| 维度 | 预期 baseline | 期望最终 |
|---|---|---|
| 解析成功率 | 60-80% | > 95% |
| ERROR 率 | 10-30% | < 2% |
| 主要 ERROR 来源 | `#art`、technique、annotation `<>`、SamplerState 状态块 | 全覆盖 |
| function_definition 抽取率 | 90%+（基础语法 fork 支持） | > 95% |
| call_expression 抽取率 | 60-70%（G66 特化的 method 调用没认） | > 90% |

### 3.5 产出物

三份清单：
1. **OK 文件清单**——这些文件 grammar 已经能解析，不用动
2. **部分 ERROR 文件清单**——能解析但有 ERROR，要看 ERROR 上下文
3. **崩/失败文件清单**——完全解析失败的，是 grammar bug 或罕见语法

---

## 4. 阶段 2：失败分析 + 分类（1-2 天）

### 4.1 做什么

看阶段 1 的失败清单，把所有 ERROR 按原因分类。**这一步决定了后面要补什么**——分类准了，阶段 3 才有靶子。

### 4.2 分类标准

**类别 A：G66 特化语法没覆盖**

这是 fork 不认的语法——tree-sitter-hlsl 是为标准 HLSL 写的，G66 加的私有语法它一无所知。需要补 grammar 规则。预期这部分占 ERROR 大头。

| 语法点 | 频次 | 处理 |
|---|---|---|
| `#art NAME "..." "BOOL"/"INT"` | 804 | 加 `preproc_art_directive` 规则 |
| `technique TShader <...> { pass p0 {...} }` | 867 | 加 `technique_block` + `pass_block` |
| `texture NAME : Semantic <annotation>` | 2120 | 加 `texture_declaration` |
| `SamplerState NAME { Filter=...; }` | 2227 | 加 `sampler_state_declaration` |
| `float u_x < SasUiLabel="..."; > = 0.5f` | ~8000 | 加 `metadata_block` + `metadata_assignment` |
| `#excludefromtemptech NAME` | 15 | 加 `preproc_exclude_from_temp_tech` |

**类别 B：tree-sitter-hlsl 本身的 bug**

fork 的 grammar 可能本身有 bug——某些 HLSL 标准语法它解析不对（比如某个运算符优先级错了、某个 swizzle 形态不认）。

需要改 grammar.js 修 fork 的 bug。**这一类要看 baseline 失败清单才能知道具体是什么**。

**类别 C：极端边角写法**

某个程序员写的怪写法，全库只出现 1-2 次。不影响主要抽取，可跳过。

### 4.3 输出

一份**特化清单**：

```
=== G66 特化语法补全清单 ===

[A 类：G66 特化没覆盖]

A1. #art 指令
    现状: tree-sitter-hlsl 不认，全库 804 处全 ERROR
    补法: 加 preproc_art_directive 规则
    grammar 规则草案: seq('#', 'art', identifier, string_literal, string_literal)
    预期 ERROR 改善: -X%

A2. technique 块
    现状: 不认，全库 867 处全 ERROR
    补法: 加 technique_block + pass_block + state_assignment
    预期 ERROR 改善: -Y%

A3. annotation <> 块
    现状: 不认，全库 8541 处全 ERROR
    补法: 加 metadata_block + metadata_assignment
    预期 ERROR 改善: -Z%（最大头）

A4. texture/SamplerState 声明
    ...

A5. #excludefromtemptech
    ...

[B 类：tree-sitter-hlsl bug]

B1. (待 baseline 跑完后填)
    现状: ...
    补法: ...

[C 类：边角写法（不补）]

C1. (待 baseline 跑完后填)
    ...
```

### 4.4 关键

**这一步决定后面阶段 3 的优先级排序**——按预期 ERROR 改善量从大到小排，先补收益最大的。

### 4.5 可选：开并行 agent 分担

如果失败清单很长（>1000 条 ERROR），可以开 5-8 个并行 Explore agent，每个负责一类语法，扫描所有 ERROR 上下文，按 `SHADERBASE_GRAMMAR_INVENTORY.md` 里的真实样例对照，分类到 A/B/C。

agent 只做"机械分类"，不写 grammar。

---

## 5. 阶段 3：逐类补 G66 特化（3-5 天）

### 5.1 做什么

按阶段 2 的特化清单，**逐类加 grammar 规则**。每补一类跑测试看覆盖率改善。

### 5.2 每轮迭代流程（每类一轮，2-4 小时）

```
1. 选一类特化（比如 A1: #art 指令）
2. 看 SHADERBASE_GRAMMAR_INVENTORY.md 里该类的真实样例
3. AI 辅助写 grammar 规则草案
4. 跑 npx tree-sitter generate → 确保不报语法错
5. 跑 npx tree-sitter test → 确保现有 corpus 没退化
6. 跑 coverage.py → 看 ERROR 率改善
   - 改善 → 合并改动，加新 corpus 覆盖该语法
   - 没改善 → 回退改动，重新分析
7. 如果发现新形态（baseline 没出现的），补 SHADERBASE_GRAMMAR_INVENTORY.md
```

### 5.3 预期补的顺序（按预期收益排）

| 顺序 | 补什么 | 预期 ERROR 率改善 |
|---|---|---|
| 1 | annotation `<>` 块（含 SamplerState 状态块） | -15% 到 -25% |
| 2 | technique 块 + pass 状态赋值 | -10% |
| 3 | `#art` 指令 | -5% |
| 4 | texture/SamplerState 声明 | -5% |
| 5 | `#excludefromtemptech` | -0.5%（少但要覆盖） |
| 6 | tree-sitter-hlsl bug 修复（按类别 B 清单） | -5% 到 -10% |

### 5.4 关键纪律

- **每改 grammar 必须加 corpus**——避免静默退化
- **新发现的语法必须补 `SHADERBASE_GRAMMAR_INVENTORY.md`**——活文档规则
- **数字改善才合并**——AI 不能说"改完了应该好了"就算数
- **每轮迭代都跑 coverage.py 跟 baseline 对比**——负反馈驱动

### 5.5 通过标准

- 解析率 > 95%
- ERROR 节点率 < 2%
- `#art` 抽取率 100%
- technique 抽取率 100%
- annotation 块抽取率 > 95%
- function_definition 抽取率 > 95%
- call_expression 抽取率 > 90%

---

## 6. 阶段 4：收敛 + Python 绑定 + 抽取器对接（2-3 天）

### 6.1 做什么

1. 把特化版 grammar 编译成 Python 包 `tree_sitter_g66_shader`
2. 写抽取器骨架（遍历 AST 抽 Function/Struct/Variable 等节点）
3. 跑抽取器在 50 个 shader 文件上，对照 expected.yaml

### 6.2 步骤

1. 用 `tree-sitter-python` 把 grammar 编译成 Python 包
2. 写最小抽取器：

```python
from tree_sitter import Parser, Language
import tree_sitter_g66_shader

parser = Parser()
parser.set_language(Language(tree_sitter_g66_shader.language()))

def extract_functions(source: bytes):
    tree = parser.parse(source)
    # 遍历 AST 找 function_definition 节点
    ...
```

3. 跑抽取器在 50 个 shader 文件上，对比预期（用 `SHADERBASE_DEV_PLAN.md` §1.7.2 的层 2 expected.yaml 格式）

### 6.3 通过标准

- Python 包能装能 import
- 抽取器能从 AST 拿到 Function/Struct/Variable 节点列表
- 抽取率 > 95%

### 6.4 产出物

- `tree_sitter_g66_shader` Python 包（可 pip install）
- 抽取器代码骨架（在 shaderbase 主项目里）
- 50 个文件的抽取率报告

---

## 7. 阶段 5：进 PreprocessorView 阶段

grammar 阶段完成，进 `SHADERBASE_DEV_PLAN.md` §2 的 PreprocessorView Python 转译。这部分单独规划，本规划不展开。

---

## 8. 关键决策点

### 8.1 选哪个 tree-sitter-hlsl fork

GitHub 上可能有多个 tree-sitter-hlsl 项目。评估标准：
- **最近 commit 日期**——半年以上没更新的不用
- **star 数**——star 多说明社区在用
- **issue 活跃度**——issue 多但没人修的不用
- **tree-sitter 版本兼容性**——跟 0.22+ 兼容的优先

如果都不理想，备选方案是基于 `tree-sitter-c` 改造（C 跟 HLSL 语法近，社区维护活跃）。

### 8.2 baseline 阶段跑全库还是抽样

| 选项 | 优点 | 缺点 |
|---|---|---|
| **全库 1298 文件** | 数字准、覆盖全 | 跑得慢（几分钟） |
| **抽样 200 文件** | 快、看趋势快 | 漏稀有语法 |

**推荐**：先抽样 200 看趋势，确认 baseline 跑得通后扩到全库。

### 8.3 `<>` 歧义怎么处理

| 选项 | 何时用 |
|---|---|
| **简化处理**（先当普通标点） | baseline 跑下来 `<>` 歧义影响小 |
| **lexical precedence**（grammar 内调优先级） | 歧义中等 |
| **external scanner**（外部扫描器，最复杂但最准） | 歧义严重影响解析 |

**推荐**：阶段 2 看失败清单再定——失败清单会告诉你歧义有多大。

### 8.4 阶段 2 要不要开并行 agent

| 选项 | 何时用 |
|---|---|
| **不开 agent**（我自己分析） | 失败清单 < 500 条 ERROR |
| **开 5-8 个并行 agent**（每个负责一类语法） | 失败清单 > 1000 条，量大 |

**推荐**：先不开，我自己分析。如果失败清单超过 1000 条再开 agent 分担。

---

## 9. 风险和应对

| 风险 | 概率 | 应对 |
|---|---|---|
| 选的 tree-sitter-hlsl fork 维护差、bug 多 | 中 | 选 commit 日期近、star 多的；可以中途换 |
| fork 的 grammar 结构跟 G66 特化不兼容 | 中 | 阶段 2 发现了，调整 fork 或自己重写冲突部分 |
| G66 特化语法塞进 fork 产生新歧义 | 中 | 用 lexical precedence 或 external scanner |
| tree-sitter-hlsl 的 swizzle 实现跟 G66 实际不完全一致 | 低 | 失败清单会暴露，针对性修 |
| baseline 跑下来发现 fork 不可用 | 低 | 转 tree-sitter-c 改造路线 |
| 阶段 3 迭代不收敛（数字卡住不改善） | 低 | 跑 5 天还不收敛就停下来 review 流程 |

---

## 10. 不做的事

- 不做语法校验（grammar 不报错不等于代码对）
- 不做完整 AST 快照回归
- 不做表达式求值（求值在 PreprocessorView）
- 不做跨文件 include 闭包（include 在 shaderbase 主项目测）
- 不做 incremental parsing（阶段 4 后再说）
- 不做从零写基础 HLSL 语法（fork 直接拿）

---

## 11. 里程碑

| 里程碑 | 时间 | 产出 |
|---|---|---|
| **M1（2 天）** | 阶段 0+1 完成 | fork 跑通 + baseline 覆盖率报告 |
| **M2（4-5 天）** | 阶段 2 完成 | 失败分析 + 特化清单（A/B/C 分类） |
| **M3（8-10 天）** | 阶段 3 完成 | G66 特化补全 + 全库 95% 解析率 |
| **M4（10-13 天）** | 阶段 4 完成 | Python 绑定 + 抽取器跑通 |
| **M5（13 天+）** | 阶段 5 启动 | 进 PreprocessorView |

### 11.1 跟用户预期的对比

用户之前说"两周最多了"——这套流程的 M3（grammar 跑通 95%）对应 8-10 天，**两周内能拿到 grammar 跑通的核心成果**。M4（Python 绑定）到 13 天，两周稍超。

**两周里程碑**对应的是 grammar 跑通 95% + 失败清单已分类完成，**Python 绑定和抽取器**作为 stretch goal，能在两周内启动但不一定完成。

---

## 12. 跟旧方案对比

| 维度 | 旧方案（从零写） | 新方案（fork + baseline 驱动） |
|---|---|---|
| 总工程量 | 15-20 天 | 7-11 天 |
| 基础 HLSL 语法 | 从零写（Day 1-7） | fork 直接拿 |
| G66 特化语法 | 一边写基础一边加特化 | 失败清单驱动精准加 |
| corpus 编写 | 50 个 corpus 慢慢写 | 用真实 shader 当 fixture |
| 失败定位 | 阶段 3 才知道哪坏 | 阶段 1 就知道哪坏 |
| 风险 | 高（不知道哪天能收敛） | 低（baseline 跑完就知道差多远） |
| 工程量预估准确度 | 低（不确定） | 高（baseline 数字驱动） |

---

## 13. 下一步

看完本规划，需要确认：

1. **选哪个 tree-sitter-hlsl fork**——需要我先调研社区有哪些 fork、各自维护情况吗？
2. **baseline 跑全库还是抽样**——先抽样 200 还是直接全库 1298？
3. **阶段 2 失败分析**——要不要开并行 agent 分担（如果失败清单大）？
4. **`<>` 歧义**——简化处理还是直接上 external scanner？

回答完这些，就**进 plan mode 写阶段 0+1 的详细实施方案**（含具体目录结构、选 fork 的调研方法、grammar.js 验证、coverage.py 脚本骨架、baseline 跑通流程）。

---

**文档版本**：v2.0（fork + baseline 驱动方案）
**生成日期**：2026-07-22
**配套文档**：
- `SHADERBASE_DESIGN.md`（整体设计拆解）
- `SHADERBASE_DEV_PLAN.md`（开发方案：grammar 测试流程 + PreprocessorView 转译 + SQLite schema + MCP 工具签名）
- `SHADERBASE_GRAMMAR_INVENTORY.md`（grammar 必须覆盖的所有语法点 + 真实样例 + 频次）
