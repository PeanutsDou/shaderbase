# Q6-Q10 美术视角测试报告

本文档整理 Q6-Q10 五个美术视角问题的 AI 实际回答、耗时，以及与确认答案的对比分析。

---

## Q6：我要做一个会随季节变色的草地材质，仓库里有现成的参考吗？季节变色是怎么实现的？

**耗时**：1 分 38 秒

### AI 回答

非常好，仓库里有完整的草地 Shader 和季节变色系统！我给你整理了详细的分析：

---

#### 📂 仓库里的草地 Shader

有 **5 个草地变体**，都在 `base/` 目录下：

| 文件 | 说明 |
|---|---|
| `base/animated_grass.nsf` | 基础草地，有季节变色 + 地形混色 |
| `base/animated_grass_noseason.nsf` | 草地（无季节） |
| `base/animated_grass_specular_flower.nsf` | 草地 + 高光 + 花朵（Alpha通道区分草/花） |
| `base/animated_grass_specular_mask.nsf` | 草地 + 高光 + Stencil Mask |
| `base/animated_grass_specular_mask_billboard.nsf` | 草地 Billboard 版本 |

还有专门的 **草地的风吹动画函数**（`shaderlib/foliage_anim_functions.hlsl`）：

- `FoliageAnim_Grass_Branch()` — 草茎（茎秆）摆动，高低频叠加 + 风力噪声图
- `FoliageAnim_Grass_Leaf()` — 草叶摆动
- `SimpleGrassWind()` — 简化版草地风
- `CalcMeadowAnimWPO()` — Meadow 系统的风动画

---

#### 🌈 季节变色是怎么控制的

**1. 核心控制：`u_season_factors`（half4 引擎驱动）**

```
u_season_factors.r (x) = 春天权重
u_season_factors.g (y) = 夏天权重
u_season_factors.b (z) = 秋天权重
u_season_factors.a (w) = 冬天权重
```

引擎在运行时按季节渐变这四个值，shader 里直接拿来 lerp。

**2. 草地季节变色入口调用链**

```
animated_grass.nsf
  └─ PixelNodesBasedGraph()
       ├─ 采样 DiffuseMap → base_color
       ├─ 混地形颜色（可选）
       └─ GetSeasonColorMeadow(uv0, flower_mask, MaterialInputs)
            ├─ GetFoliageColorSimple(season_factors, base_color, 2.0)
            │    ← 春/秋/冬染色 + 去色（纯颜色数学，不依赖额外贴图）
            └─ 冬天雪覆盖（season_factors.w > 0）
                 ├─ 按 UV.y 方向生成 snow mask
                 ├─ customSmoothStep 控制 snowmask 范围
                 └─ lerp(base_color, brightness + u_snowmask_color.rgb, mask)
```

**3. `GetFoliageColorSimple()` 染色公式（适用于草地）**

```hlsl
half4 dye_color = autumn_color * season_factors.b + winter_color * season_factors.a;
half3 dis_color = lerp(base_color, length(base_color), dye_color.w);   // 去色
leaves_color = dye_color.xyz * dis_color + base_color * (season_factors.r + season_factors.g);
```

> 草地的染色 **不采样额外的季节贴图**，直接用 `u_autumn_color` / `u_winter_color` 颜色常量 + `season_factors` 权重做数学混合。而更精细的 foliage（树）会采样季节贴图。

**4. 可调节的 Uniform 参数**

| Uniform | 默认值 | 作用 |
|---|---|---|
| `u_season_intentisy` | 1.0 | 季节混合强度（0=无效果） |
| `u_seasontex_range` | 4.5 | 季节混合范围（法线角度衰减） |
| `u_uncovered_scale` | 1.0 | 全局开关，0=不受季节影响（室内物体） |
| `u_autumn_color` | — | 秋天染色颜色 |
| `u_winter_color` | — | 冬天染色颜色 |
| `u_snowmask_color` | (0.25,0.28,0.32,0.5) | 雪颜色 + alpha 强度补偿 |
| `u_snowmask_scale` / `u_snowmask_scale2` | 0.65 | 雪遮罩细节范围 min/max |
| `u_snowmask_scale_max` | 0.4 | 雪遮罩范围上限 |
| `maxangle` | 0.5 | 季节混合边缘(法线角度) |

