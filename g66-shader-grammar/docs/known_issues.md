# Known Issues

本文件记录 grammar 已知未覆盖的 G66 语法点，跑 baseline 后持续更新。

## 状态标记

- ⚠️ **未覆盖**：grammar 不认，会产 ERROR 节点
- 🟡 **部分覆盖**：能解析但结构不全（比如认了 texture 但不认 annotation 块）
- ✅ **已覆盖**：grammar 完整识别该语法
- 🔧 **进行中**：正在补 grammar 规则
- 🚫 **不可收敛**：C 类边角写法，强行修会破坏 grammar 结构

## G66 特化语法覆盖状态（v4 基线，2026-07-23）

| 语法 | 频次 | 状态 | 备注 |
|---|---|---|---|
| `#art NAME "..." "BOOL"/"INT"` | 800 | ✅ 已覆盖 | `preproc_art_directive` 规则 |
| `technique TShader <...> { pass p0 {...} }` | 852 | ✅ 已覆盖 | `technique_block` + `pass_block` |
| `texture NAME : Semantic <annotation>` | 1794 | ✅ 已覆盖 | `texture_declaration` |
| `SamplerState NAME { Filter=...; }` 状态块 | 2096 | ✅ 已覆盖 | `sampler_state_declaration` |
| `float u_x < SasUiLabel="..."; > = 0.5f` annotation | 7119 | ✅ 已覆盖 | `metadata_block` + `metadata_assignment` |
| `#excludefromtemptech NAME` | 15 | ✅ 已覆盖 | `preproc_exclude_from_temp_tech` |
| `cbuffer NAME : register(b1) { fields }`（无 `;`） | 4+ | ✅ 已覆盖 | `cbuffer_specifier` v4 改 `;` 为 optional |
| `PLSLayout(rgba8) float4 color0 : SV_Target0;` | 10 文件 | ✅ 已覆盖 | `pls_layout_attribute` v4 新增，`field_declaration` 加可选前缀 |
| `#elif` 无 condition | 1 | 🚫 不可收敛 | CPP 上游 parser 表冲突，C 类边角 |
| `if` 不带括号 | 5 | 🚫 不可收敛 | 全局规则改动会引发 if/for/while 歧义，C 类边角 |
| `state = 1 - SRCALPHA` 带算术 | 1 | 🚫 不可收敛 | state_assignment 加表达式会放大全局歧义 |
| `Texture2D X : register(t) : Semantic` 双冒号 | 1 | 🚫 不可收敛 | 大写贴图走 CPP declaration，semantics 只允许一个 |
| function-like macro 调用当 statement（如 `GET_LIGHTING_MULTIPLIER_DEF(...)`） | 1 文件 | 🚫 不可收敛 | `g66_macro_statement` 只列已知宏名，泛匹配会回退 |
| `#if`/`#endif` 不匹配（smaa.hlsl 缺 1 个 #endif） | 1 文件 | 🚫 不可收敛 | shader 源码本身的 bug，grammar 无法修复 |

## 整体覆盖率

- 文件解析率：1227 / 1233 = **99.51%**
- 失败文件：6 个（全是 C 类边角写法）
  - `shaderlib/season_uniforms.hlsl`（5 个 ERROR，if 不带括号）
  - `base/common_snow.nsf`（1 个 ERROR，裸 #elif）
  - `common_shader/volumetric_cloud.nsf`（1 个 ERROR，state 带算术）
  - `ui/gradient_color.nsf`（1 个 ERROR，Texture2D 双冒号）
  - `shaderlib/pixel_init.hlsl`（4 个 MISSING `;`，function-like macro 当 statement）
  - `shaderlib/smaa.hlsl`（2 个 MISSING `#endif`，源码本身 #if/#endif 不匹配）

## v4 变更（2026-07-23）

- `cbuffer_specifier` 的 `;` 改 `optional(';')` + `prec.right` 解决冲突
  → 修复 6 个文件（cbuffer 块不带 `;` 的写法）
- 新增 `pls_layout_attribute` 规则 + `field_declaration` 加可选 PLSLayout 前缀
  → 修复 10 个文件（PLSLayout Pixel Local Storage 语法）
- indexer 容错末尾无换行符（补 `\n` 再判 has_error）
  → 修复 19 个文件（纯文件格式误判）
- 解析率从 97.65%（29 失败）→ 99.51%（6 失败）

## tree-sitter-hlsl 上游可能的 bug

v4 基线未发现上游 bug——所有 ERROR 都是 G66 私有语法变体或源码本身的问题。

## 边角写法（不补）

见上表 🚫 不可收敛的 6 类，分布在 6 个文件。

---

**更新规则**：每跑一次 baseline 或改 grammar 后，必须更新本文件的状态标记。
当前版本对应 v4 基线。
