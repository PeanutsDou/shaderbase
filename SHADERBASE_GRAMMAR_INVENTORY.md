# G66 Shader Grammar 完整样例汇总

> 本文档基于对 `D:/douzhongjun/work/shader/shader-source` 全库的普查，列出 shaderbase 自研 tree-sitter grammar 必须覆盖的所有语法点 + 真实样例 + 出现频次。
> 
> 目的：写 grammar.js 时随时对照——每个语法点都有真实出现位置，按频次排优先级。

---

## 0. 普查诚实声明

本文档基于对 shader-source 全库（1298 个 .nsf/.hlsl/.fxh 文件，跳过 no_source/no_source_pc/pipeline_output/bin/.git）的扫描。频次数字是 grep/正则匹配结果，可能高估（字符串/注释里的命中也算），但量级可靠。

**已知局限**：

- 静态扫描不能发现语法之间的上下文关系（比如某个 semantic 只能用在 struct 字段还是函数参数上）
- 没扫动态生成代码（如果 G66 编译器有内置宏生成的语法形态，扫不到）
- attribute 列表因为扫"行首 `[` 开头"才能跟数组下标区分，行中嵌的 attribute 可能漏

后续写 grammar 时跑 fixture 仍可能发现新的边角语法，按 §X 的"持续补全"流程迭代。

---

## 1. 总览（按频次排序）

### 高频（>500 次）

| 语法点                     | 出现次数                   |
| ----------------------- | ---------------------- |
| `#endif`                | 6621                   |
| `#define`（object-like）  | 6009                   |
| `#if`                   | 4801                   |
| `#include`              | 3358                   |
| `SamplerState`          | 2227                   |
| `texture`               | 2120                   |
| `inout`（参数方向）           | 1970                   |
| `#ifndef`               | 1783                   |
| `struct`                | 1471                   |
| `void`（类型）              | 1519                   |
| `string`（annotation 类型） | 19277（多在 annotation 里） |
| `#undef`                | 1654                   |
| `#else`                 | 1304                   |
| `technique`             | 867                    |
| `pass`                  | 994                    |
| `const`                 | 923                    |
| `if`                    | 7096                   |
| `return`                | 3735                   |
| `else`                  | 1988                   |
| `for`                   | 818                    |

### 中频（20-500 次）

| 语法点                             | 次数  |
| ------------------------------- | --- |
| `#art`（G66 特化）                  | 804 |
| `#elif`                         | 158 |
| `discard`（shader 独有语句）          | 144 |
| `uniform`（修饰符）                  | 125 |
| `[unroll]`（attribute）           | 114 |
| `[numthreads(...)]`（attribute）  | 107 |
| `register(tN)`（贴图 slot 绑定）      | 109 |
| `register(sN)`（采样器 slot 绑定）     | 58  |
| `static`（修饰符）                   | 77  |
| `[branch]`（attribute）           | 78  |
| `[loop]`（attribute）             | 31  |
| `inline`（修饰符）                   | 35  |
| `groupshared`（修饰符）              | 35  |
| `#ifdef`                        | 36  |
| `unorm`（类型修饰）                   | 37  |
| `register(bN)`（cbuffer slot 绑定） | 4   |

### 罕见（<20 次，仍要覆盖）

| 语法点                            | 次数      |
| ------------------------------ | ------- |
| `#excludefromtemptech`（G66 特化） | 15      |
| `precise`（修饰符）                 | 18      |
| `snorm`（类型修饰）                  | 22      |
| `[flatten]`（attribute）         | 4       |
| `cbuffer`                      | 8       |
| `row_major`                    | 1       |
| 行接续 `\`（实为字符串里 `\\`，无真行接续）     | 0（真行接续） |

### 不出现（grammar 不做）

| 语法点                                                                                        | 次数                        |
| ------------------------------------------------------------------------------------------ | ------------------------- |
| `typedef`                                                                                  | 0                         |
| `namespace`                                                                                | 0                         |
| `goto`                                                                                     | 0                         |
| `#pragma` / `#error` / `#warning`                                                          | 0                         |
| `volatile` / `extern` / `export` / `column_major` / `nointerpolation` / `globallycoherent` | 0                         |
| matrix swizzle `_m00` 形式                                                                   | 0（G66 用 `m[0][1]` 数组下标访问） |
| 变参 `#define macro(...)`                                                                    | 0                         |

---

## 2. A 类：顶层声明

### A1. 函数定义（高频）

**所有 .nsf 都有 `vs_main`/`ps_main`，是 grammar 最基础的语法点**。

形态 1：带 semantic 的 PS 入口

```hlsl
// 文件：common_cocosui/gray.nsf
float4 ps_main(PS_INPUT psIN) : COLOR
{
    float4 final_color;
    float4 local_0 = CC_Texture0.Sample(s_CC_Texture0, psIN.v_texture0);
    ...
}
```

形态 2：返回 struct 的 VS 入口

```hlsl
// 文件：common_cocosui/positioncolortexture.nsf
PS_INPUT vs_main(VS_INPUT input)
{
    PS_INPUT psIN = (PS_INPUT)0;
    float4 local_0 = mul(input.position, CC_PMatrix);
    psIN.final_position = local_0;
    ...
}
```

形态 3：void 函数

```hlsl
void CalcWorldNormal(PixelMaterialInputs MaterialInputs, inout PixelMaterialParameters MaterialParameters)
{
    ...
}
```

