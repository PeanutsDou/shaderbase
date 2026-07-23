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



测试答案：

现在信息很清楚了。来看完整的分析结论：

---

## `CalcVertexAnimationGrass` 的调用位置

**被 7 个文件中的 `CustomizedVertexDataOutput` / `vs_main` 调用**（共 19 条引用记录）：

| 文件                                                | 行号       | 所在函数                            |
| ------------------------------------------------- | -------- | ------------------------------- |
| `base/animated_grass.nsf`                         | 129, 133 | `CustomizedVertexDataOutput`    |
| `base/animated_grass_noseason.nsf`                | 123, 128 | `CustomizedVertexDataOutput`    |
| `base/animated_grass_specular_flower.nsf`         | 214, 218 | `CustomizedVertexDataOutput`    |
| `base/animated_grass_specular_mask.nsf`           | 193, 197 | `CustomizedVertexDataOutput`    |
| `base/animated_grass_specular_mask_billboard.nsf` | 192, 196 | `CustomizedVertexDataOutput`    |
| `common_shader/dynamic_velocity.nsf`              | 441, 445 | `vs_main`                       |
| `shaderlib/transparent_velocity_mask.hlsl`        | 402, 406 | `CalcTransparentScreenVelocity` |
| `shaderlib/transparent_velocity_no_mask.hlsl`     | 66, 70   | `CalcTransparentScreenVelocity` |

---

## 关键：`API_MOBILE_HIGH_QUALITY` 对 `animated_grass.nsf` 的影响

从你关注的 `base/animated_grass.nsf` 第 124-142 行源码：

```hlsl
void CustomizedVertexDataOutput(VS_INPUT input, inout VertexData v, inout PS_INPUT output)
{
#if API_MOBILE_HIGH_QUALITY
    CalcMeadowAnimWPO(input, v, output, u_wind_param, u_frame_time, u_wind_y_stress, u_wind_param_lastframe, u_frame_time_lastframe);
#else
    CalcVertexAnimationGrass(input, v, output, u_wind_param);
    #if ENABLE_GBUFFER_VELOCITY && HAS_VELOCITY
        // 仅在需要输出 velocity 时计算 lastframe 风偏移，避免 TAA 残影
        PS_INPUT output_lastframe = (PS_INPUT) 0;
        CalcVertexAnimationGrass(input, v, output_lastframe, u_wind_param_lastframe);
        v.world_position_lastframe += output_lastframe.world_position;
    #endif
#endif
    ...
}
```

### 结论

| `API_MOBILE_HIGH_QUALITY` 值 | 哪个动画函数被调用                    | 说明                                                                                                          |
| --------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **== 0 / 未定义**（低端设备）        | `CalcVertexAnimationGrass` ✅ | **旧版草地动画**，还会根据 `ENABLE_GBUFFER_VELOCITY` 额外算一帧 lastframe 用于 TAA velocity                                   |
| **== 1**（高端设备）              | `CalcMeadowAnimWPO` ✅        | **新版草地动画**（定义在 `foliage_anim_functions.hlsl` 第 1064 行），功能更丰富：多 pivot 支持、branch_vector、顶点色 mask、grass_mask 等 |

> **`CalcVertexAnimationGrass` 仅在 `API_MOBILE_HIGH_QUALITY` == 0 时被调用**。当 `API_MOBILE_HIGH_QUALITY` == 1 时，代码走 `#if` 分支，调用的是 `CalcMeadowAnimWPO`，不会经过 `CalcVertexAnimationGrass`。

### 两个动画函数的差异

**`CalcVertexAnimationGrass`**（`va_uniforms.hlsl`:232）

- 简单风动画，基于 `wind_param` 做 sin 弯曲 + 纹理风噪声
- 代码约 30 行，逻辑集中

**`CalcMeadowAnimWPO`**（`foliage_anim_functions.hlsl`:1064）

- 完整 meadow 草动画管线：多 pivot、branch_vector 方向、grass_mask、顶点色 A 控制
- 通过 `FoliageAnim_Grass_Branch()` 实现更精细的草丛摆动
- 8 参重载（第 1140 行）内部自动处理 lastframe，TAA 兼容性更好