---

#### 🔧 美术可以怎么调

1. **换色**：调 `u_autumn_color` / `u_winter_color` 的 RGB
2. **强度**：调 `u_season_intentisy`（0-20），0 关闭季节效果
3. **雪范围**：调 `u_snowmask_scale` / `u_snowmask_scale2` 控制雪从草的哪个高度开始覆盖
4. **雪颜色**：调 `u_snowmask_color` RGB，alpha 作为强度补偿
5. **开关**：`u_uncovered_scale = 0` 关闭该物体的季节影响

---

#### 📌 最直接的参考素材

如果要给美术做 demo，直接看 `base/animated_grass.nsf` 的 `PixelNodesBasedGraph()` 函数（第141行），以及 `shaderlib/season_uniforms.hlsl` 里的 `GetSeasonColorMeadow()`（第791行）和 `GetFoliageColorSimple()`（第564行）—— 这三个函数组成了草地季节变色的完整流程。

---

### 与确认答案对比

| 对比项 | 确认答案 | AI 回答 | 一致性 |
|---|---|---|---|
| 草地材质 5 个 | ✅ 5 个 | ✅ 5 个，名称全对 | ✅ 一致 |
| GetSeasonColorMeadow 位置 | season_uniforms.hlsl L791 | ✅ L791 | ✅ 一致 |
| GetFoliageColorSimple 位置 | season_uniforms.hlsl L564 | ✅ L564 | ✅ 一致 |
| customSmoothStep 位置 | function.hlsl L1136 | ✅ 提到但未标行号 | ✅ 一致 |
| BlendSeasonColor | terrain_diffuse_common.hlsl L542 | ❌ 未提及 | ⚠️ 遗漏 |
| SEASON_SUPPORT 开关 | "10 个文件"（确认答案有误，实际 38） | 未提具体数量 | — |
| 调用链 | 列了 5 个函数 | ✅ 画了完整调用链图 | ✅ 超预期 |
| uniform 参数表 | 未详细列 | ✅ 列了 10 个参数 + 默认值 + 作用 | ✅ 超预期 |
| 美术调参建议 | 无 | ✅ 给了 5 条调参建议 | ✅ 超预期 |

**评价**：⭐⭐⭐⭐⭐ 超预期。AI 不仅回答了"有什么"，还补充了调用链图、染色公式、uniform 参数表和美术调参建议。比确认答案丰富得多。

---

## Q7：我要做一个需要 2 套 UV 控制的材质（比如贴花同时控制纹理和遮罩），仓库里哪些 shader 支持双 UV？

**耗时**：1 分 31 秒

### AI 回答

综合查询结果，仓库中双 UV 的使用情况整理如下：

---

#### 🔴 最相关：直接在材质层面用双 UV 做贴花/印花

**1. `pbr_silk_flow_dye` / `pbr_silk_flow_dye_transparent`（丝绸染色材质）**

文件：`pbr/nodes/pbr_silk_flow_dye_nodes.hlsl`

| 语义 | 字段 | 用途 |
|---|---|---|
| `TEXCOORD0` | `texcoord0`（→ `MaterialParameters.uv0`） | 基础纹理（固有色、法线、Param） |
| `TEXCOORD1` | `texcoord1`（`PS_INPUT.texcoord1`） | **2U 印花图案 UV**（`#if USE_2U_MODE` 开关控制，调 `UseSecondUVPattern()` 做图案贴花） |
| `TEXCOORD2` | `texcoord2`（`PS_INPUT.texcoord2`） | 裙子流动遮罩 UV（mask1/mask2 的时间动画 + 扰动采样，用于染色区域控制） |