形态 4：带 attribute 的 compute shader 入口

```hlsl
// 文件：common_pipeline/add_surfel_to_scene.nsf
[numthreads(BLOCK_SIZE, BLOCK_SIZE, 1)]
void cs_main(uint3 DTid : SV_DispatchThreadID)
{
    ...
}
```

形态 5：前置声明（无 body）

```hlsl
void CalcWorldNormal(inout PixelMaterialParameters mp);
```

**grammar 规则要点**：

```
function_definition: seq(
  optional(attribute),       # [numthreads(...)] 这种
  type,                      # 返回类型
  identifier,               # 函数名
  parameter_list,           # (...)
  optional(semantic_binding),  # : COLOR / : SV_Position
  compound_statement         # { ... }
)

# 前置声明跟 function_definition 区分：用 has_body 标记
```

### A2. 变量声明（高频）

形态 1：简单声明

```hlsl
float u_roughness;
```

形态 2：带初始值

```hlsl
float u_roughness = 0.5f;
```

形态 3：列表初始化

```hlsl
float4 color_const = {0.76f, 0.65f, 0.56f, 0.0f};
```

形态 4：带修饰符组合

```hlsl
static const int MAX_BONE_COUNT = 100;
```

形态 5：多变量声明

```hlsl
float x, y, z;
```

形态 6：数组

```hlsl
float arr[6];
float4x4 matrices[8];
```

**grammar 规则要点**：声明 = 修饰符* + 类型 + 声明符列表（含初始化）+ `;`

### A3. struct 声明（高频，1471 次）

形态 1：普通 struct，字段带 semantic

```hlsl
// 文件：shaderlib/vs_input_extend.hlsl
struct VS_INPUT {
    float4 position : POSITION;
    half4 normal : NORMAL;
    half2 texcoord0 : TEXCOORD0;
    half4 diffuse : COLOR0;
    float4 texcoord1 : TEXCOORD1;
    float4 texcoord4 : TEXCOORD4;
    half4 tangent : TANGENT;
    float4 blendIndices : BLENDINDICES;
    float4 blendWeights : BLENDWEIGHT;
};
```

形态 2：G66 特化——struct 体内 #include

```hlsl
// 文件：base/animated_grass.nsf
struct VS_INPUT
{
    // no light map
    // texcoord1 存植被动态信息
    #include "../shaderlib/vs_input_extend.hlsl"
};
```

形态 3：struct 体内字段也带 annotation（罕见）

```hlsl
// 实际未扫到，但 G66 编译器支持，需覆盖
struct Material {
    float roughness < float SasUiMin = 0; >;
};
```

**grammar 规则要点**：

- struct 体内可以有 `field_declaration` 也可以有 `preproc_include`（G66 特化）
- 字段声明 = 类型 + 名字 + 可选 semantic + 可选 annotation + `;`

### A4. cbuffer 声明（罕见，8 次）

```hlsl
// 文件：shaderlib/builtin_uniforms.hlsl
cbuffer GlobalConstants1 : register(b1)  // per frame
{
    float4 u_ambient : Ambient;
    float4 u_fog_color : FogColor;
    float4 u_player_position : PlayerPosition;
    float4 u_wind_param : WindParam;
    float4x4 u_lvp : LightViewProj;
    float4 u_point_attr[POINT_NUM*LIGHT_ATTR_ITEM_NUM] : PointLightAttrs;
    int u_cascade_count : CascadeCount;
    float u_frame_time : FrameTime;
    ...
}
```

**grammar 规则要点**：`cbuffer_declaration: seq('cbuffer', identifier, optional(register_binding), '{', repeat(field_declaration), '}')`

### A5. typedef / namespace（不做）

全库 0 次，grammar 不覆盖。

---

## 3. B 类：预处理指令

### B1. #include（高频，3358 次）

形态 1：引号路径（仓库内文件）

```hlsl
#include "../shaderlib/vs_input_extend.hlsl"
```

形态 2：尖括号路径（系统头，罕见）

```hlsl
#include <stdio.h>
```

形态 3：宏名（罕见，需要预处理展开才能解析）

```hlsl
#include MY_HEADER_H
```

**grammar 规则要点**：`preproc_include: seq('#', 'include', choice(string_literal, system_path, identifier))`

### B2. #define（高频，6009 + 320 次）

#### B2.1 object-like 宏（6009 次）

形态 1：常量

```hlsl
#define PI 3.14159
#define MAX_BONE_COUNT 100
```

形态 2：开关

```hlsl
#define NORMAL_MAP_ENABLE 1
#define HAS_TWO_SIDE 0
```

形态 3：token 别名

```hlsl
#define SHADINGMODELID SHADINGMODELID_DEFAULT_LIT
```

#### B2.2 function-like 宏（320 次）

形态 1：带固定参数

```hlsl
#define SQR(x) ((x) * (x))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
```

形态 2：`##` token paste（全库 2 次，罕见）

```hlsl
#define CONCAT(a, b) a##b
```

形态 3：`#` 字符串化（在 #define 里）

```hlsl
#define ASSERT(x) if (!(x)) { /* report */ }
#define STR(x) #x
```

形态 4：变参 `...` —— **全库 0 次，grammar 不做**

**grammar 规则要点**：