所以如果你在 `API_MOBILE_HIGH_QUALITY` 开启时排查顶点动画问题，应该去看 `CalcMeadowAnimWPO` 和它内部调用的 `FoliageAnim_Grass_Branch`，而不是 `CalcVertexAnimationGrass`。

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



测试答案：

下面是 **pbr_default**（具体变体 `pbr_default_volcano`）材质的完整结构分析：

---

## 三件套文件

| 角色              | 文件路径                                    |
| --------------- | --------------------------------------- |
| **入口文件 (.nsf)** | `pbr/pbr_default_volcano.nsf`           |
| **节点逻辑**        | `pbr/nodes/pbr_default_nodes.hlsl`      |
| **参数声明**        | `pbr/nodes/pbr_default_parameters.hlsl` |

---

## vs_main / ps_main 入口

**vs_main** 和 **ps_main** 并不直接定义在 `.nsf` 文件中，而是通过 **include 链**引入：

1. `pbr/pbr_default_volcano.nsf` 在 `technique TShader` 中声明：
   
   ```hlsl
   VertexShader = vs_main;
   PixelShader  = ps_main;
   ```

2. 同一 `.nsf` 文件末尾通过 `#include` 引入共享 shaderlib：
   
   ```hlsl
   #include "../shaderlib/forward_shading_transparent.hlsl"
   ```

3. `vs_main` 和 `ps_main` 的实际定义就在 **`shaderlib/forward_shading_transparent.hlsl`** 中：
   
   | 入口          | 文件                                           | 行号         | 签名                                                                      |
   | ----------- | -------------------------------------------- | ---------- | ----------------------------------------------------------------------- |
   | **vs_main** | `shaderlib/forward_shading_transparent.hlsl` | **第 14 行** | `PS_INPUT vs_main(VS_INPUT input)`                                      |
   | **ps_main** | `shaderlib/forward_shading_transparent.hlsl` | **第 66 行** | `PS_OUTPUT ps_main(PS_INPUT input, bool bIsFrontFace : SV_IsFrontFace)` |

---

## 调用链简图

```
pbr_default_volcano.nsf (technique TShader)
  ├─ VertexShader = vs_main
  │    └─ #include "../shaderlib/forward_shading_transparent.hlsl"
  │         └─ vs_main 在此定义
  │              └─ 调用 VertexDataNodesBasedGraph() ← pbr_default_nodes.hlsl 中的节点逻辑
  │
  └─ PixelShader = ps_main
       └─ #include "../shaderlib/forward_shading_transparent.hlsl"
            └─ ps_main 在此定义
                 └─ 调用 PixelNodesBasedGraph() ← pbr_default_nodes.hlsl 中的节点逻辑
```

`.nsf` 中的 `VertexDataNodesBasedGraph` / `PixelNodesBasedGraph` 是连接共享 shaderlib 和材质特有节点逻辑的桥梁，它们最终调用 `pbr_default_nodes.hlsl` 中生成的节点计算代码。

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



测试答案：

## `GetSeasonColorMeadow` 的调用链分析

`GetSeasonColorMeadow` 定义在 **`shaderlib/season_uniforms.hlsl`** 第 791 行。

### 直接调用的函数（outbound，深度 1）

| 被调函数                    | 类型        | 定义位置                                 |
| ----------------------- | --------- | ------------------------------------ |
| `GetFoliageColorSimple` | **自定义函数** | `shaderlib/season_uniforms.hlsl:564` |
| `customSmoothStep`      | **自定义函数** | `shaderlib/function.hlsl:1136`       |
| `saturate`              | HLSL 内置   | —                                    |
| `length`                | HLSL 内置   | —                                    |
| `lerp`                  | HLSL 内置   | —                                    |

### 关键调用点（行号级）

从边信息来看调用发生在以下行：

- **第 809 行** → `length`（计算草地位置距中心长度）
- **第 813 行** → `GetFoliageColorSimple`（获取 foliage 基础颜色）
- **第 820 行** → `customSmoothStep`（对距离做平滑过渡）
- **第 821 行** → `customSmoothStep` + `saturate`
- **第 825/828/830 行** → `lerp` + `saturate`（混合季节颜色）