> **这就是你要的贴花双 UV 模式的参考实现！** 通过 `USE_2U_MODE` 宏开关（`USE_2U_PATTERN` / `USE_2_COLOR`）控制 TEXCOORD1 是否启用做图案遮罩，TEXCOORD2 固定用于流动染色遮罩。

---

**2. `pbr_h_2u` / `pbr_h_2u_login`（头发双 UV 材质）**

文件：`pbr/nodes/pbr_h_2u_nodes.hlsl`

| 语义 | 字段 | 用途 |
|---|---|---|
| `TEXCOORD0` | `texcoord0`（→ `MaterialParameters.uv0`） | 基础纹理+发丝法线+StrandsMap |
| `TEXCOORD1` | `texcoord1` | **AO 贴图 UV**（`#if HAS_AMBIENT_OCCLUSION` 时 `MaterialParameters.uv1 = input.texcoord1.xy`，传给 `SampleParamTextureUV1AO`） |
| `TEXCOORD2` | — | 无（VS_INPUT 没有 texcoord2） |

> 名字带 "2u" 表明就是双 UV 设计，TEXCOORD1 用于 AO 纹理采样。

---

#### 🟡 基础材质层有双 UV 传递，可用于贴花

**3. `pbr_fabric`（布料 PBR）**

| 语义 | 字段 |
|---|---|
| `TEXCOORD0` | `texcoord0` |
| `TEXCOORD2` | `texcoord2`（VS_INPUT 声明） |

**4. `pbr_default` / `pbr_transparent` / `pbr_transparent_dissolve`（通用 PBR）**

| 语义 | 字段 |
|---|---|
| `TEXCOORD0` | `texcoord0` |
| `TEXCOORD2` | `texcoord2`（VS_INPUT 声明，float4） |

**5. `pbr_foliage`（foliage PBR）**

| 语义 | 字段 |
|---|---|
| `TEXCOORD0` | `texcoord0` |
| `TEXCOORD2` | `texcoord2`（VS_INPUT 有） |

**6. `pbr_vat_bonebase` / `pbr_vat_bonebase_transparent`（VAT 动画）**

| 语义 | PS_INPUT 字段 | 用途 |
|---|---|---|
| `TEXCOORD0` | `texcoord0` | 基础纹理 |
| `TEXCOORD10` | `texcoord2` | VAT 数据 |
| `TEXCOORD11` | `vertex_color` | 顶点色 |

**7. `road_specular`（道路高光）**

- VS_INPUT: `texcoord0` (TEXCOORD0), `texcoord1` (TEXCOORD1, float4)
- PS_INPUT: `texcoord0` → 基础纹理, `texcoord1` → 额外 UV

**8. `terrain` 地形系统**

- PS_INPUT: `texcoord0` (TEXCOORD0) + `texcoord1` (TEXCOORD1, float4) 做混杂纹理

---

#### 🟢 SFX 特效类的双 UV（偏后处理/流动而非贴花）

**9. `furniture_common_sfx`（家具通用特效）**

- `uv_offset0` (TEXCOORD1) — 偏移 UV 做纹理动画
- `uv_raw` (TEXCOORD2) — 原始 UV 做扰动采样

**10. `uber_fx_common` / `uber_fx_common_multilayer` 等**

- `texcoord1` (TEXCOORD1) — 带偏移的 UV
- `texcoord2` (TEXCOORD2) — 第二套 UV（另一层纹理或扰动）

**11. `flow_water` / `super_flow_water` 等水流特效**

- `uv_offset0` (TEXCOORD1) — 流动偏移 UV
- `water_uv` / `uv_raw` (TEXCOORD2) — 水纹 UV

---

#### 总结：贴花双 UV 建议参考

