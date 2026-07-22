"""preprocessor — PreprocessorView Python 转译（借鉴 nsp-intellision 算法）。

双视图架构（DEV_PLAN §2.2）：
- 索引阶段：空 defines 建全分支视图，算 branch_signature
- 查询阶段：Agent 传 macros，算 line_active + 按 active 过滤边
"""