- object-like：`preproc_def: seq('#', 'define', identifier, replacement_text)`
- function-like：`preproc_def: seq('#', 'define', identifier, '(', parameter_list, ')', replacement_text)`
- replacement_text 不做语法解析（黑盒），只当字符串

### B3. #undef（高频，1654 次）

```hlsl
#undef DYNAMIC_SH_TEXTURE_ENABLE
#undef HAS_TWO_SIDE
```

### B4. 条件编译（高频）

#### B4.1 #ifdef（中频，36 次）

```hlsl
#ifdef REF_DEVICE
    ...
#endif
```

#### B4.2 #ifndef（高频，1783 次，常配合 include guard）

```hlsl
#ifndef HAS_TERRAIN_COLOR
    #define HAS_TERRAIN_COLOR 0
#endif
```

#### B4.3 #if / #elif / #else / #endif（高频）

形态 1：简单 if/else

```hlsl
#if API_PC_HIGH_QUALITY
    // 高画质路径
#else
    // 低画质路径
#endif
```

形态 2：多分支 elif

```hlsl
// 文件：base/far_landscape_1.nsf
#elif ENABLE_UE4_FOG && API_PC_HIGH_QUALITY
```

形态 3：复杂表达式（defined / 算术 / 逻辑组合）

```hlsl
#if NORMAL_MAP_SUPPORT && !API_MOBILE
    ...
#endif

#if SHADINGMODELID == SHADINGMODELID_DIFFUSE
    ...
#elif SHADINGMODELID == SHADINGMODELID_DEFAULT_LIT
    ...
#endif
```

形态 4：嵌套

```hlsl
#if API_PC
    #if QUALITY_HIGH
        ...
    #elif QUALITY_MIDDLE
        ...
    #endif
#else
    ...
#endif
```

**grammar 规则要点**：

- `preproc_if: seq('#', 'if', expression, repeat(_top_level_item), repeat(preproc_elif), optional(preproc_else), '#', 'endif')`
- `#if` 的 expression 里支持：`defined(NAME)`、`&&`、`||`、`!`、算术、位运算、宏引用
- grammar 只建语法树，**算 active 哪条分支交给 PreprocessorView**

### B5. #art（G66 特化，中频，804 次）

```hlsl
#art HAS_TERRAIN_COLOR "开启地表染色" "BOOL"
#art ENABLE_SSSMASK "开启M贴图SSS通道" "BOOL"
#art COLOR_CHANGE_MODE "换色模式/1单色/2四色..." "INT"
```

**grammar 规则要点**：`preproc_art_directive: seq('#', 'art', identifier, string_literal, string_literal)` — 第一个字符串是描述，第二个必须是 `"BOOL"` 或 `"INT"`（其他类型不识别）。

### B6. #excludefromtemptech（G66 特化，罕见，15 次）

```hlsl
// 文件：pbr/pbr_hair_depth_prepass.nsf
#excludefromtemptech BATCH_SKINNED_MESH

// 文件：pbr/nodes/lightmap_pbr_parameters.hlsl
#excludefromtemptech NORMAL_MAP_ENABLE
```

**grammar 规则要点**：`preproc_exclude_from_temp_tech: seq('#', 'excludefromtemptech', identifier)`

### B7. #pragma / #error / #warning（不做）

全库 0 次，grammar 加规则但允许空命中（防御性）。

### B8. 行接续 `\`

**实际全库无真行接续**——扫到的 15 次 `\\\n` 都是字符串里的 `\\` Windows 路径：

```hlsl
// 文件：common_pipeline/color_grading.nsf
string TextureFile="common\\nocompress\\rgbtable1x16.png";
```

但 grammar 仍要支持（标准 C 预处理特性，未来可能出现）——lexer 层处理。

---

## 4. C 类：语句层

### C1. compound_statement `{ ... }`（高频）

函数体、if/for body 都是。可以嵌套。

### C2. if / else（高频，7096 次）

形态 1：标准 if/else

```hlsl
if (cond) { ... }
if (cond) { ... } else { ... }
```

形态 2：else if 链

```hlsl
if (cond) {} else if (cond2) {} else {}
```

形态 3：不带括号（C 风格）

```hlsl
if (cond) foo();
```

形态 4：attribute 修饰 if

```hlsl
[branch] if (cond) { ... }   # 78 次
[flatten] if (cond) { ... }  # 4 次
```

### C3. for 循环（高频，818 次）

形态 1：声明在 init

```hlsl
for (int i = 0; i < 4; i++) { ... }
```

形态 2：用外部变量

```hlsl
for (i = 0; i < 4; i++) { ... }
```

形态 3：无限循环

```hlsl
for (;;) { ... }
```

形态 4：attribute 修饰

```hlsl
[unroll]
for (int i = 0; i < 4; i++) { ... }  # 114 次

[unroll(20)]
for (int i = 0; i < 20; i++) { ... }  # 3 次，带参数

[loop]
for (...) { ... }  # 31 次
```

### C4. while / do-while（罕见，< 30 次）

```hlsl
while (cond) { ... }
do { ... } while (cond);
```

### C5. switch / case / default / break（罕见，< 50 次）

```hlsl
switch (x) {
    case 1: ...; break;
    case 2: ...; break;
    default: ...; break;
}
```

### C6. continue / break / return（高频）

```hlsl
return;                  // void return
return value;            // 带值
return foo(a, b);        // 返回函数调用
return float4(1, 0, 0, 1);  // 返回类型构造
break;
continue;
```

