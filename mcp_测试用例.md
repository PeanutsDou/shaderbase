# shaderbase MCP 测试用例

5 个经过实测验证的问题，用于测试 MCP 的查询能力。每个问题都有对应的 MCP 工具调用方式和确认答案。

---

## Q1：条件编译分析——animated_grass 的顶点动画在移动端和 PC 端走的是哪个函数？

**场景**：你在排查 animated_grass 草地 shader 的顶点动画问题。`base/animated_grass.nsf` 的 `CustomizedVertexDataOutput` 函数里有一个 `#if API_MOBILE_HIGH_QUALITY` 条件分支，你想搞清楚在不同配置下调用了哪个动画函数。

**问 MCP**：`CalcVertexAnimationGrass` 被哪些地方调用？在 `API_MOBILE_HIGH_QUALITY` 开启和关闭时，调用是否生效？

**工具调用**：
```
get_references(symbol="CalcVertexAnimationGrass", macros={"API_MOBILE_HIGH_QUALITY": 0})
get_references(symbol="CalcVertexAnimationGrass", macros={"API_MOBILE_HIGH_QUALITY": 1})
```

**确认答案**：
- `CalcVertexAnimationGrass` 定义在 `shaderlib/va_uniforms.hlsl` L232-L261
- 在 `base/animated_grass.nsf` 的 `CustomizedVertexDataOutput` 函数（L124-L139）里被调用了 2 次：
  - **L129**：`CalcVertexAnimationGrass(input, v, output, u_wind_param)` —— 在 `#else` 分支里
  - **L133**：`CalcVertexAnimationGrass(input, v, output_lastframe, u_wind_param_lastframe)` —— 在嵌套的 `#if ENABLE_GBUFFER_VELOCITY && HAS_VELOCITY` 里
- **条件编译 active 标注**（带 `conditional_signature: "2:1"`）：
  - `API_MOBILE_HIGH_QUALITY=0`（PC 端/低端移动）：L129 `active=true`（`#else` 分支生效），L133 `active=false`
  - `API_MOBILE_HIGH_QUALITY=1`（高端移动）：L129 `active=false`（走了 `#if` 分支的 `CalcMeadowAnimWPO` 而非 `#else` 的 `CalcVertexAnimationGrass`），L133 `active=false`
- 另一个函数 `CalcMeadowAnimWPO` 定义在 `shaderlib/foliage_anim_functions.hlsl` L1064，只在 `API_MOBILE_HIGH_QUALITY=1` 时被调用

**验证要点**：MCP 应该能返回带 `active` 字段的结果，且 `active` 值随 `macros` 参数变化。

---

## Q2：材质三件套——pbr_default 的三个文件是什么？入口函数在哪？

**场景**：你想了解 PBR 默认材质的完整结构。G66 的材质由三件套组成：`.nsf`（入口）+ `_nodes.hlsl`（节点逻辑）+ `_parameters.hlsl`（参数声明），你想确认 pbr_default 的三件套文件和入口函数。

**问 MCP**：pbr_default 材质的三个文件分别是什么？它的 vs_main 和 ps_main 入口在哪个文件？

**工具调用**：
```
get_material_files(material_name="pbr_default")
find_entry_points(technique="TShader")
```

**确认答案**：
- 三件套文件（`get_material_files`）：
  - `pbr/pbr_default.nsf`（或 `pbr/pbr_default_volcano.nsf`）—— 入口文件
  - `pbr/nodes/pbr_default_nodes.hlsl`—— 节点逻辑
  - `pbr/nodes/pbr_default_parameters.hlsl`—— 参数声明
- 入口函数（`find_entry_points` 限定 `technique="TShader"`）：
  - `pbr/pbr_default.nsf`：`vs_main`（vertex stage）+ `ps_main`（pixel stage）
  - `pbr/pbr_default_volcano.nsf`：`vs_main`（vertex stage）+ `ps_main`（pixel stage）
- 入口通过 `technique TShader { pass p0 { VertexShader = vs_main; PixelShader = ps_main; } }` 声明

**验证要点**：MCP 应该能从材质名找到三件套文件，并从 technique 找到入口函数和 stage。

---

## Q3：调用链追踪——GetSeasonColorMeadow 调了哪些函数？这些函数定义在哪？

**场景**：你在分析季节换色逻辑。`GetSeasonColorMeadow` 是草地季节颜色的核心函数，你想追踪它的完整调用链——它调了哪些函数，这些函数定义在哪个文件。

**问 MCP**：`GetSeasonColorMeadow` 调用了哪些函数？这些被调函数的定义在哪？

**工具调用**：
```
trace_calls(function_name="GetSeasonColorMeadow", direction="outbound", depth=2)
get_code_snippet(function_name="GetSeasonColorMeadow")
```

