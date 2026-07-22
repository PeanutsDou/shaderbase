# Known Issues

本文件记录 grammar 已知未覆盖的 G66 语法点，跑 baseline 后持续更新。

## 状态标记

- ⚠️ **未覆盖**：grammar 不认，会产 ERROR 节点
- 🟡 **部分覆盖**：能解析但结构不全（比如认了 texture 但不认 annotation 块）
- ✅ **已覆盖**：grammar 完整识别该语法
- 🔧 **进行中**：正在补 grammar 规则
- 🚫 **不可收敛**：C 类边角写法，强行修会破坏 grammar 结构

## G66 特化语法覆盖状态（v3 基线，2026-07-22）

| 语法 | 频次 | 状态 | 备注 |
|---|---|---|---|
| `#art NAME "..." "BOOL"/"INT"` | 804 | ✅ 已覆盖 | `preproc_art_directive` 规则，实测 800/804 |
| `technique TShader <...> { pass p0 {...} }` | 867 | ✅ 已覆盖 | `technique_block` + `pass_block`，实测 852 |
| `texture NAME : Semantic <annotation>` | 2120 | ✅ 已覆盖 | `texture_declaration`，实测 1794 |
| `SamplerState NAME { Filter=...; }` 状态块 | 2227 | ✅ 已覆盖 | `sampler_state_declaration`，实测 2096 |
| `float u_x < SasUiLabel="..."; > = 0.5f` annotation | ~8000 | ✅ 已覆盖 | `metadata_block` + `metadata_assignment`，实测 7119 块 |
| `#excludefromtemptech NAME` | 15 | ✅ 已覆盖 | `preproc_exclude_from_temp_tech`，实测 15 |
| `cbuffer NAME : register(b1) { fields };` | 8 | ✅ 已覆盖 | `cbuffer_specifier`（v3 新修接入 _top_level_item），实测 4 |
| `#elif` 无 condition | 1 | 🚫 不可收敛 | CPP 上游 parser 表冲突，C 类边角 |
| `if` 不带括号 | 5 | 🚫 不可收敛 | 全局规则改动会引发 if/for/while 歧义，C 类边角 |
| `state = 1 - SRCALPHA` 带算术 | 1 | 🚫 不可收敛 | state_assignment 加表达式会放大全局歧义 |
| `Texture2D X : register(t) : Semantic` 双冒号 | 1 | 🚫 不可收敛 | 大写贴图走 CPP declaration，semantics 只允许一个 |

## 整体覆盖率

- 文件解析率：1223 / 1227 = **99.67%**
- ERROR 节点率：8 / 1,689,374 = **0.0005%**
- 失败文件：4 个（全是 C 类边角写法）

## tree-sitter-hlsl 上游可能的 bug

v3 基线未发现上游 bug——所有 ERROR 都是 G66 私有语法变体导致的 C 类边角。

## 边角写法（不补）

见上表 🚫 不可收敛的 4 类，共 8 个 ERROR，分布在 4 个文件：
- shaderlib/season_uniforms.hlsl（5 个，if 不带括号）
- base/common_snow.nsf（1 个，裸 #elif）
- common_shader/volumetric_cloud.nsf（1 个，state 带算术）
- ui/gradient_color.nsf（1 个，Texture2D 双冒号）

---

**更新规则**：每跑一次 baseline 或改 grammar 后，必须更新本文件的状态标记。
当前版本对应 `scripts/error_report_v3.txt`。
