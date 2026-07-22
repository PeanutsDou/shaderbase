# Known Issues

本文件记录 grammar 已知未覆盖的 G66 语法点，跑 baseline 后持续更新。

## 状态标记

- ⚠️ **未覆盖**：grammar 不认，会产 ERROR 节点
- 🟡 **部分覆盖**：能解析但结构不全（比如认了 texture 但不认 annotation 块）
- ✅ **已覆盖**：grammar 完整识别该语法
- 🔧 **进行中**：正在补 grammar 规则

## G66 特化语法覆盖状态

| 语法 | 频次 | 状态 | 备注 |
|---|---|---|---|
| `#art NAME "..." "BOOL"/"INT"` | 804 | ⚠️ 未覆盖 | 需加 `preproc_art_directive` 规则 |
| `technique TShader <...> { pass p0 {...} }` | 867 | ⚠️ 未覆盖 | 需加 `technique_block` + `pass_block` |
| `texture NAME : Semantic <annotation>` | 2120 | 🟡 部分覆盖 | texture 当类型认了，`<>` annotation 没认 |
| `SamplerState NAME { Filter=...; }` 状态块 | 2227 | 🟡 部分覆盖 | SamplerState 当类型认了，`{...}` 状态块没认 |
| `float u_x < SasUiLabel="..."; > = 0.5f` annotation | ~8000 | ⚠️ 未覆盖 | 需加 `metadata_block` + `metadata_assignment` |
| `#excludefromtemptech NAME` | 15 | ⚠️ 未覆盖 | 需加 `preproc_exclude_from_temp_tech` |

## tree-sitter-hlsl 上游可能的 bug

待 baseline 跑完后填。

## 边角写法（不补）

待 baseline 跑完后填。

---

**更新规则**：每跑一次 baseline 或改 grammar 后，必须更新本文件的状态标记。
