"""发布闸门：完整性不过不发布；发布中断时保留上一份完整旧版。

发布是唯一改 access_published 的动作。校验失败、调用方显式 abort，
都只返回失败结果，绝不覆盖库里上一份发布版。
"""

from __future__ import annotations

from app import errors

REQUIRED_KEYS = ("run_id", "batch_key", "adjudication", "access_ledger", "created_at")


def integrity_ok(run: dict) -> tuple[bool, list[str]]:
    problems: list[str] = []
    for key in REQUIRED_KEYS:
        if not run.get(key):
            problems.append(f"缺字段 {key}")
    ledger = run.get("access_ledger") or {}
    if ledger and not ledger.get("balanced"):
        problems.append("授权账不平，禁止发布")
    if ledger and not ledger.get("channel_consistent"):
        problems.append("页面与导出结论不一致，禁止发布")
    for decision in (run.get("adjudication") or {}).get("decisions", []):
        gates = decision.get("channel_gates") or {}
        levels = {gate for gate in gates.values()}
        if len(levels) > 1:
            problems.append(f"{decision.get('consumer')} 渠道结论矛盾")
    return not problems, sorted(problems)


def publish(run: dict, abort: bool = False) -> dict:
    """返回发布结果；任何失败路径都不触碰已发布版本。"""
    from app import store

    previous = store.get_published_access()
    if abort:
        return _kept(previous, "调用方标记发布中断", ["PUBLISH_ABORTED"])
    ok, problems = integrity_ok(run)
    if not ok:
        return _kept(previous, "发布校验未通过，保留上一份完整旧版", problems)
    store.set_published_access(run)
    return {
        "published": True,
        "run_id": run["run_id"],
        "kept_previous": False,
        "previous_run_id": (previous or {}).get("run_id"),
        "problems": [],
    }


def _kept(previous: dict | None, message: str, problems: list[str]) -> dict:
    if previous is None:
        raise errors.Conflict("没有可回退的旧版，且本次发布未通过",
                              detail={"problems": problems})
    return {
        "published": False,
        "run_id": previous["run_id"],
        "kept_previous": True,
        "previous_run_id": previous["run_id"],
        "message": message,
        "problems": problems,
    }
