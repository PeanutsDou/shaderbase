# test/fixtures/nodes — 节点抽取层 fixture（DEV_PLAN §1.7.2 层 2）

本目录是 shaderbase 抽取器的"对的标准"——每份 `.expected.yaml` 描述一个 shader 文件
**应该**抽到哪些节点（kind/name/line/关键字段），抽取器改动后跑比对脚本，diff 几秒出结果。

## 规则（DEV_PLAN §1.7.4）

1. **入库不可变**：一旦 fixture 入库，期望字段不能为迎合 grammar/抽取器改动而改。
   grammar/抽取器必须迁就 fixture，不是反过来。
2. **例外**：grammar/抽取器节点设计确实要演进时，必须标注"这是结构演进不是退化"，
   用户 review 后才能改期望。
3. **新增容易，删除要审批**：删 fixture 等于降低标准。
4. **AI 只对照不判断**：跑比对脚本，报 diff，不做主观判断。

## 格式

```yaml
# <file>.expected.yaml
input: <相对 shader-source 的路径>
expected_nodes:
  - kind: Function
    name: vs_main
    line: 30
    stage: vertex        # entry point 才有
  - kind: Struct
    name: VS_INPUT
    field_count: 3      # 只验字段数，不逐个验（字段细节变化多）
  - kind: Uniform
    name: u_roughness
    type: float
    has_annotation: true
  - kind: Texture
    name: Tex0
    texture_type: texture   # 或 Texture2D/Texture3D/...
  - kind: Technique
    name: TShader
    pass_count: 1
```

字段遵循"够用就好"原则——只验稳定字段，不验会变的细节。