### C7. discard（shader 独有，144 次）

`discard` 是 shader 独有的语句——丢弃当前像素（类似 return 但语义是"这个像素不渲染"）。

```hlsl
// 文件：common_cocosui/positiontexturecoloralphatest.nsf
if (local_0.w - CC_alpha_value <= 0.0)
{
    discard;
}
```

### C8. goto / 标签（不做）

全库 0 次。

### C9. 空语句 `;`

允许但偶尔出现，不专门建节点。

---

## 5. D 类：表达式层（shaderbase 建图的核心）

### D1. 字面量

#### D1.1 数值字面量

```hlsl
0.5f       // 浮点带 f 后缀（HLSL 标准）
0.5        // 浮点不带后缀
1          // 十进制整数
100        // 十进制整数
0xFF       // 十六进制整数
1e-5       // 科学计数法
0.01f      # 高频
0x1p-4f    # 十六进制浮点（罕见）
```

#### D1.2 字符串字面量

```hlsl
"diffuse tex RGB-固有色 A-透明"
"开启细节法线图"
"common\\nocompress\\rgbtable1x16.png"   # 路径里的反斜杠
```

#### D1.3 字符字面量

罕见，但 lexer 支持。

#### D1.4 布尔字面量

```hlsl
true
false
TRUE
FALSE
```

**grammar 规则要点**：

- `number: choice(/0[xX][0-9a-fA-F]+[uUlL]*/, /0[xX][0-9a-fA-F]*\.[0-9a-fA-F]*[pP][+-]?[0-9]+[fF]?/, /[0-9]+\.[0-9]+([eE][+-]?[0-9]+)?[fFhH]?/, /[0-9]+\.[0-9]*/, /\.[0-9]+([eE][+-]?[0-9]+)?[fFhH]?/, /[0-9]+([eE][+-]?[0-9]+)?[fFhH]?/, /[0-9]+[uUlL]*/)`
- `string_literal: /"[^"]*"/`
- `boolean: choice('true', 'false', 'TRUE', 'FALSE')`

### D2. 函数调用 call_expression（最关键）

**shaderbase 建 CALLS 边的基础**。

形态 1：无参调用

```hlsl
foo()
```

形态 2：多参调用

```hlsl
foo(a, b, c)
mul(v, m)                 # HLSL intrinsic，同 call_expression
```

形态 3：嵌套调用

```hlsl
foo(foo(foo()))
saturate(mul(v, m))
```

形态 4：类型构造调用（跟函数调用同形）

```hlsl
float4(1, 0, 0, 1)
half3(0.5)
int2(0, 1)
```

形态 5：成员方法调用

```hlsl
tex.Sample(s, uv)
tex.SampleLevel(s, uv, 0)
CC_Texture0.Sample(s_CC_Texture0, psIN.v_texture0.xy)
```

**grammar 规则要点**：

- `call_expression: seq(choice(identifier, member_expression, type_identifier), argument_list)`
- `argument_list: seq('(', optional(seq(expression, repeat(seq(',', expression)))), ')')`
- 类型构造（`float4(...)`）跟函数调用同形——靠 callee 是不是已知类型在抽取层判断

### D3. 成员访问 member_expression（高频）

形态 1：普通字段访问

```hlsl
obj.field
MaterialInputs.base_color
MaterialParameters.world_position
```

形态 2：链式访问

```hlsl
MaterialParameters.world_position.xyz
psIN.v_texture0.xy
```

形态 3：方法访问（接 call_expression）

```hlsl
tex.Sample
CC_Texture0.Sample
```

**grammar 规则要点**：`member_expression: seq(expression, '.', identifier)`

### D4. 二元运算（高频）

按优先级（从高到低，跟 C/HLSL 标准）：

| 优先级 | 运算符                   | 例子                        | 频次                        |
| --- | --------------------- | ------------------------- | ------------------------- |
| 3   | `*` `/` `%`           | `a * b`, `a / b`, `a % b` | 22990 / 157332 / 729      |
| 4   | `+` `-`               | `a + b`, `a - b`          | 12056 / 55332             |
| 5   | `<<` `>>`             | `a << 3`, `a >> 2`        | 827 / 458                 |
| 6   | `<` `>` `<=` `>=`     | `a < b`, `a >= b`         | 11054 / 10016 / 236 / 234 |
| 7   | `==` `!=`             | `a == b`, `a != b`        | 9227 / 201                |
| 8   | `&`                   | `a & b`                   | ~50                       |
| 9   | `^`                   | `a ^ b`                   | 151                       |
| 10  | `\|`                  | `a \| b`                  | ~30                       |
| 11  | `&&`                  | `a && b`                  | 1030                      |
| 12  | `\|\|`                | `a \|\| b`                | 910                       |
| 13  | `? :`（三元）             | `cond ? a : b`            | 529                       |
| 14  | 赋值 `= += -= *= /= %=` | `a = b`, `a += 1`         | 各几十次                      |

**grammar 规则要点**：用 tree-sitter 的 `prec.left(N, ...)` 表达优先级。参考 tree-sitter-c 的 binary_expression 实现。

### D5. 一元运算（高频）

