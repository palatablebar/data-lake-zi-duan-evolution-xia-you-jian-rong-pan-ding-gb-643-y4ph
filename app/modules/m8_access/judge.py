"""授权裁定纯规则：每个消费方对受保护字段身份给保留 / 补审 / 阻断。

规则（只认 field_id，不按列名重绑）：
1. 没有授权登记            -> BLOCK  GRANT_MISSING
2. 授权身份与字段身份不一致 -> BLOCK  GRANT_ID_MISMATCH
3. 旧授权未核清            -> REVIEW GRANT_UNVERIFIED（未核清不得扩大渠道范围）
4. 请求渠道超出已授权范围  -> REVIEW GRANT_CHANNEL_SCOPE
5. 身份一致、已核清、渠道全覆盖 -> RETAIN GRANT_VERIFIED

页面与导出是同一字段身份的两个出口，共享同一条裁定：渠道只做闸门，
不允许页面放行而导出阻断（反之亦然）。
"""

from __future__ import annotations

from app import config

# 判定优先级：阻断类先于补审类，补审类先于保留类；同档按原因码字典序
VERDICT_ORDER = {"BLOCK": 0, "REVIEW": 1, "RETAIN": 2}
VERDICT_REASON = {
    "RETAIN": "ACCESS_RETAIN",
    "REVIEW": "ACCESS_REVIEW",
    "BLOCK": "ACCESS_BLOCK",
}


def _grant_index(grants: list[dict]) -> dict[str, dict]:
    return {item["consumer"]: item for item in grants}


def adjudicate(access: dict) -> dict:
    protected = access["protected"]
    target_id = protected["field_id"]
    channels = tuple(access["channels"])
    index = _grant_index(access["grants"])

    decisions: list[dict] = []
    for reference in sorted(access["references"], key=lambda item: item["consumer"]):
        consumer = reference["consumer"]
        grant = index.get(consumer)
        decision = _one(consumer, reference, grant, target_id, channels)
        decisions.append(decision)

    buckets = {name: 0 for name in config.ACCESS_VERDICTS}
    for item in decisions:
        buckets[item["verdict"]] += 1
    return {
        "protected": protected,
        "channels": list(channels),
        "decisions": decisions,
        "buckets": buckets,
        "total": len(decisions),
        "balanced": _balanced(buckets, len(decisions)),
    }


def _one(consumer: str, reference: dict, grant: dict | None,
         target_id: str, channels: tuple[str, ...]) -> dict:
    findings: list[str] = []
    if grant is None:
        findings.append("GRANT_MISSING")
    elif grant["field_id"] != target_id:
        findings.append("GRANT_ID_MISMATCH")
    else:
        if not grant["verified"]:
            findings.append("GRANT_UNVERIFIED")
        granted = set(grant["granted_channels"])
        if any(channel not in granted for channel in channels):
            findings.append("GRANT_CHANNEL_SCOPE")
        if not findings:
            findings.append("GRANT_VERIFIED")

    verdict = _verdict_of(findings)
    channel_gates = _gates(verdict, grant, channels)
    return {
        "consumer": consumer,
        "kind": reference.get("kind") or "task",
        "ref": reference["ref"],
        "field_id": target_id,
        "verdict": verdict,
        "primary": VERDICT_REASON[verdict],
        "reasons": sorted(findings),
        "grant_field_id": grant["field_id"] if grant else None,
        "verified": bool(grant and grant["verified"]),
        "granted_channels": sorted(grant["granted_channels"]) if grant else [],
        "review_batch": grant["review_batch"] if grant else "",
        "channel_gates": channel_gates,
        "note": grant["note"] if grant else "",
    }


def _verdict_of(findings: list[str]) -> str:
    if any(code in findings for code in ("GRANT_MISSING", "GRANT_ID_MISMATCH")):
        return "BLOCK"
    if any(code in findings for code in
           ("GRANT_UNVERIFIED", "GRANT_CHANNEL_SCOPE")):
        return "REVIEW"
    return "RETAIN"


def _gates(verdict: str, grant: dict | None,
           channels: tuple[str, ...]) -> dict[str, str]:
    """同一字段身份在页面与导出的访问闸门必须给出同一档结论。

    RETAIN：只在已授权渠道放行；REVIEW/BLOCK：全部渠道先关闸，
    补审通过或阻断解除前两个出口一致，不会一个放一个拦。
    """
    gates: dict[str, str] = {}
    granted = set(grant["granted_channels"]) if grant else set()
    for channel in channels:
        if verdict == "RETAIN":
            gates[channel] = "ALLOW" if channel in granted else "DENY"
        elif verdict == "REVIEW":
            gates[channel] = "PENDING_REVIEW"
        else:
            gates[channel] = "DENY"
    return gates


def _balanced(buckets: dict[str, int], total: int) -> bool:
    return sum(buckets[name] for name in config.ACCESS_VERDICTS) == total


def assert_consistent_channels(decision: dict) -> None:
    """不变量检查：同一条裁定里，两个出口不能出现互相矛盾的结论档。"""
    gates = decision["channel_gates"]
    levels = {_gate_level(value) for value in gates.values()}
    if len(levels) > 1:
        raise AssertionError(f"{decision['consumer']} 页面与导出结论不一致: {gates}")


def _gate_level(gate: str) -> str:
    if gate == "ALLOW":
        return "open"
    if gate == "PENDING_REVIEW":
        return "pending"
    return "closed"