| 需求 | 最佳参考 |
|---|---|
| **TEXCOORD0 基础纹理 + TEXCOORD1 遮罩/印花** | **`pbr_silk_flow_dye`**（`USE_2U_MODE` 宏 + `UseSecondUVPattern()`） |
| **TEXCOORD0 + TEXCOORD1 做 AO/Param 采样** | **`pbr_h_2u`**（`HAS_AMBIENT_OCCLUSION` + `SampleParamTextureUV1AO`） |
| **三套 UV（TEXCOORD0 + 1 + 2）** | **`pbr_silk_flow_dye`**（TEXCOORD2 额外用于遮罩流动动画） |
| **已有 texcoord2 声明但未利用** | **`pbr_default`、`pbr_fabric`、`pbr_foliage`** — 可直接在 PixelNodesBasedGraph 里加 TEXCOORD2 采样逻辑 |

最推荐的参考是 **`pbr_silk_flow_dye_nodes.hlsl`**，它完整实现了 `USE_2U_MODE` 宏开关控制 TEXCOORD1 做贴花/印花的流程，且兼容 TEXCOORD2 做额外遮罩，和你们的需求基本一致。

---

### 与确认答案对比

| 对比项 | 确认答案 | AI 回答 | 一致性 |
|---|---|---|---|
| 双 UV .nsf 数量 | 88 个 | 未给总数，列了 11 个案例 | ⚠️ 未给总数 |
| decal 系列 | ✅ 列了 6 个 | ❌ 未提 decal 系列 | ⚠️ 遗漏 |
| road_specular | ✅ 在列表 | ✅ 列了（#7） | ✅ 一致 |
| common_bake | ✅ 在列表 | ❌ 未提 | ⚠️ 遗漏 |
| TEXCOORD2 语义重载 | ✅ 提到 | ✅ 按用途分类（world_position/UV/扰动等）| ✅ 一致 |
| 推荐参考 | 无推荐 | ✅ 推荐 pbr_silk_flow_dye + 对比表 | ✅ 超预期 |
| pbr_silk_flow_dye | 未提及 | ✅ 深入分析 USE_2U_MODE 宏 | ✅ 超预期 |
| pbr_h_2u | 未提及 | ✅ 发现头发双 UV 材质 | ✅ 超预期 |

**评价**：⭐⭐⭐⭐ 良好。AI 没给总数（88 个）且漏了 decal 系列，但它发现了 `pbr_silk_flow_dye`（丝绸染色双 UV）和 `pbr_h_2u`（头发双 UV）这两个确认答案没有的高价值案例，并给了推荐对比表。对美术的实用价值很高。

---

## Q8：我想做一个火山岩浆材质，要有流动纹理 + 自发光 + 烟雾效果，仓库里有类似的多层纹理材质吗？

**耗时**：48 秒

### AI 回答

现在找到了完整的参考信息。以下是仓库分析结果：

---

#### 仓库中同时带 流动纹理 + 自发光 + 烟雾 的材质

找到的最接近参考是 **`pbr_default_volcano.nsf`**（火山材质），它同时具备你需要的三个要素：

| 要素 | 实现 | 文件 |
|---|---|---|
| **Distort（流动纹理）** | `t_distort_tex1` - 采样 RG 通道做 UV 偏移，带 Panner 动画 + 强度控制 | `pbr/nodes/pbr_volcano_nodes.hlsl` |
| **Emissive（自发光）** | `t_emmisve_tex1` - UV 被 distort 扰动后采样，经 power + intensity + mask 控制 | 同上 |
| **Smoke（烟雾）** | **两层烟雾贴图** `t_smoke1_tex` / `t_smoke2_tex`，各有独立 UV 动画（Tiling/Offset/Speed） | `pbr/pbr_default_volcano.nsf` 的 uniform 声明 |
| **多层遮罩** | `t_mask01` 遮罩层，与 distort 共享偏移，控制 emissive 区域 | `pbr/nodes/pbr_volcano_nodes.hlsl` |

#### 🔥 另一个相关参考：`pbr_lava_nodes.hlsl`（岩浆材质）

这个**没有烟雾**，但流动和自发光实现更丰富：

- **FlowMap 驱动流动**：用 RG 通道存方向向量，支持 UV 动画
- **双层岩浆**：高温区域 + 冷却区域，用 Mask + Height Blending + Noise Blending 混合
- **自发光**：基于 height_mask_with_noise 控制 emissive 区域强度
- **Z-Mask**：基于法线方向做地形高度混合

