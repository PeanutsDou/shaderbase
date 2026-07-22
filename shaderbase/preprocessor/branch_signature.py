"""branch_signature — 分支签名 + 家族键（迭代 7）。

对齐 nsp workspace_index_relations.cpp:
- branch_signature_key: "branchId:branchIndex;branchId:branchIndex;..."
  精确标识行在哪个 #if 的哪个分支
- branch_family_key: "branchId;branchId;..."
  只标识行在哪些 #if 块里（不管哪个分支），用于"同家族查询"
- branch_signature_compatible: 两个签名是否兼容
  （同一组 #if 块里选了相同分支 → 兼容）
"""
from __future__ import annotations


def branch_signature_key(sig: list[tuple[int, int]]) -> str:
    """生成分支签名键。

    sig: [(branch_id, branch_index), ...]
    返回: "id1:idx1;id2:idx2;..."
    """
    if not sig:
        return ""
    return ";".join(f"{bid}:{bidx}" for bid, bidx in sig)


def branch_family_key(sig: list[tuple[int, int]]) -> str:
    """生成分支家族键（只取 branch_id，不取 branch_index）。

    返回: "id1;id2;..."
    """
    if not sig:
        return ""
    return ";".join(str(bid) for bid, _ in sig)


def parse_branch_signature_key(key: str) -> list[tuple[int, int]]:
    """解析签名键回 [(branch_id, branch_index), ...]。"""
    if not key:
        return []
    out = []
    for part in key.split(";"):
        if ":" not in part:
            continue
        bid_s, bidx_s = part.split(":", 1)
        try:
            out.append((int(bid_s), int(bidx_s)))
        except ValueError:
            continue
    return out


def branch_signature_compatible(lhs_key: str, rhs_key: str) -> bool:
    """两个签名是否兼容（同一组 #if 块里选了相同分支）。

    对齐 nsp branchSignatureCompatible:
    - 共同的 branch_id 必须选相同 branch_index → 兼容
    - 只在一边出现的 branch_id 忽略
    """
    lhs = parse_branch_signature_key(lhs_key)
    rhs = parse_branch_signature_key(rhs_key)
    i = j = 0
    while i < len(lhs) and j < len(rhs):
        if lhs[i][0] < rhs[j][0]:
            i += 1
            continue
        if rhs[j][0] < lhs[i][0]:
            j += 1
            continue
        # 同一 branch_id，必须选相同 branch_index
        if lhs[i][1] != rhs[j][1]:
            return False
        i += 1
        j += 1
    return True


__all__ = [
    "branch_signature_key",
    "branch_family_key",
    "parse_branch_signature_key",
    "branch_signature_compatible",
]
