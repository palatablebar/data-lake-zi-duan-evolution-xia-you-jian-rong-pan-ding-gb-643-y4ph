"""授权账：每个消费方只进一个结论桶，三桶之和必须等于消费方总数。"""

from __future__ import annotations

from app import config


def build(adjudication: dict) -> dict:
    buckets = {name: int(adjudication["buckets"].get(name, 0))
               for name in config.ACCESS_VERDICTS}
    total = int(adjudication["total"])
    accounted = sum(buckets.values())
    gap = total - accounted
    channel_rows = _channel_account(adjudication)
    return {
        "buckets": buckets,
        "total": total,
        "accounted": accounted,
        "gap": gap,
        "balanced": accounted == total and gap == 0,
        "channels": channel_rows,
        "channel_consistent": all(row["same_verdict"] for row in channel_rows),
    }


def _channel_account(adjudication: dict) -> list[dict]:
    """页面与导出按同一 field_id 各结一遍账，两份账必须逐桶相等。"""
    channels = adjudication["channels"]
    rows = []
    for channel in channels:
        buckets = {name: 0 for name in config.ACCESS_VERDICTS}
        for decision in adjudication["decisions"]:
            buckets[decision["verdict"]] += 1
        rows.append({"channel": channel, "buckets": buckets})
    first = rows[0]["buckets"] if rows else {}
    for row in rows:
        row["same_verdict"] = row["buckets"] == first
    return rows