---

#### 建议参考路径

**如果你是美术想抄作业**，直接看 `pbr_default_volcano.nsf`：

1. **Distort 层** ← `CalcVolcanoEmissive()` 里的 `t_distort_tex1` 采样做 UV 偏移
2. **Emissive 层** ← 扰动后的 UV 采 `t_emmisve_tex1`，经 mask01 裁剪
3. **Smoke 层** ← `t_smoke1_tex` / `t_smoke2_tex` 两层烟雾，带独立 UV 动画参数

**如果你想要更高级的 FlowMap 流动**（而不是简单的 distort noise），参考 `pbr_lava_nodes.hlsl` 里的 `CalFlowMap()`。

这两个材质结合起来，火山岩浆效果的三个要素 + 多层贴图叠放就都齐了。

---

### 与确认答案对比

| 对比项 | 确认答案 | AI 回答 | 一致性 |
|---|---|---|---|
| 目标材质 | pbr_default_volcano.nsf | ✅ pbr_default_volcano.nsf | ✅ 一致 |
| 8 张贴图 | 列了全部 8 张 + 行号 | ✅ 列了 smoke×2/emissive/distort/mask | ⚠️ 没全列（漏 wpo/emissivemask/fmask）|
| 贴图行号 | 全部标注 | ❌ 未标行号 | ⚠️ 缺行号 |
| uniform 参数 | 列了 tilling/offset/speed/clamp | ✅ 提到 Tiling/Offset/Speed | ✅ 一致 |
| 入口函数 | forward_shading_transparent.hlsl | ❌ 未提 | ⚠️ 遗漏 |
| pbr_lava | 未提及 | ✅ 发现了 pbr_lava_nodes.hlsl | ✅ 超预期 |
| CalcVolcanoEmissive | 未提及 | ✅ 提到函数名 | ✅ 超预期 |
| 美术建议 | 无 | ✅ 给了"抄作业"路径 | ✅ 超预期 |

**评价**：⭐⭐⭐⭐ 良好。AI 准确定位了 pbr_default_volcano.nsf，但贴图列表不够完整（漏了 3 张），行号也没标。亮点是发现了确认答案没有的 `pbr_lava_nodes.hlsl`（岩浆材质），并给了 FlowMap 的高级实现建议。

---

## Q9：我想做一个物体溶解消失的效果，仓库里有哪些溶解 shader 可以参考？溶解的参数怎么控制？

**耗时**：未单独测试（AI 答案与 Q8 合并返回）

### AI 回答

*注：Q9 的 AI 测试答案未在文档中单独记录，可能是因为 Agent 在回答 Q8 时已覆盖了部分溶解相关内容。以下为确认答案的验证结果。*

### 与确认答案对比（基于 MCP 实测验证）

| 对比项 | 确认答案 | MCP 实测 | 一致性 |
|---|---|---|---|
| 溶解材质数量 | "5 个" | **实际 7 个**（漏了 ui/dissolve.nsf 和 ui/dissolve_ui.nsf） | ⚠️ 确认答案偏少 |
| dissolve_factor | ✅ float | ✅ 在 dissolve_void/flow_water 系列 | ✅ 一致 |
| dissolve_width | ✅ float | ✅ 在 dissolve_void/flow_water 系列 | ✅ 一致 |
| dissolve_edge_color | ✅ float4 | ✅ 在 flow_water_fluorescer/mask_rim_diss | ✅ 一致 |
| dissolve_intensity | ✅ float | ✅ 在 flow_water_fluorescer/mask_rim_diss | ✅ 一致 |
| CalcAlphaTestClipDissolve | pbr_diss_nodes.hlsl L223 | ✅ L223 | ✅ 一致 |
| CalcDissolveOpacityAndNormal | 同文件 L189 | ✅ L189 | ✅ 一致 |
| DissolveChangeColorEffect | surface_functions.hlsl L1002 | ✅ L1002 | ✅ 一致 |
| EmissiveDissolveDistort | surface_distort.hlsl L346 | ✅ L346 | ✅ 一致 |

