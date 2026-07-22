# Fixture 选定清单（50 个代表性文件）

选文件原则：每类节点 Top 3 + 各顶层目录至少 1 个 + pbr_rock 三件套。
按"够用就好"原则，每份只验稳定字段（kind/name/line/关键字段），不逐个验细节。

## 选定文件（47 个 + pbr_rock 三件套 = 50）

| # | 路径（相对 shader-source） | 节点总数 | 覆盖类型 |
|---|---|---|---|
| 1 | base/road_specular.nsf | 42 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 2 | billboard/pbr_foliage_billboard.nsf | 18 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 3 | common_cocosui/labeloutline.nsf | 11 | Fn/Struct/Texture/Sampler/Technique |
| 4 | common_pipeline/allocate_voxel_surfel_cluster.nsf | 8 | Fn/Struct/Technique |
| 5 | common_pipeline/compact_cluster_surfel_data.nsf | 10 | Fn/Struct/Technique |
| 6 | common_pipeline/gicommon.hlsl | 53 | Fn/Struct |
| 7 | common_pipeline/makeup_v4_color.nsf | 35 | Fn/Struct/Texture/Sampler/Technique |
| 8 | common_shader/fresnel_noise_transparent_rt_output.nsf | 21 | Fn/Struct/Texture/Sampler/Uniform/Technique×3 |
| 9 | common_shader/skydome_v2_functions.hlsl | 55 | Fn/Texture/Sampler |
| 10 | hlod/hierarchicallod.nsf | 24 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 11 | matcap/nodes/matcap_sand_nodes.hlsl | 46 | Fn/Texture/Sampler/Uniform |
| 12 | meadow/meadow_base_v2_1_billboard.nsf | 26 | Fn/Struct/Uniform/Technique |
| 13 | pbr/nodes/crystal_functions.hlsl | 101 | Fn/Texture/Sampler/Uniform |
| 14 | pbr/nodes/eye_functions.hlsl | 65 | Fn/Texture/Sampler/Uniform |
| 15 | pbr/nodes/hair_functions.hlsl | 63 | Fn/Texture/Sampler/Uniform |
| 16 | pbr/nodes/pbr_bluetide_parameters.hlsl | 54 | Texture/Sampler/Uniform |
| 17 | pbr/nodes/pbr_carrier_parameters.hlsl | 63 | Texture/Sampler/Uniform |
| 18 | pbr/nodes/pbr_monster_parameters.hlsl | 92 | Texture/Sampler/Uniform |
| 19 | pbr/nodes/pbr_monster_va_parameters.hlsl | 126 | Texture/Sampler/Uniform×108 |
| 20 | pbr/nodes/pbr_volcano_parameters .hlsl | 57 | Texture/Sampler/Uniform |
| 21 | pbr/nodes/skin_functions.hlsl | 103 | Fn/Texture/Sampler/Uniform |
| 22 | pbr/pbr_default_volcano.nsf | 81 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 23 | sfx/blend_highlight.nsf | 15 | Fn/Struct/Texture/Sampler/Uniform/Technique×4 |
| 24 | sfx/nodes/pbr_flow_water_parameters.hlsl | 135 | Texture/Sampler/Uniform×113 |
| 25 | sfx/nodes/uber_fx_common_input.hlsl | 133 | Texture/Sampler/Uniform×113 |
| 26 | sfx/nodes/uber_fx_common_multilayer_input.hlsl | 211 | Texture/Sampler/Uniform×183 |
| 27 | sfx/scanning_light_noise.nsf | 63 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 28 | sfx/uber_flow_sparkles.nsf | 87 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 29 | sfx/uber_fx_flowmap.nsf | 125 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 30 | sfx/uber_fx_glitch_2d.nsf | 54 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 31 | sfx/uber_mask_rim_diss.nsf | 15 | Texture/Sampler/Technique×3 |
| 32 | sfx/uber_noise_mask.nsf | 65 | Fn/Struct/Texture/Sampler/Uniform/Technique×2 |
| 33 | sfx/uber_shoal_wave.nsf | 81 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 34 | sfx/uber_water_circle.nsf | 56 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 35 | sfx/uber_water_flow.nsf | 102 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 36 | sfx/uber_water_wave.nsf | 67 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 37 | sfx/wpo_fragment.nsf | 70 | Fn/Struct/Texture/Sampler/Uniform/Technique |
| 38 | shaderlib/builtin_uniforms.hlsl | 104 | CBuffer×4/Fn/Texture/Sampler×48 |
| 39 | shaderlib/ffx_a.fxh | 494 | Function×494 |
| 40 | shaderlib/foliage_anim_functions.hlsl | 73 | Fn/Texture/Sampler/Uniform |
| 41 | shaderlib/function.hlsl | 131 | Function×131 |
| 42 | shaderlib/season_uniforms.hlsl | 55 | Fn/Texture/Sampler/Uniform |
| 43 | shaderlib/shading_models.hlsl | 60 | Fn×52/Struct/Texture/Sampler |
| 44 | shaderlib/surface_functions.hlsl | 84 | Fn/Texture/Sampler/Uniform |
| 45 | shaderlib/vat_bonebase.hlsl | 55 | Fn/Struct/Texture/Sampler/Uniform |
| 46 | terrain/new_water_sea_fishing.nsf | 56 | Fn/Struct/Texture/Sampler/Uniform/Technique×2 |
| 47 | terrain/terrain_diffuse_common.hlsl | 96 | Fn×50/Struct/Texture×21/Sampler×22/Uniform |
| 48 | terrain/terrain_water_common.hlsl | 60 | Fn/Texture/Sampler/Uniform |
| 49 | test/test_svon.nsf | 5 | Fn/Struct/Technique |
| 50 | ui/button_flow_o.nsf | 15 | Fn/Struct/Texture/Sampler/Technique |

## pbr_rock 三件套（已有 fixture，本地教学版）

- `test/fixtures/nodes/pbr_rock_nsf.expected.yaml`
- `test/fixtures/nodes/pbr_rock_nodes.expected.yaml`
- `test/fixtures/nodes/pbr_rock_parameters.expected.yaml`