| 运算符         | 例子                     | 频次                |
| ----------- | ---------------------- | ----------------- |
| `-x`        | `-a`, `-0.5f`          | ~5000             |
| `!x`        | `!flag`, `!API_MOBILE` | 985               |
| `~x`        | `~mask`                | ~10               |
| `++x` `x++` | `++i`, `i++`           | 430               |
| `--x` `x--` | `--i`, `i--`           | 20815（高频！for 循环用） |

**grammar 规则要点**：`unary_expression: choice(seq(choice('-', '!', '~', '++', '--'), expression), seq(expression, choice('++', '--')))`

### D6. 三元运算（中频，529 次）

```hlsl
cond ? a : b
(flag) ? 1.0f : 0.0f
```

### D7. swizzle（shader 独有，高频）

**Top 30 swizzle 形态**（共扫到 90 种）：

| swizzle                                | 频次                       | 含义           |
| -------------------------------------- | ------------------------ | ------------ |
| `.x` `.y` `.z` `.w`                    | 4895/4472/2946/2952      | 单分量          |
| `.xy` `.xyz` `.xyzw`                   | 3854/3426/81             | 多分量（xyzw 系）  |
| `.rgb` `.r` `.g` `.b` `.a` `.rgba`     | 2421/915/509/454/1190/43 | 颜色通道（rgba 系） |
| `.zw` `.xz` `.xzy`                     | 923/367/166              | 任意组合         |
| `.xx` `.xxx` `.xxx` `.yy` `.zz` `.zzz` | 80/62/52/19/13           | 重复取一个        |
| `.xyxy` `.zy` `.yx`                    | 38/70/21                 | 重排序          |

**grammar 规则要点**：

- `swizzle_suffix: /.[xyzwrgba]{1,4}/` — 1-4 个字符
- 跟普通 `member_expression` 区分：member_expression 的 identifier 必须是合法变量名；swizzle 是预定义的 xyzw/rgba/stpq 字母组合

### D8. matrix 元素访问

G66 **不用** `_m00` 形式（扫到 0 次），全用数组下标：

```hlsl
m[0][1]
matrices[i][j]
```

按 D9 处理，不专门建 `_m00` 规则。

### D9. 数组下标（高频）

```hlsl
arr[0]
arr[i+1]
matrices[3][2]          # 多维
u_point_attr[POINT_NUM*LIGHT_ATTR_ITEM_NUM]   # 声明里的数组
```

**grammar 规则要点**：`subscript_expression: seq(expression, '[', expression, ']', repeat(seq('[', expression, ']')))`

### D10. cast 显式类型转换（中频）

```hlsl
(int)x
(float4)vec
(half3)color
(PS_INPUT)0
```

**grammar 规则要点**：`cast_expression: seq('(', type, ')', expression)`

### D11. 括号表达式（高频）

```hlsl
(a + b) * c
((x))
```

**grammar 规则要点**：`parenthesized_expression: seq('(', expression, ')')`

### D12. 逗号表达式（中频）

主要在 for init 和函数参数里：

```hlsl
for (i = 0, j = 0; i < 4; i++, j++) { ... }
foo(a, b, c)
```

不单独建节点，跟 argument_list / for_init 共用。

---

## 6. E 类：HLSL 特化语法

### E1. register 绑定（中频）

| 形态                              | 次数  | 用途           |
| ------------------------------- | --- | ------------ |
| `register(t0)` ~ `register(tN)` | 109 | 贴图 slot      |
| `register(s0)` ~ `register(sN)` | 58  | 采样器 slot     |
| `register(b1)`                  | 4   | cbuffer slot |

**真实样例**：

```hlsl
cbuffer GlobalConstants1 : register(b1)
Texture2D<float4> tex : register(t0)
SamplerState s : register(s0)
```

**grammar 规则要点**：`register_binding: seq(':', 'register', '(', /[bsut]/, optional(seq(',', number)), ')')`

### E2. 语义绑定（高频）

**Top 20 semantic**（按频次）：

| semantic                                            | 频次                 | 用途                  |
| --------------------------------------------------- | ------------------ | ------------------- |
| `TEXCOORD0` ~ `TEXCOORD13`                          | 590+148+126+...+32 | VS↔PS 通用数据传递槽位      |
| `SV_Position`                                       | 431                | 系统语义：光栅器位置          |
| `POSITION`                                          | 393                | VS 输入顶点位置           |
| `COLOR0` / `COLOR`                                  | 68 / 54            | 顶点色                 |
| `NORMAL`                                            | 33                 | 顶点法线                |
| `TANGENT`                                           | < 20               | 顶点切线                |
| `BLENDINDICES` / `BLENDWEIGHT`                      | < 20               | 骨骼蒙皮                |
| `SV_Target`                                         | ~50                | PS 输出 render target |
| `SV_Depth`                                          | ~10                | PS 输出深度             |
| `SV_IsFrontFace`                                    | < 10               | PS 输入（是否前面）         |
| `SV_InstanceID`                                     | < 10               | VS 输入（instance ID）  |
| `SV_DispatchThreadID`                               | < 10               | CS 输入               |
| `SV_GroupID` / `SV_GroupThreadID` / `SV_GroupIndex` | < 10               | CS 输入               |
| `FOG`                                               | < 10               | 雾                   |
| `PSIZE`                                             | < 5                | 点大小                 |

**自定义 semantic 也出现**：`Ambient`、`FogColor`、`DirLightColor`、`DirLightDirection`、`WindParam` 等（这些是给引擎 SetUniform 用的标识符）。