**评价**：确认答案准确度高，但溶解材质数量偏保守（5→7）。MCP 实际返回的 dissolve 函数更丰富（还有 SampleDissolveTexture/GetDissolveUV/GetDissolveValue 等）。

---

## Q10：我想做一个水面材质，要有波纹流动 + Fresnel 反射，仓库里的水面 shader 是怎么实现的？

**耗时**：1 分 5 秒

### AI 回答

以下是对仓库中水面 Shader 的完整调研结果：

---

#### 仓库里的水面 Shader

仓库中有 **三套** 主要水面 Shader 体系：

**1. 地形水面（terrain water） — `terrain/new_terrain_water*.nsf`**

用于场景地形上的水面（河流、湖泊），是主力水面方案，有三个变体：

| 文件 | 说明 |
|---|---|
| `terrain/new_terrain_water.nsf` | 基础地形水面 |
| `terrain/new_terrain_water_env.nsf` | 带环境反射的水面 |
| `terrain/new_terrain_water_sea_blend.nsf` | 海水混合地形水面 |

**2. SFX 流动水面 — `sfx/super_fl_water_*.nsf`**

用于特效道具类水面（瀑布、水池装饰），有 PBR 材质支持：

| 文件 | 说明 |
|---|---|
| `sfx/super_fl_water_scene.nsf` | 场景流动水面 |
| `sfx/super_fl_water_build.nsf` | 建筑物件流动水面 |

**3. 河道水面 — `terrain/terrain_water_common.hlsl`**

老的/official 河道水面方案，功能最全，参数最多。

---

#### 波纹流动的实现方式

**地形水面（new_terrain_water）**

```hlsl
// 读取 Noise 贴图，双 UV 叠加扰动
float4 offset1 = NoiseMap.Sample(s_noise_map, input.water_uv.xy) * 2.0f - 1.0f;
float4 offset2 = NoiseMap.Sample(s_noise_map, input.water_uv.zw) * 2.0f - 1.0f;
MaterialParameters.uv1 = offset1.xy + offset2.xy;  // 叠加扰动
```

核心思路：两套 UV 各自采样 Noise 贴图 → 叠加融合 → 对颜色/法线做偏移，模拟水流波纹。

**SFX 流动水面（super_fl_water）**

```hlsl
float2 WaterFxUv(float2 uv, float2 tiling, float2 offset, float2 speed)
{
    return uv * tiling + u_frame_time_radian * speed + offset;
}
```

- UV 随时间（`u_frame_time_radian`）沿 speed 方向平移 → UV 动画流动
- 两张 Noise 贴图分别沿相反方向流动叠加，产生复杂波纹

**河道水面（terrain_water_common）**

通过时间计算动画周期内的偏移量来驱动法线/颜色扰动。参数包括 `animation_length`（水流循环周期，默认 3.0s）、`animation_strength`（水流力度，默认 0.2）、`base_speed_x/y`（扰动速度）。

---

#### Fresnel（菲涅尔）反射的实现

**地形水面：标准 Schlick 近似**

```hlsl
half GetFresnelCoef(float3 n, float3 v)
{
    half fade = 1.2;     // 菲涅尔生效衰减距离
    half F0 = 0.0204;    // 水面 F0（约 0.02）
    half Fc = saturate(1.0 + fade * dot(n, v));
    half Fc2 = Fc * Fc;
    Fc = Fc2 * Fc2 * Fc;  // 等价于 (1-v·n)^5 的近似
    return F0 + (1.0 - F0) * Fc;
}
```

- 用 **几何法线**（world_normal）而非扰动后的法线计算，避免菲涅尔边缘闪烁
- F0 = 0.0204（水的典型值）

**SFX 水面：菲涅尔遮罩不透明度**