### 第二层调用（深度 2）

`GetFoliageColorSimple`（`season_uniforms.hlsl:564`）本身也可能调用其他函数，需要我继续追下去吗？

---

**总结**：核心被调函数有 **2 个**：

1. **`GetFoliageColorSimple`** — `shaderlib/season_uniforms.hlsl` 第 564 行
2. **`customSmoothStep`** — `shaderlib/function.hlsl` 第 1136 行

其余均为 HLSL 内置函数。你要看一下源码确认具体逻辑吗？

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



测试答案：

数据出来了，信息量很大。我来帮你系统梳理 `TEXCOORD2` 的流通全貌。

---

## `TEXCOORD2` 流通分析

**定义位置：** 没有显式定义（作为 HLSL 内置 semantic，由图形 API 定义）

**引用总量：** 113 处

---

### 一、作为 VS 输入（来自顶点缓冲区）— 第1类 UV 输入

绝大部分 `.nsf` 文件中，**`VS_INPUT` 结构体**的 `texcoord2` 字段使用 `TEXCOORD2` semantic，类型 `float4`：

| 目录                   | 文件列表                                                                                                                                                                                                                                                                                                                                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **base/**            | `cloud.nsf`, `cloudt.nsf`, `cloud_base_color.nsf`, `cloud_blur.nsf`, `cloud_sample.nsf`, `common_hiding.nsf`, `heat_distortion.nsf`                                                                                                                                                                                                                                                                           |
| **common_pipeline/** | `decal.nsf`, `decal_change_fog.nsf`, `decal_influence.nsf`, `decal_lighting.nsf`, `decal_lighting_v2.nsf`, `deferred_decal.nsf`                                                                                                                                                                                                                                                                               |
| **common_shader/**   | `skydome_niosemap.nsf`                                                                                                                                                                                                                                                                                                                                                                                        |
| **hlod/**            | `hierarchicallod.nsf`                                                                                                                                                                                                                                                                                                                                                                                         |
| **pbr/**             | `blast_pbr_foliage.nsf`, `pbr_default.nsf`, `pbr_fabric.nsf`, `pbr_foliage.nsf`, `pbr_lava.nsf`, `pbr_silk_flow_dye.nsf`, `pbr_silk_flow_dye_transparent.nsf`, `pbr_subsurface_va.nsf`, `pbr_subsurface_vat_bonebase.nsf`, `pbr_transparent.nsf`, `pbr_transparent_dissolve.nsf`, `pbr_vat_bonebase.nsf`, `pbr_vat_bonebase_transparent.nsf`, `pbr_water.nsf`, `pbr_weapon.nsf`, `pbr_weapon_transparent.nsf` |
| **sfx/**             | `fx_vertex_particles.nsf`（`float2`）, `wpo_fragment.nsf`                                                                                                                                                                                                                                                                                                                                                       |

**路径含义：** 从顶点缓冲区传入的 **第二套 UV 坐标**（texcoord2）。

---

### 二、作为 VS 输出（顶点→像素阶段）— 跨阶段数据传递

| 文件                                    | 结构体         | 字段         | 类型         |
| ------------------------------------- | ----------- | ---------- | ---------- |
| `common_pipeline/sssr_common.hlsl:95` | `VS_OUTPUT` | `inv_view` | `float3x3` |

这是唯一的 **VS_OUTPUT** 使用，传递的是**视图逆矩阵**。

其余所有 PS_INPUT 的 `TEXCOORD2` 字段，理论上都是对应 VS 输出写入的数据。

---

### 三、作为 PS 输入（像素着色器接收）— 语义最多的部分

`TEXCOORD2` 在 PS_INPUT 中被重载为 **多种不同含义**，按字段名分组如下：

| 字段名               | 类型               | 含义               | 出现次数 | 代表文件                                                                                                                                                           |
| ----------------- | ---------------- | ---------------- | ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `world_position`  | `float4`         | **世界坐标位置**       | 最多   | `common_bake.nsf`, `dynamic_velocity.nsf`, `xray_perspective.nsf`, `fx_liquid.nsf`, `flow_masked_fresnel.nsf`, `scanlight_fresnel.nsf`, `terrain_input.hlsl` 等 |
| `world_normal`    | `float4`/`half3` | **世界法线**         | 4    | `unlit_outline_fresnel.nsf`, `distant_cloud_sheet.nsf`, `uber_water_circle.nsf`, `uber_water_flow.nsf`                                                         |
| `world_tangent`   | `float4`         | **世界切线**         | 2    | `normal_output_impostor.nsf`, `normal_output_impostor_dirty.nsf`                                                                                               |
| `texcoord2`       | `float4`/`half4` | **第二套 UV**（直接传递） | 12+  | `flow_cloud.nsf`, `uber_distort.nsf`, `uber_fx_*.nsf`, `uber_noise_mask.nsf`, `wpo_fragment.nsf` 等                                                             |
| `uv_raw`          | `float2`         | **原始 UV**        | 4    | `furniture_common_sfx.nsf`, `flow_water_fluorescer.nsf`, `flow_water_mask.nsf`, `flow_water_mask_rim_diss.nsf`                                                 |
| `uv0`             | `float2`         | UV 坐标            | 1    | `aurora.nsf`                                                                                                                                                   |
| `v_texture2`      | `float2`         | **采样 UV2**       | 13   | `down_sample.nsf`, `esm_hor_blur.nsf`, `hdr_*.nsf`, `horizontal_blur.nsf`, `vertical_blur.nsf`, `kawase_filter.nsf` 等                                          |
| `uv10` / `uv01`   | `float2`         | 周边 UV 偏移         | 3    | `volumetric_depth_downsample.nsf`, `volumetric_light_upsample.nsf`, `depth_downsample.nsf`                                                                     |
| `fog_info`        | `half4`/`float4` | **雾信息**          | 4    | `new_prez.nsf`, `conveyor_belt.nsf`, `flow_water.nsf`, `flow_water_instance.nsf`                                                                               |
| `point_light`     | `half4`          | **点光源信息**        | 7    | `new_terrain_water.nsf`, `terrain_water.nsf`, `terrain_water_batch.nsf` 等                                                                                      |
| `batch_id`        | `float`          | **批次 ID**        | 2    | `erosion.nsf`, `new_gold.nsf`                                                                                                                                  |
| `instance_id`     | `uint`           | **实例 ID**        | 2    | `blend_surfel_ray.nsf`, `voxel_ddgi_voxel_visualization_debug.nsf`                                                                                             |
| `water_uv`        | `float4`         | 水面 UV            | 1    | `super_flow_water.nsf`                                                                                                                                         |
| `rain_far`        | `float2`         | 远距离雨滴            | 1    | `falling.nsf`                                                                                                                                                  |
| `preposition_cs`  | `float4`         | 草地预处理位置          | 2    | `meadow_base_pre_z.nsf`, `meadow_base_pre_z_v2_1.nsf`                                                                                                          |
| `screen_position` | `float4`         | 屏幕空间位置           | 1    | `uber_modulate.nsf`                                                                                                                                            |
| `texture_raw0`    | `float2`         | 纹理原始坐标           | 1    | `animtexmix.nsf`                                                                                                                                               |
| `inv_view`        | `float3x3`       | 视图逆矩阵            | 1    | `sssr_common.hlsl`（VS_OUTPUT）                                                                                                                                  |

---

### 四、核心问题定位清单

如果你在调试某个具体材质的数据传递问题，可以对照下表判断：

**注意要点：**

1. **语义重载严重** — `TEXCOORD2` 在不同文件中被用来传递**完全不同的数据**（世界坐标、法线、UV、雾信息、点光源等），这本身不是问题（HLSL 允许语义复用），但调试时**必须看具体 shader 的 VS 输出和 PS 输入是否匹配**。

2. **VS→PS 的传递链** — 同一个材质文件内，VS 写入 `TEXCOORD2` 用的是什么字段，PS 读出时是什么字段，需要分别在 VS_OUTPUT 和 PS_INPUT 结构体中确认字段名和类型是否一致。

3. **没有标准定义** — `TEXCOORD2` 不是宏也不是函数，是 HLSL 编译器内置的 semantic，所以 `get_definition` 返回空。

需要我进一步查看某个具体文件的 VS_OUTPUT/PS_INPUT 结构体细节，或者追踪某个特定材质的完整传递链吗？



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



测试答案：

好，已经拿到了完整的数据。来汇报结果：

---

## 📊 Shader 仓库死代码扫描结果

**总计：890 个函数被标记为"无人调用"**（排除了 856 个入口函数）

仓库概况：4775 个函数节点，28685 条 CALLS 边，覆盖率足够高，结果可信。

### 🏆 排名靠前的"可疑死代码"

| #   | 函数名                              | 文件                                                       | 行号     | 说明                      |
| --- | -------------------------------- | -------------------------------------------------------- | ------ | ----------------------- |
| 1   | **`NormalStrength`**             | `base/common_hiding.nsf`                                 | 111    | 基础库里的法线强度函数，完全没人用       |
| 2   | **`CorrelatedColorTemperature`** | `common_pipeline/combine_lut.nsf`                        | 250    | 色温相关颜色校正函数，未调用          |
| 3   | **`GetProbeTexelDirection`**     | `common_pipeline/build_hierarchical_surfel_lumin_cs.nsf` | 53     | Surfel 探针方向计算，定义未用      |
| 4   | **`PrincipalComponentAnalysis`** | `common_pipeline/compress_astc_*_impl.hlsl`              | 102    | ASTC 压缩的 PCA 函数，2个版本都未用 |
| 5   | **`FindByBoundingBox`**          | `common_pipeline/compress_astc_*_impl.hlsl`              | 192    | ASTC 包围盒搜索，同上           |
| 6   | **`EncodeBlock`**                | `common_pipeline/compress_astc_*_impl.hlsl`              | ~560   | ASTC 块编码核心逻辑，两个实现版本都闲置  |
| 7   | **`CompressBC3Block`**           | `common_pipeline/compress_bc3_impl.hlsl`                 | 125    | BC3 压缩核心函数              |
| 8   | **`CompressETC2RGBABlock`**      | `common_pipeline/compress_etc2_impl.hlsl`                | 116    | ETC2 压缩核心函数             |
| 9   | **`Min3`** / **`MatElemMul`**    | `common_pipeline/compress_etc2_impl.hlsl`                | 27/105 | Helper 函数随父函数一起被抛弃      |
| 10  | **`cs_main_loop`**               | `common_pipeline/copy_buffer.nsf`                        | 40     | Copy Buffer 的内循环入口      |

### 📂 按目录分布

- **`common_pipeline/compress_*`** — 压缩管线是重灾区：ASTC（2-plane 和普通版）、BC3、BC7（3个模式）、ETC2 的整套编码实现都处于"已定义但无 entry point 引用"状态。可能是有离线压缩工具在外部调用，或旧方案被废弃。
- **各种 `cs_main`** — 大量 compute shader 的入口函数被标记为死代码，包括 `clear_buffer`、`clear_tex2d`、`copy_buffer` 等基础工具，这些大概率是因为 `.nsf` 文件需要被 technique 引用才能从入口点追踪到。
- **`base/common_hiding.nsf` 的 `NormalStrength`** — 基础库中唯一的死代码函数。

### ⚠️ 注意事项

1. **条件编译可能掩盖调用关系**：有 123 个条件节点和 2664 条条件边。`NormalStrength` 等函数可能是在特定 `#ifdef` 分支中被调用，但我传了默认 macros（空），所以没追踪到。**需要确认一下是否真的没人用。**
2. **6 个文件有解析错误**，可能漏掉了一些引用关系：
   - `shaderlib/season_uniforms.hlsl`（5 个错误）
   - `base/common_snow.nsf`
   - `common_shader/volumetric_cloud.nsf`
   - `ui/gradient_color.nsf`
3. **`cs_main` 类函数**：很多被标记为死代码的 `cs_main` 可能缺少 `IS_ENTRY_POINT` 边关联到 technique。建议进一步核实哪些 `.nsf` 文件没有对应的 technique 引用。

### 🔍 建议下一步

要不要我：

- **对 `NormalStrength` 做引用追踪**（带条件编译宏）——看看它是不是在某些条件分支里被调用？
- **查看某个最可疑的死代码的具体源码**，判断能否安全删除？
- **列出按目录统计的完整死代码数量排行**，帮你规划清理顺序？

---

## 使用方法

1. 启动 MCP server：`py -3 -m shaderbase.mcp_server`
2. 重启 ZCode，确保 MCP 连上（Settings → MCP 看到 shaderbase 是 connected）
3. 开新会话，按 Q1-Q5 顺序问 Agent
4. 对照本文档的"确认答案"验证 Agent 回答是否准确

如果 Agent 回答跟确认答案不一致，把 Agent 的回答贴出来，用于排查是 MCP 工具的问题还是 Agent 理解的问题。

---

# 第二批：美术视角的抽象问题（Q6-Q10）

这批问题模拟美术/TA 在实际开发中会问的自然语言问题，测试 MCP 能否理解模糊需求并定位到具体 shader。

---

## Q6：我要做一个会随季节变色的草地材质，仓库里有现成的参考吗？季节变色是怎么实现的？

**场景**：美术想做一个季节换色的草地效果，想知道仓库里有没有现成的实现可以参考，季节变色是怎么控制的。

**问 MCP**：仓库里有草地相关的 shader 吗？季节变色是怎么实现的？用了哪些开关和函数？

**工具调用**：
```
search_shader(file_pattern="grass", kind="Technique")
search_shader(file_pattern="season", kind="Function")
search_shader(name="GetSeasonColor", kind="Function")
get_code_snippet(function_name="GetSeasonColorMeadow")
```

**确认答案**：
- 草地材质有 5 个（都在 `base/` 目录）：
  - `base/animated_grass.nsf`（technique=TShader）—— 主版本，含季节变色
  - `base/animated_grass_noseason.nsf` —— 无季节版
  - `base/animated_grass_specular_flower.nsf` —— 带花的季节版
  - `base/animated_grass_specular_mask.nsf` / `..._billboard.nsf` —— 高光遮罩版
- 季节变色通过 `#art SEASON_SUPPORT "开启四季默认开" "BOOL"` 开关控制（全库 10 个文件有这个开关）
- 核心函数 `GetSeasonColorMeadow` 定义在 `shaderlib/season_uniforms.hlsl` L791-835：
  - 用 `u_season_factors`（季节因子）+ `u_snowmask_color`（雪遮罩）
  - 调 `GetFoliageColorSimple`（L813）算基础颜色
  - 调 `customSmoothStep`（L820/821）算季节遮罩
  - 调 `lerp` 混合季节颜色
- 另有 `BlendSeasonColor`（`terrain/terrain_diffuse_common.hlsl` L542）用于地形季节混合

**验证要点**：MCP 应该能从"草地"模糊词定位到具体 .nsf 文件，并找到季节相关的函数和 #art 开关。

---

## Q7：我要做一个需要 2 套 UV 控制的材质（比如贴花同时控制纹理和遮罩），仓库里哪些 shader 支持双 UV？

**场景**：美术要做贴花材质，需要 TEXCOORD0 控制基础纹理、TEXCOORD1 或 TEXCOORD2 控制遮罩/偏移。想知道仓库里哪些 shader 已经用了双 UV。

**问 MCP**：仓库里哪些 shader 用了 TEXCOORD1 和 TEXCOORD2 两套 UV？分别是干什么用的？

**工具调用**：
```
trace_stage_flow(semantic="TEXCOORD1")
trace_stage_flow(semantic="TEXCOORD2")
```

**确认答案**：
- 全库有 88 个 `.nsf` 文件同时声明了 TEXCOORD1 和 TEXCOORD2（双 UV）
- 典型案例：
  - `common_pipeline/decal.nsf` / `decal_change_fog.nsf` / `decal_influence.nsf` / `decal_lighting.nsf` / `decal_lighting_v2.nsf` / `deferred_decal.nsf` —— 贴花系列，TEXCOORD1/2 用于贴花 UV 和遮罩
  - `base/road_specular.nsf` —— 路面高光，TEXCOORD1/2 用于第二套纹理坐标
  - `base/common_bake.nsf` —— 烘焙材质，TEXCOORD2 传 world_position
  - `billboard/normal_output_impostor.nsf` —— Impostor 法线，TEXCOORD2 传 world_tangent
- 注意：TEXCOORD2 在不同文件里语义不同——有的是第二套 UV，有的是世界坐标/法线/切线/雾信息/点光源，需要看具体文件的 PS_INPUT 字段名判断

**验证要点**：MCP 应该能交叉查询两个 semantic 的结果，找出同时用两套 UV 的文件。

---

## Q8：我想做一个火山岩浆材质，要有流动纹理 + 自发光 + 烟雾效果，仓库里有类似的多层纹理材质吗？

**场景**：美术要做火山岩浆效果——流动纹理（distort）+ 自发光（emissive）+ 烟雾（smoke），需要多层贴图叠加。想找仓库里最接近的参考。

**问 MCP**：仓库里有没有同时带流动纹理（distort）、自发光（emissive）、烟雾（smoke）的材质？

**工具调用**：
```
search_shader(file_pattern="volcano")
search_shader(name="t_distort", kind="Texture")
search_shader(name="t_emissive", kind="Texture")
search_shader(name="t_smoke", kind="Texture")
search_shader(kind="Uniform", file_pattern="volcano")
```

**确认答案**：
- `pbr/pbr_default_volcano.nsf` 正是要找的——一个材质同时有 8 张贴图：
  - `t_smoke1_tex`（L94）+ `t_smoke2_tex`（L108）—— 烟雾贴图 ×2
  - `t_wpo_tex`（L122）—— WPO（世界位置偏移）贴图
  - `t_emissive1_tex`（L136）+ `t_emissive2_tex`（L164）—— 自发光贴图 ×2
  - `t_emissivemask_tex`（L178）—— 自发光遮罩
  - `t_distort_tex`（L150）—— 扰动/流动贴图
  - `t_fmask_tex`（L192）—— 流动遮罩
- 每张贴图都有完整的 uniform 控制组（tilling/offset/speed/clamp/intensity），例如：
  - `u_emissive1_tilling_x/y`、`u_emissive1_speed_x/y`、`u_emissive1_offset_x/y`（自发光 UV 控制）
  - `u_distort1_tilling_x/y`、`u_distort1_speed_x/y`、`u_distort1_u_intensity/v_intensity`（扰动 UV 控制）
- 入口：`vs_main` + `ps_main`（technique TShader），定义在 `shaderlib/forward_shading_transparent.hlsl`

**验证要点**：MCP 应该能从抽象需求（流动+发光+烟雾）定位到具体材质，并列出贴图和 uniform。

---

## Q9：我想做一个物体溶解消失的效果，仓库里有哪些溶解 shader 可以参考？溶解的参数怎么控制？

**场景**：美术要做物体溶解/消散效果（dissolve），想知道仓库里有哪些现成的 dissolve shader，参数怎么调。

**问 MCP**：仓库里有 dissolve 相关的 shader 吗？溶解效果有哪些参数可以控制？

**工具调用**：
```
search_shader(file_pattern="dissolve", kind="Technique")
search_shader(name="dissolve", kind="Uniform")
search_shader(name="Dissolve", kind="Function")
get_code_snippet(function_name="CalcAlphaTestClipDissolve")
```

**确认答案**：
- 溶解材质有 5 个：
  - `sfx/dissolve_void.nsf`（technique=TShader）—— 虚空溶解
  - `pbr/pbr_crystal_dissolve.nsf` —— 水晶溶解
  - `pbr/pbr_normal_dissolve.nsf` —— 普通溶解
  - `pbr/pbr_transparent_dissolve.nsf` —— 透明溶解
  - `sfx/ghost_hologram_dissolve.nsf` —— 幽灵全息溶解
- 溶解参数（uniform）：
  - `dissolve_factor`（float）—— 溶解进度（0-1），控制溶解程度
  - `dissolve_width`（float）—— 溶解边缘宽度
  - `dissolve_edge_color`（float4）—— 溶解边缘颜色（在 flow_water 系列里）
  - `dissolve_intensity`（float）—— 溶解强度
- 溶解核心函数：
  - `CalcAlphaTestClipDissolve`（`pbr/nodes/pbr_diss_nodes.hlsl` L223）—— alpha test 溶解
  - `CalcDissolveOpacityAndNormal`（同文件 L189）—— 算溶解透明度和法线
  - `DissolveChangeColorEffect`（`shaderlib/surface_functions.hlsl` L1002）—— 溶解变色
  - `EmissiveDissolveDistort`（`shaderlib/surface_distort.hlsl` L346）—— 溶解+发光+扰动

**验证要点**：MCP 应该能从"dissolve"模糊词找到多个材质，并列出控制参数和核心函数。

---

## Q10：我想做一个水面材质，要有波纹流动 + Fresnel 反射，仓库里的水面 shader 是怎么实现的？

**场景**：美术要做水面效果，需要波纹流动 + 菲涅尔反射。想看仓库里的水面 shader 怎么实现的，有哪些可调参数。

**问 MCP**：仓库里的水面 shader 有哪些？水面波纹和 Fresnel 是怎么实现的？

**工具调用**：
```
search_shader(file_pattern="water", kind="Technique")
search_shader(name="fresnel", kind="Function")
search_shader(name="flow", kind="Function", file_pattern="water")
get_code_snippet(function_name="ps_main", )  # 需要先搜 pbr_water.nsf 的 node_id
```

**确认答案**：
- 水面 shader 有 20+ 个，分几类：
  - **海面**：`terrain/new_water_sea.nsf`（technique=DeepSeaShader + ShallowSeaShader，深浅海两个 pass）
  - **PBR 水面**：`pbr/pbr_water.nsf`（technique=TShader，ps_main 在 L100）
  - **流动水面**：`sfx/flow_water.nsf` / `flow_water_fluorescer.nsf` / `flow_water_mask.nsf` / `flow_water_mask_rim_diss.nsf`（带溶解边缘）
  - **超级水面**：`sfx/super_flow_water.nsf` / `super_fl_water_build.nsf` / `super_fl_water_scene.nsf`
  - **水波圆环**：`sfx/uber_water_circle.nsf` / `uber_water_flow.nsf`
- Fresnel 相关：
  - `sfx/dissolve_void.nsf` 有 `u_fresnel_brightness`（L76）和 `u_fresnel_size`（L68）—— 菲涅尔亮度和范围
  - `sfx/flow_water_fluorescer.nsf` / `flow_water_mask_rim_diss.nsf` 也有 fresnel 参数
- 水面溶解边缘参数（flow_water 系列）：
  - `dissolve_edge_color`（float4）—— 溶解边缘颜色
  - `dissolve_factor`（float）—— 溶解进度
  - `dissolve_width`（float）—— 边缘宽度

**验证要点**：MCP 应该能从"水面"模糊词找到 20+ 个相关 shader，并按类型分类（海面/PBR/流动/圆环），还能找到 fresnel 相关参数。

---

## 使用方法（两批通用）

1. 启动 MCP server：`py -3 -m shaderbase.mcp_server`
2. 重启 ZCode，确保 MCP 连上（Settings → MCP 看到 shaderbase 是 connected）
3. 开新会话，按 Q1-Q10 顺序问 Agent
4. 对照本文档的"确认答案"验证 Agent 回答是否准确

**第一批（Q1-Q5）**测技术准确性——条件编译、调用链、数据流、死代码。
**第二批（Q6-Q10）**测美术可用性——模糊需求理解、材质定位、参数发现、效果参考。

如果 Agent 回答跟确认答案不一致，把 Agent 的回答贴出来，用于排查是 MCP 工具的问题还是 Agent 理解的问题。