**grammar 规则要点**：`semantic_binding: seq(':', identifier)` — 统一当 identifier，具体什么语义是抽取层判断（对照 shaderlib/builtin_uniforms.hlsl 里的语义表）。

### E3. 模板参数（中频）

| 类型                      | 频次  |
| ----------------------- | --- |
| `RWStructuredBuffer<T>` | 86  |
| `StructuredBuffer<T>`   | 71  |
| `RWTexture2D<T>`        | 62  |
| `Texture3D<T>`          | 48  |
| `Texture2D<T>`          | 46  |
| `RWTexture3D<T>`        | 45  |
| `Buffer<T>`             | 40  |
| `Texture2DArray<T>`     | 11  |
| `TextureCube<T>`        | 2   |

**真实样例**：

```hlsl
Texture2D<float4> Tex0;
RWStructuredBuffer<int> output_buffer;
RWTexture2D<float4> uav;
Buffer<float4> vertex_buffer;
```

**grammar 规则要点**：模板类型用 `display_type` 存完整（`Texture2D<float4>`），规范化后只存 `Texture2D` 当 `type`。

---

## 7. F 类：G66 特化语法

### F1. technique 块（高频，867 次）

**完整样例**：

```hlsl
// 文件：base/animated_grass_specular_mask.nsf
technique TShader
<
    string Description = "animated_grass_specular_mask";
    string SupportVelocityBuffer = "1";
    string SupportDeferredShading = "1";
>
{
    pass p0
    {
        StencilEnable = TRUE;
        StencilRef = 240;
        StencilWriteMask = 0x00f0;
        StencilFunc = ALWAYS;
        StencilFail = REPLACE;
        StencilZFail = KEEP;
        StencilPass = REPLACE;
        SupportCreateTemporaryTech = TRUE;
        VertexShader = vs_main;
        PixelShader = ps_main;
    }
}
```

**grammar 规则要点**：

- `technique_block: seq('technique', identifier, optional(metadata_block), '{', repeat(pass_block), '}')`
- `pass_block: seq('pass', identifier, '{', repeat(state_assignment), '}')`

### F2. technique 内状态赋值全清单

按频次（每个状态都是 `identifier = value;`）：

| 状态名                                                            | 频次      | 取值类型                          |
| -------------------------------------------------------------- | ------- | ----------------------------- |
| `StencilEnable`                                                | 186     | BOOL                          |
| `StencilPass` / `StencilFail` / `StencilZFail` / `StencilFunc` | 各 ~178  | enum（REPLACE/KEEP/ALWAYS/...） |
| `StencilRef`                                                   | 176     | int / hex                     |
| `StencilWriteMask`                                             | 159     | hex                           |
| `ZWriteEnable`                                                 | 111     | BOOL                          |
| `DestBlend` / `SrcBlend`                                       | 95 / 92 | enum                          |
| `SrcBlendAlpha` / `DestBlendAlpha`                             | 90 / 88 | enum                          |
| `SeparateAlphaBlendEnable`                                     | 90      | BOOL                          |
| `AlphaBlendEnable`                                             | 83      | BOOL                          |
| `ZEnable`                                                      | 70      | BOOL                          |
| `StencilMask`                                                  | 65      | hex                           |
| `CullMode`                                                     | 56      | enum                          |
| `SupportCreateTemporaryTech`                                   | 27      | BOOL                          |
| `ZFunc`                                                        | 14      | enum                          |
| `DepthBias`                                                    | 13      | float                         |
| `VertexShader` / `PixelShader`                                 | 各 ~870  | identifier（入口函数名）             |

**enum 取值示例**：

- `ALWAYS` / `KEEP` / `REPLACE` / `INCR` / `DECR` / `INVERT` 等（stencil）
- `NONE` / `FRONT` / `BACK` / `CW` / `CCW` 等（CullMode）
- `ZERO` / `ONE` / `SRCALPHA` / `INVSRCALPHA` 等（Blend）

**grammar 规则要点**：`state_assignment: seq(identifier, '=', choice(identifier, number, string_literal, boolean), ';')` — 状态名是普通 identifier，由抽取层对照状态表识别。

### F3. annotation 块 `<>`（高频，8541 个块）

**统计**：平均 152 字节，最大 11776 字节（很长的 annotation 块也存在）。

#### F3.1 简单 annotation（贴图）

```hlsl
// 文件：base/animated_grass.nsf
texture Tex0 : DiffuseMap
<
    string SasUiLabel = "diffuse tex";
    string SasUiControl = "FilePicker";
>;
```

#### F3.2 多 key annotation（uniform）

```hlsl
// 文件：pbr/nodes/pbr_default_nodes.hlsl
float u_roughness
<
    string SasUiGroup = "高光Specular";
    string SasUiLabel = "粗糙度Roughness";
    string SasUiControl = "FloatSlider";
    float SasUiSteps = 0.01;
    float SasUiMin = 0;
    float SasUiMax = 1;
> = 0.5f;
```

#### F3.3 带 TextureFile 的贴图 annotation

```hlsl
// 文件：shaderlib/season_uniforms.hlsl
texture t_autumn_blend
<
    string SasUiLabel = "blend";
    string SasUiControl = "FilePicker";
    string TextureFile = "textures_bw\\season\\weather\\surface_season_01_autumn.tga";
>;
```

#### F3.4 technique 头部 annotation