```hlsl
half WaterFxCalcFresnelNoV(PixelMaterialParameters MaterialParameters)
{
    half fresnel_nov = saturate(abs(dot(MaterialParameters.world_normal,
                                        MaterialParameters.camera_vector)));
    return fresnel_nov;
}

half WaterFxApplyFresnelMaskOpacity(half base_opacity, half fresnel)
{
    return base_opacity * (1.0h - saturate(fresnel));
}
```

- 视线与法线夹角越小（看向水面边缘）→ fresnel ≈ 0 → 不透明度降低 → 边缘透出更多底下颜色

**河道水面**

`FresnelWeaken`（默认 0.2），控制菲涅尔衰减强度。

---

#### 可调参数汇总

**地形水面（new_terrain_water）参数**

| 参数 | 分组 | 默认值 | 说明 |
|---|---|---|---|
| `u_flatten_normal` | Normal Map | 0.0 | 法线压平程度（0~1） |
| `u_noisetex_tilling` | 扰动 | 0.5 | 扰动噪声平铺数 |
| `u_noisetex_speed` | 扰动 | 0.1 | 扰动移动速度 |
| `u_diffuse_noise` | 扰动 | 0.01 | Diffuse 扰动程度 |
| `u_wave_noise` | 扰动 | 0.2 | Wave 扰动程度 |
| `u_wavetex_tilling` | 波纹 | 0.5 | 波纹贴图平铺数 |
| `u_wavetex_speed` | 波纹 | -0.1 | 波纹贴图移动速度 |

**SFX 流动水面（super_fl_water）参数**

| 参数 | 分组 | 默认值 | 说明 |
|---|---|---|---|
| `emissive_color` | — | white | 自发光颜色 |
| `change_color_bright` | — | 1.5 | 自发光强度（0~20） |
| `water_height` | 扰动 | 0.2 | 水池高度 |
| `u_uv_info` | — | (1,1,0,0) | UV 缩放和偏移 |
| `u_speed` | — | 0.0 | U 方向流动速度 |
| `v_speed` | — | 0.1 | V 方向流动速度 |
| `u_seafog_up` | 海底雾 | 0.0 | 海底雾上限 |
| `u_seafog_down` | 海底雾 | -20.0 | 海底雾下限 |
| `u_seafog_upcolor` | 海底雾 | (0, 0.6, 0.63, 0.4) | 海底雾上限颜色 |
| `u_seafog_downcolor` | 海底雾 | (0.008, 0.27, 0.63, 0.6) | 海底雾下限颜色 |

**河道水面（terrain_water_common）参数**

| 参数 | 分组 | 默认值 | 说明 |
|---|---|---|---|
| `u_deep_color` | Color | 深蓝色 | 水底深色 |
| `u_rim_color` | Color | (0.1,0.2,0.3,1) | 边缘颜色 |
| `DepthPower` | Depth | 0.5 | 深度衰减 Power |
| `u_depth_param` | 透明/深度 | (10,1,0.5,0) | 深水距离&透明度&对比度 |
| `FoamColor` | Foam | white | 泡沫颜色 |
| `FresnelWeaken` | Waves | 0.2 | 菲涅尔衰减强度 |
| `NormalUvScale` | Waves | 1.0 | 法线 UV 缩放 |
| `animation_length` | Waves | 3.0 | 水流循环周期 |
| `animation_strength` | Waves | 0.2 | 水流力度 |
| `base_speed_x` | Waves | 0.05 | 扰动速度 X |
| `base_speed_y` | Waves | 0.05 | 扰动速度 Y |
| `sub_wave_scale` | Waves | 1.0 | 扰动 UV Scale |

---

#### 搭建建议

**美术如果要自己做水面效果**，最推荐的切入点是：

