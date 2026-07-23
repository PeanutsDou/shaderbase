const CPP = require("tree-sitter-cpp/grammar")

module.exports = grammar(CPP, {
    name: 'hlsl',

    conflicts: ($, original) => original.concat([
        [$.function_declarator],
        // metadata_block '<...>' 跟模板参数/比较运算符歧义
        [$.metadata_block, $.expression],
        // declaration 后跟 metadata_block 跟模板参数歧义
        [$._declarator, $.template_type, $.template_function],
        // technique 块跟函数声明/变量声明歧义
        [$.technique_block, $.declaration],
        [$.technique_block, $.function_definition],
        // sampler_state_block 跟 field_declaration_list/compound_statement 歧义
        [$.sampler_state_block, $.field_declaration_list],
        [$.sampler_state_block, $.compound_statement],
        // texture 声明跟普通变量声明歧义
        [$.texture_declaration, $.declaration],
        // declaration 跟 init_declarator 歧义（metadata_block + = init）
        [$.declaration, $.init_declarator],
        // metadata_assignment 跟 expression/assignment_expression 歧义
        [$.expression, $.assignment_expression, $.metadata_assignment],
        // semantics 跟 function declarator 歧义（: register(...) vs : SEMANTIC）
        [$.semantics, $.semantics_call],
        // PLSLayout(...) 当 attribute vs 当 type_identifier + parenthesized_declarator
        [$.pls_layout_attribute, $.field_declaration],
        // (if 不带括号改动已回退，相关 conflict 声明已移除)
    ]),

    rules: {
        _top_level_item: (_, original) => original,

        function_definition: ($, original) => seq(
            optional(
                $.hlsl_attribute,
            )
            , original
        ),

        // G66 field_declaration 加可选 PLSLayout 前缀
        // PLSLayout(rgba8) float4 color0 : SV_Target0;
        field_declaration: ($, original) => seq(
            optional($.pls_layout_attribute),
            original,
        ),
        function_declarator: ($, original) => seq(
            original,
            optional($.semantics)
        ),

        // G66 annotation `<>` 块：跟在变量/texture/technique 声明后
        // 形态： < key = value; key = value; ... >
        // 内部允许：metadata_assignment / 裸宏名 / 带参数宏调用 / 声明 / 注释
        metadata_block: $ => prec.right(seq(
            '<',
            repeat(choice(
                $.metadata_assignment,
                $.g66_macro_statement,
                $.call_expression,
                $.declaration,
                $.comment,
            )),
            '>',
        )),

        metadata_assignment: $ => prec(100, seq(
            optional($.metadata_type),
            field('name', $.identifier),
            '=',
            field('value', choice(
                $.string_literal,
                $.number_literal,
                $.identifier,
                $.true_keyword,
                $.false_keyword,
                $.unary_expression,         // 支持 - 1.0 这种负数
                $.initializer_list,         // 支持 int3 SasVersion = {1,0,0};
                $.call_expression,          // 支持 float3(1,0,0) 这种构造
            )),
            ';',
        )),

        metadata_type: _ => choice(
            'string',
            'float',
            'int',
            'bool',
            'half',
            'texture',
            'double',
            'uint',
            'int2', 'int3', 'int4',
            'float2', 'float3', 'float4',
            'half2', 'half3', 'half4',
            'uint2', 'uint3', 'uint4',
            'float2x2', 'float3x3', 'float4x4',
        ),

        true_keyword: _ => choice('TRUE', 'true'),
        false_keyword: _ => choice('FALSE', 'false'),

        // declaration 加上可选 metadata_block（在 semantics 之后）+ 可选初始化
        // 支持：
        //   float u_x <...>;                        ← 无初始化
        //   float u_x <...> = 0.5f;                 ← expression 初始化
        //   static const int arr[4] = {1,2,3,4};   ← initializer_list 初始化
        //   float3 w[8] = {float3(1,0,0), ...};     ← initializer_list 含类型构造
        declaration: $ => prec(1, seq(
            $._declaration_specifiers,
            commaSep1(field('declarator', seq(
                $._declarator,
                optional(alias(seq(':', $.expression), $.semantics)),
                optional($.metadata_block),
                optional(seq('=', choice($.expression, $.initializer_list))),
            ))),
            ';'
        )),

        _declaration_modifiers: ($, original) => choice(
            'in',
            'out',
            'inout',
            $.qualifiers,
            original),


        parameter_declaration: ($, original) =>
            seq(
                original,
                optional($.semantics),
            ),

        // G66 semantics 识别 register(...) 和 : SEMANTIC_NAME 两种形态
        // : TEXCOORD0           → semantics(identifier)
        // : register(s0)        → semantics(call_expression)
        // : SV_Position         → semantics(identifier)
        // : packoffset(c0.y)    → semantics(call_expression with member_access)
        semantics: $ => prec.right(seq(":", choice(
            $.identifier,
            alias($.semantics_call, $.call_expression),
        ))),

        semantics_call: $ => prec(1, seq(
            $.identifier,
            $.argument_list,
            optional(seq('.', $.identifier)),
        )),

        _non_case_statement: ($, original) => choice($.discard_statement, $.cbuffer_specifier, $.g66_macro_statement, original),

        if_statement: ($, original) => seq(optional($.hlsl_attribute), original),

        // G66 已知裸宏调用 statement（限定已知宏名，避免泛匹配导致回退）
        g66_macro_statement: $ => prec.right(choice(
            'NEOX_SASEFFECT_SUPPORT_MACRO_BEGIN',
            'NEOX_SASEFFECT_SUPPORT_MACRO_END',
            'NEOX_SASEFFECT_ATTR_BEGIN',
            'NEOX_SASEFFECT_ATTR_END',
            'HAIR_SHADING_PARAMS_PREPARE',
            seq('KILL_NAN_INF_NEGATIVE', optional($.argument_list)),
        )),

        discard_statement: _ => seq('discard', ';'),
        qualifiers: _ => choice(
            'precise',
            'shared',
            'groupshared',
            'uniform',
            'row_major',
            'column_major',
            'globallycoherent',
            'centroid',
            'noperspective',
            'nointerpolation',
            'sample',
            'linear',
            'snorm',
            'unorm',
            'point',
            'line',
            'triangleadj',
            'lineadj',
            'triangle',
        ),

        // G66 cbuffer 声明：
        //   cbuffer NAME { fields };              ← 简单形态（带 ;）
        //   cbuffer NAME { fields }               ← 简单形态（不带 ;，G66 实际写法）
        //   cbuffer NAME : register(b1) { fields };  ← 带 register 绑定
        //   cbuffer NAME : register(b1);         ← 仅前向声明（无 body）
        // 用 prec(1) 优先于普通 declaration，避免 'cbuffer' 被当类型
        cbuffer_specifier: $ => prec.right(1, seq(
            'cbuffer',
            field('name', $._class_name),
            repeat($.semantics),           // : register(b1)
            field('body', optional($.field_declaration_list)),
            optional(';'),                // ; 可选（G66 很多 cbuffer 块不带 ;）
        )),

        hlsl_attribute: $ => seq('[',
            $.expression,
            ']'),

        // G66 PLSLayout attribute：PLSLayout(rgba8) / PLSLayout(rgb10_a2)
        // 出现在 field_declaration 前，类似 hlsl_attribute 的前缀作用
        // 形态：PLSLayout(format) float4 color0 : SV_Target0;
        // 用 prec.dynamic 让 PLSLayout(...) 优先匹配成 attribute 而非 type+declarator
        pls_layout_attribute: $ => prec.dynamic(10, seq(
            'PLSLayout',
            $.argument_list,
        )),

        for_statement: ($, original) => seq(optional($.hlsl_attribute), original),

        // G66 technique 块：technique NAME <metadata> { pass ... }
        technique_block: $ => prec.right(seq(
            'technique',
            field('name', $.identifier),
            field('metadata', optional($.metadata_block)),
            '{',
            repeat($.pass_block),
            '}',
        )),

        pass_block: $ => seq(
            'pass',
            field('name', $.identifier),
            '{',
            repeat($.state_assignment),
            '}',
        ),

        state_assignment: $ => seq(
            field('name', $.identifier),
            '=',
            field('value', choice(
                $.identifier,
                $.number_literal,
                $.string_literal,
                $.true_keyword,
                $.false_keyword,
            )),
            ';',
        ),

        // G66 texture 声明：
        //   texture NAME : Semantic <annotation>;
        //   texture NAME : register(t6) : DepthBuffer;  ← 双冒号
        //   texture NAME <annotation>;
        // 注意：Texture2D/Texture3D 等大写形式走原 declaration（当 type_identifier）
        texture_declaration: $ => prec.right(seq(
            'texture',
            field('name', $.identifier),
            repeat($.semantics),
            field('metadata', optional($.metadata_block)),
            ';',
        )),

        // G66 SamplerState 声明，覆盖三种形态：
        //   1. SamplerState NAME;                              （无状态块，无 register）
        //   2. SamplerState NAME : register(s0);              （无状态块，带 register）
        //   3. SamplerState NAME { Filter=...; AddressU=...; };  （带状态块）
        // 用 prec(1) 让它优先于原 declaration（避免 SamplerState 被当普通 type）
        sampler_state_declaration: $ => prec(1, seq(
            'SamplerState',
            field('name', $.identifier),
            repeat($.semantics),
            optional($.sampler_state_block),
            ';',
        )),

        sampler_state_block: $ => seq(
            '{',
            repeat($.state_assignment),
            '}',
        ),

        // 把 G66 顶层声明加进 _top_level_item
        // 用 override 扩展原 _top_level_item
        _top_level_item: ($, original) => choice(
            original,
            $.technique_block,
            $.texture_declaration,
            $.sampler_state_declaration,
            $.cbuffer_specifier,
            $.preproc_art_directive,
            $.preproc_exclude_from_temp_tech,
        ),

        // G66 特化指令也要能在 #if body 里出现
        _block_item: ($, original) => choice(
            original,
            $.preproc_art_directive,
            $.preproc_exclude_from_temp_tech,
            $.technique_block,
            $.texture_declaration,
            $.sampler_state_declaration,
        ),

        // G66 #art 指令：#art NAME "描述" "BOOL"/"INT"
        // 用 prec(1) 比 preproc_call 优先级高，让 #art 优先匹配这条而不是 preproc_call
        preproc_art_directive: $ => prec(1, seq(
            '#art',
            field('name', $.identifier),
            field('description', $.string_literal),
            field('art_type', $.string_literal),
            token.immediate(/\r?\n/),
        )),

        // G66 #excludefromtemptech 指令
        preproc_exclude_from_temp_tech: $ => prec(1, seq(
            '#excludefromtemptech',
            field('name', $.identifier),
            token.immediate(/\r?\n/),
        )),

        // G66 override: #elif 后面的 condition 可选（支持裸 #elif）
        preproc_elif: ($, original) => prec.right(seq(
            '#elif',
            optional(field('condition', $._preproc_expression)),
            '\n',
            repeat($._block_item),
            field('alternative', optional(choice($.preproc_elif, $.preproc_else))),
        )),

        // G66 override: if 不带括号的形态
        // if season_factors.x > 0.0h { ... }     ← 不带括号
        // if UP_FACING_MASK ... #endif           ← 宏当条件（在 #if 块里）
        if_statement: ($, original) => seq(optional($.hlsl_attribute), original),

    }
});

function commaSep1(rule) {
    return seq(rule, repeat(seq(',', rule)))
}