**确认答案**：
- `GetSeasonColorMeadow` 定义在 `shaderlib/season_uniforms.hlsl` L791-L835
- 函数签名：`void GetSeasonColorMeadow(float2 uv0, half flower_mask, inout PixelMaterialInputs MaterialInputs)`
- outbound 调用的函数（depth=1）：
  - `saturate`（L806）—— HLSL intrinsic，不 resolve
  - `length`（L809）—— HLSL intrinsic
  - `GetFoliageColorSimple`（L813）—— 草地简单颜色计算
  - `customSmoothStep`（L820、L821）—— 自定义平滑步进
  - `lerp`（L825、L827、L828）—— HLSL intrinsic
- 核心逻辑：用 `u_season_factors` 季节因子 + `u_snowmask_color` 雪遮罩，通过 `GetFoliageColorSimple` 算基础颜色，再用 `customSmoothStep` 算季节遮罩，最后 `lerp` 混合

**验证要点**：MCP 应该能 BFS 遍历调用链，区分用户函数和 intrinsic，返回调用位置行号。

---

## Q4：数据流追踪——TEXCOORD2 这个 semantic 从 VS 输出流到了哪些 PS 输入？

**场景**：你在调试一个顶点传像素的数据问题。你想知道 `TEXCOORD2` 这个 semantic 在哪些 shader 文件里被用，哪些 VS 输出结构体声明了它，哪些 PS 输入结构体接收了它。

**问 MCP**：`TEXCOORD2` 这个 semantic 流过哪些结构体字段？

**工具调用**：
```
trace_stage_flow(semantic="TEXCOORD2")
```

**确认答案**：
- 全库有 113 个 `TEXCOORD2` 匹配
- 主要出现在 `VS_INPUT` 结构体（顶点输入，说明 TEXCOORD2 是从引擎/上层传入的）：
  - `base/cloud.nsf` L3：`VS_INPUT.texcoord2 (float4) : TEXCOORD2`
  - `base/cloudt.nsf` L3：`VS_INPUT.texcoord2 (float4) : TEXCOORD2`
  - `base/cloud_base_color.nsf` L3：`VS_INPUT.texcoord2 (float4) : TEXCOORD2`
  - `base/common_hiding.nsf`、`base/common_snow.nsf` 等多个 base 目录文件
- 也有出现在 `PS_INPUT` 结构体的（像素输入，说明 TEXCOORD2 从 VS 传到 PS）：
  - `base/common_bake.nsf`：`PS_INPUT.world_position`（语义复用，不是标准 TEXCOORD）
  - `base/furniture_common_sfx.nsf`：`PS_INPUT.uv_raw`
- 结论：`TEXCOORD2` 主要用于传递植被动态信息（texcoord1 存植被动态信息，texcoord2 可能存额外 UV 或世界坐标），在草地和云类 shader 中广泛使用

**验证要点**：MCP 应该能从 semantic 名查到所有声明的结构体字段，返回 struct name / field name / field type / file path / line。

---

## Q5：死代码检测——哪些函数没人调用？

**场景**：你想清理 shader 仓库里的死代码。仓库经过多轮迭代，可能有些函数定义了但没人调用。你想找出这些函数。

**问 MCP**：仓库里有没有死代码（没人调用的函数）？排在最前面的是哪些？

**工具调用**：
```
find_dead_code(exclude_entry_points=true)
```

**确认答案**：
- 全库检测出 890 个死函数（无人调用的 Function 节点，已排除 vs_main/ps_main/cs_main 入口）
- 按目录分布：
  - `common_pipeline/`：49 个死函数（主要是 compute shader 的内部辅助函数）
  - `base/`：1 个死函数 `NormalStrength`（`base/common_hiding.nsf` L111）
  - 其余分布在 pbr / sfx / shaderlib 等目录
- 排名靠前的死代码示例：
  - `cs_main`（`common_pipeline/add_surfel_to_scene.nsf` L118）—— 虽然叫 cs_main 但没被 technique 引用为入口
  - `cs_main`（`common_pipeline/allocate_voxel_surfel_cluster.nsf` L69）—— 同上
  - `NormalStrength`（`base/common_hiding.nsf` L111）—— 法线强度调整函数，可能已废弃

**注意**：890 个"死函数"里有一部分是重载函数（同名多定义）或被 `#include` 进来但当前文件没用的工具函数，不全是真正的死代码。`find_dead_code` 基于 CALLS 边的入度判断，跨文件 resolve 召回率约 70-80%，所以会有少量误报。

**验证要点**：MCP 应该能返回死函数列表，包含 function name / file path / line / node id，且排除入口函数。

---

## 使用方法

1. 启动 MCP server：`py -3 -m shaderbase.mcp_server`
2. 重启 ZCode，确保 MCP 连上（Settings → MCP 看到 shaderbase 是 connected）
3. 开新会话，按 Q1-Q5 顺序问 Agent
4. 对照本文档的"确认答案"验证 Agent 回答是否准确

如果 Agent 回答跟确认答案不一致，把 Agent 的回答贴出来，用于排查是 MCP 工具的问题还是 Agent 理解的问题。