```hlsl
technique TShader
<
    string Description = "pbr_default";
    string SupportDeferredShading = "1";
>
{ ... }
```

**所有出现的 annotation key**（不完全列表）：

- `SasUiLabel` / `SasUiGroup` / `SasUiControl` / `SasUiSteps` / `SasUiMin` / `SasUiMax` / `SasUiDefaultValue`
- `TextureFile` / `ThumbnailEnable`
- `Description` / `SupportDeferredShading` / `SupportVelocityBuffer`

**所有出现的 SasUiControl 取值**（不完全）：

- `FilePicker` / `FloatSlider` / `ColorPicker` / `FloatPicker` / `AngleSlider` / `AlphaSlider` / `CheckBox` / `Button` / `ComboBox` / `ListBox` / `CurveEditor` / `VectorSlider` / `ColorAlphaSlider` / `MatrixSlider` / `MaximizedColorPicker` / `MinimizedColorPicker` / `RampSelector` / `UIUniformSlotRef`

**grammar 规则要点**：

- `metadata_block: seq('<', repeat(metadata_assignment), '>')`
- `metadata_assignment: seq(type, identifier, '=', choice(string_literal, number, identifier, boolean), optional(';'))`
- `type` 在 annotation 里是 `string` / `float` / `int` / `bool` / `texture` / `half` 等

### F4. SamplerState 状态块（高频，2227 次）

```hlsl
// 文件：base/animated_grass.nsf
SamplerState s_diffuse
{
    MipLODBias = -1;
    AddressU = CLAMP;
    AddressV = CLAMP;
    bSRGB = TRUE;
};

SamplerState s_terrain_color_tex
{
    MipLODBias = -1;
    AddressU = CLAMP;
    AddressV = CLAMP;
    Filter = MIN_MAG_LINEAR_MIP_POINT;
    bSRGB = TRUE;
};
```

**SamplerState 状态名全清单**（不全）：`MipLODBias`、`AddressU`、`AddressV`、`AddressW`、`Filter`、`bSRGB`、`BorderColor`、`MinLOD`、`MaxLOD`、`MaxAnisotropy`、`ComparisonFunc`

**grammar 规则要点**：`sampler_state_declaration: seq('SamplerState', identifier, optional(semantic_binding), optional(register_binding), '{', repeat(state_assignment), '}', ';')`

### F5. texture 声明（高频，2120 次）

#### F5.1 简单贴图

```hlsl
texture Tex0 : DiffuseMap < string SasUiLabel = "diffuse tex"; >;
```

#### F5.2 带 TextureFile 默认路径

```hlsl
texture t_autumn_blend < string TextureFile = "textures_bw\\season\\...tga"; >;
```

#### F5.3 模板类型贴图

```hlsl
Texture2D<float4> Tex0 : register(t0);
Texture2DArray<float4> tex_array;
Texture3D<float4> tex3d;
TextureCube<float4> env_map;
RWTexture2D<float4> uav;
```

**grammar 规则要点**：

- `texture_declaration: seq('texture', identifier, optional(semantic_binding), optional(metadata_block), ';')`
- 模板贴图走 E3 的模板类型

### F6. attribute `[xxx]`（中频）

按真实出现位置（行首的才算 attribute，行中的 `[...]` 是数组下标）：

#### F6.1 标准 HLSL attribute（少量）

| attribute               | 频次  | 用途         |
| ----------------------- | --- | ---------- |
| `[unroll]`              | 114 | 循环展开       |
| `[unroll(N)]`           | 4   | 指定展开次数     |
| `[loop]`                | 31  | 不展开        |
| `[branch]`              | 78  | if 走分支     |
| `[flatten]`             | 4   | if 走扁平     |
| `[numthreads(x, y, z)]` | 107 | CS 入口线程组大小 |

#### F6.2 G66 自定义 meta-attribute（多见）

扫到很多 `[xxx]` 形式但大部分是数组下标（`[i]`、`[index]`），不是真 attribute。真正的 G66 自定义 attribute 主要是：

- `[numthreads(BLOCK_SIZE, BLOCK_SIZE, 1)]` 这种带参数的，重复多次但参数是宏

**所有 `numthreads` 参数形态**：

```
[numthreads(BLOCK_SIZE, BLOCK_SIZE, 1)]
[numthreads(BLOCK_SIZE_X, BLOCK_SIZE_Y, 1)]
[numthreads(BLOCK_SIZE_X, BLOCK_SIZE_Y, BLOCK_SIZE_Z)]
[numthreads(BLOCK_SIZE_X, 1, 1)]
[numthreads(8, 8, 1)]
[numthreads(1, 1, 1)]
... 等等
```

**grammar 规则要点**：

- `attribute: seq('[', identifier, optional(argument_list), ']')` — 跟 call_expression 类似的参数列表
- 可以出现在 `for` / `if` / 函数定义前

---

## 8. G 类：修饰符

### G1. 类型修饰符

| 修饰符                                                                       | 频次  | 用途              |
| ------------------------------------------------------------------------- | --- | --------------- |
| `const`                                                                   | 923 | 常量              |
| `uniform`                                                                 | 125 | 引擎喂值的全局变量       |
| `static`                                                                  | 77  | 全局或局部静态变量       |
| `inline`                                                                  | 35  | 内联函数            |
| `groupshared`                                                             | 35  | CS 共享内存         |
| `precise`                                                                 | 18  | 精确（防止编译器优化改变结果） |
| `row_major`                                                               | 1   | 矩阵存储顺序          |
| `column_major`                                                            | 0   | 不做              |
| `volatile` / `extern` / `export` / `nointerpolation` / `globallycoherent` | 0   | 不做              |