1. **先看 `terrain/new_terrain_water.nsf`** — 最标准的地形水面，调 `u_flatten_normal`（法线强度）、`u_noisetex_speed`（流动速度）、`u_wavetex_tilling`（波纹密度）就能快速出效果
2. **Fresnel 直接用 `GetFresnelCoef(n, v)`** — 水面 F0=0.0204，调 `fade` 参数控制 Fresnel 生效距离
3. **SSS 效果** 用 `WaterCalcSSS(vertexNormal, pixelNormal, V)` — 给水面增加次表面散射质感
4. **如果是道具/特效水面** 用 `sfx/super_fl_water_scene.nsf`，参数更少更直观

---

### 与确认答案对比

| 对比项 | 确认答案 | AI 回答 | 一致性 |
|---|---|---|---|
| 水面 shader 数量 | "20+"（实际 30） | "三套体系"（未给总数） | ⚠️ 都偏保守 |
| 海面 DeepSeaShader+ShallowSeaShader | ✅ | ❌ 未提海面 | ⚠️ 遗漏 |
| PBR 水面 pbr_water.nsf | ✅ | ❌ 未提 | ⚠️ 遗漏 |
| 流动水面 flow_water 系列 | ✅ 4 个 | ✅ 提到 super_fl_water | ⚠️ 不同系列 |
| 超级水面 super_flow_water | ✅ 3 个 | ✅ 2 个 | ⚠️ 少了 1 个 |
| 水波圆环 uber_water_circle | ✅ | ❌ 未提 | ⚠️ 遗漏 |
| Fresnel 实现方式 | 只列了 uniform 参数 | ✅ 给了完整 Schlick 公式 + 代码 | ✅ 超预期 |
| Fresnel 参数 | u_fresnel_brightness/size | ✅ FresnelWeaken + 代码 | ✅ 一致 |
| 可调参数表 | 未列 | ✅ 三套水面各列了完整参数表 | ✅ 超预期 |
| 搭建建议 | 无 | ✅ 4 条建议 | ✅ 超预期 |
| 波纹流动实现 | 未提 | ✅ 给了三套实现 + 代码 | ✅ 超预期 |
| SSS 效果 | 未提 | ✅ 提到 WaterCalcSSS | ✅ 超预期 |

**评价**：⭐⭐⭐⭐ 良好。AI 漏了海面（DeepSeaShader/ShallowSeaShader）和 PBR 水面，但它的 Fresnel 实现分析（Schlick 公式 + 代码）、波纹流动实现（三套方案 + 代码）、可调参数表（三套水面完整列出）和搭建建议都远超确认答案。对美术的实用价值很高。

---

## 总结

| # | 问题 | 耗时 | 评价 | 核心亮点 | 核心不足 |
|---|---|---|---|---|---|
| Q6 | 草地季节变色 | 1分38秒 | ⭐⭐⭐⭐⭐ 超预期 | 调用链图 + 染色公式 + 10 个 uniform 参数表 + 美术调参建议 | 漏了 BlendSeasonColor |
| Q7 | 双 UV 材质 | 1分31秒 | ⭐⭐⭐⭐ 良好 | 发现 pbr_silk_flow_dye（USE_2U_MODE）+ pbr_h_2u + 推荐对比表 | 没给总数（88 个），漏了 decal 系列 |
| Q8 | 火山岩浆 | 48秒 | ⭐⭐⭐⭐ 良好 | 发现 pbr_lava_nodes.hlsl + FlowMap 建议 + "抄作业"路径 | 贴图没列全（漏 3 张），缺行号 |
| Q9 | 溶解消失 | 未单独测试 | — | — | 确认答案材质数偏少（5→7） |
| Q10 | 水面波纹+Fresnel | 1分5秒 | ⭐⭐⭐⭐ 良好 | Fresnel Schlick 公式 + 三套波纹实现 + 完整参数表 + 搭建建议 | 漏了海面和 PBR 水面 |

**整体评价**：AI 在美术视角的测试中表现优秀。它不仅能从模糊需求（"我要做草地/双UV/火山/溶解/水面"）定位到具体 shader，还能结合源码给出调用链、实现公式、参数表和调参建议——这些是确认答案里没有的。主要的不足是偶尔遗漏部分文件（decal 系列/海面），但这不影响对美术的实用价值。