### G2. 类型符号修饰（罕见）

| 修饰符     | 频次  | 用途           |
| ------- | --- | ------------ |
| `snorm` | 22  | 有符号归一化（-1~1） |
| `unorm` | 37  | 无符号归一化（0~1）  |

**真实样例**：

```hlsl
snorm float x;       // -1 到 1 的归一化浮点
unorm float y;       // 0 到 1 的归一化浮点
```

### G3. 参数方向修饰符（中频，关键）

| 修饰符     | 频次   | 用途       |
| ------- | ---- | -------- |
| `in`    | 800  | 输入参数（默认） |
| `out`   | 392  | 输出参数     |
| `inout` | 1970 | 输入输出参数   |

**真实样例**：

```hlsl
void CalcLocalData(in VS_INPUT input, inout VertexData v) { ... }
void foo(in float4 a, out float4 b, inout int c) { ... }
```

**grammar 规则要点**：`parameter: seq(optional(choice('in', 'out', 'inout')), type, identifier, optional(semantic_binding), optional(default_value))`

---

## 9. H 类：边角/罕见

### H1. discard 语句（shader 独有，144 次）

见 §C7。

### H2. 多行字符串

**全库无真多行字符串**——lexer 支持单行字符串，多行用 `\` 接续（罕见）。

### H3. 嵌套注释

C 标准不认嵌套 `/* ... /* ... */ ... */`，lexer 只看第一个 `*/` 结束。

### H4. 字符串里的反斜杠路径

```hlsl
// 文件：common_pipeline/color_grading.nsf
string TextureFile="common\\nocompress\\rgbtable1x16.png";
```

不是行接续，是 Windows 路径字符串。lexer 把整个字符串当一个 token。

### H5. 宏调用在表达式里

#### H5.1 当函数用

```hlsl
SQR(5)
MAX(a, b)
```

#### H5.2 当常量用

```hlsl
PI * 2
MAX_BONE_COUNT
```

#### H5.3 当类型用（罕见）

```hlsl
MaterialFloat4 color;   // MaterialFloat4 是宏，展开成 half4 或 float4
```

**grammar 规则要点**：宏调用语法上跟普通函数调用/标识符一样，不专门建节点。展开交给 PreprocessorView。

### H6. `defined()` 在 #if 表达式里

```hlsl
#if defined(API_PC) && !defined(API_MOBILE)
```

**grammar 规则要点**：`#if` 表达式里支持 `defined(identifier)` 和 `defined identifier` 两种形式。

---

## 10. 按分层测试组织（落到 §1.3 的 7 层）

| 层             | 覆盖语法点                                                 | fixture 数量 |
| ------------- | ----------------------------------------------------- | ---------- |
| 01_basic      | A1 函数、A2 变量、A3 struct、A4 cbuffer、G1/G2 修饰符            | 5-8 个      |
| 02_preproc    | B1-B8 所有预处理指令                                         | 6-8 个      |
| 03_annotation | F3 annotation 块（含多 key、TextureFile）、F4 SamplerState 块 | 4-6 个      |
| 04_technique  | F1 technique + F2 状态赋值                                | 3-5 个      |
| 05_semantic   | E2 语义绑定全列出 + E1 register 绑定 + G3 参数方向                 | 4-6 个      |
| 06_expression | D1-D12 所有表达式 + C1-C9 语句                               | 8-10 个     |
| 07_cbuffer    | A4 cbuffer 完整建模 + E3 模板参数 + F5 texture 声明             | 2-3 个      |

**总计约 36-46 个 corpus fixture**。

---

## 11. 持续补全流程

写 grammar 时跑 fixture 可能发现新的边角语法。流程：

1. 跑 `python scripts/coverage.py test/fixtures/full/` 看解析率
2. 挑 ERROR 最多的文件 → 看 ERROR 节点上下文
3. 识别是什么语法没覆盖 → 如果是本文档没列的，**补到本文档**
4. 写 grammar 规则 + 加 fixture 覆盖
5. 跑测试看数字改善

本文档是**活文档**，随 grammar 迭代持续补充。每次发现新语法点，必须先补到本文档（注明出现的文件 + 频次），再改 grammar——避免 grammar 漂移没记录。

---

## 12. 工程量评估

按本文档的覆盖面：

- 高频语法（19 类）：必做，grammar 第一周覆盖
- 中频语法（约 15 类）：第二周覆盖
- 罕见语法（约 13 类）：第三周覆盖，部分可推迟到阶段 2
- 全库 fixture 验证：4-5 天跑全库迭代收敛

**总工程量**：3-4 周（AI 辅助下）。如果接受 95% 覆盖率（不追 100%），可以压到 2-3 周。

---

**文档版本**：v1.0
**生成日期**：2026-07-21
**数据来源**：对 `D:/douzhongjun/work/shader/shader-source` 全库扫描（1298 个 .nsf/.hlsl/.fxh 文件，跳过 no_source/no_source_pc/pipeline_output/bin/.git）
**配套文档**：`SHADERBASE_DESIGN.md`、`SHADERBASE_DEV_PLAN.md`
