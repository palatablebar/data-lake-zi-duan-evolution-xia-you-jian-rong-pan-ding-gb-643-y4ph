"""授权裁定编排：读登记 → 逐消费方裁定 → 授权账 → 落批次 → 发布。

同一授权批次（授权登记摘要 + 所依据的字段批次 + 规则集）只算一次：
命中批次键直接复用，重跑不新增结论、不新增行。
"""

from __future__ import annotations

import json

from app import config, errors, manifest as manifest_mod, store
from app.modules.m8_access import csv_out, judge, ledger as ledger_mod, loader, publish

DEFAULT_GRANTS = config.DEFAULT_ACCESS


def _batch_key(access: dict, base_run_id: str | None) -> str:
    payload = {
        "grants_sha256": access["sha256"],
        "source": access["source"],
        "base_run_id": base_run_id or "",
        "protected_field_id": access["protected"]["field_id"],
        "channels": access["channels"],
        "ruleset": config.RULESET_VERSION,
    }
    raw = manifest_mod.sha256_bytes(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    return raw


def execute(grants_file: str = DEFAULT_GRANTS, base_run: dict | None = None,
            abort_publish: bool = False) -> dict:
    access = loader.load(grants_file)
    base = base_run if base_run is not None else store.latest_run()
    base_run_id = (base or {}).get("run_id")
    key = _batch_key(access, base_run_id)

    # 重跑同一授权批次：原样复用，绝不新增结论或改动发布版
    existing = store.find_access_run_by_key(key)
    if existing is not None:
        reused = dict(existing)
        reused["reused"] = True
        if abort_publish:
            # 发布中断：发布版保持上一份完整旧版（此处即该批次自身）
            reused["publish"] = {
                "published": False,
                "run_id": existing["run_id"],
                "kept_previous": True,
                "previous_run_id": existing["run_id"],
                "message": "发布中断，沿用上一份完整旧版",
                "problems": ["PUBLISH_ABORTED"],
            }
        else:
            reused["publish"] = {
                "published": True,
                "run_id": existing["run_id"],
                "kept_previous": False,
                "previous_run_id": existing["run_id"],
                "problems": [],
            }
        return reused

    adjudication = judge.adjudicate(access)
    for decision in adjudication["decisions"]:
        judge.assert_consistent_channels(decision)
    access_ledger = ledger_mod.build(adjudication)
    if not access_ledger["balanced"] or not access_ledger["channel_consistent"]:
        raise errors.Conflict("授权账未对齐，拒绝落批次",
                              detail={"ledger": access_ledger})

    created_at = config.now_utc().isoformat()
    run = {
        "run_id": "ac-" + key[:12],
        "batch_key": key,
        "base_run_id": base_run_id,
        "input_sha256": access["sha256"],
        "source": access["source"],
        "registered_at": access.get("registered_at"),
        "ruleset": config.RULESET_VERSION,
        "created_at": created_at,
        "protected": adjudication["protected"],
        "channels": adjudication["channels"],
        "adjudication": adjudication,
        "access_ledger": access_ledger,
        "reused": False,
    }
    run["summary"] = _summary(run)
    store.save_access_run(run)

    # 发布失败只保留上一份完整旧版，不影响已落库的裁定
    run["publish"] = publish.publish(run, abort=abort_publish)
    return run


def _summary(run: dict) -> dict:
    buckets = run["access_ledger"]["buckets"]
    return {
        "run_id": run["run_id"],
        "base_run_id": run.get("base_run_id"),
        "source": run["source"],
        "created_at": run["created_at"],
        "field_id": run["protected"]["field_id"],
        "total": run["access_ledger"]["total"],
        "retain": buckets["RETAIN"],
        "review": buckets["REVIEW"],
        "block": buckets["BLOCK"],
        "balanced": run["access_ledger"]["balanced"],
        "channel_consistent": run["access_ledger"]["channel_consistent"],
        "reused": False,
    }


def ensure_seeded() -> dict | None:
    """启动时随字段批次一起把样例授权裁定跑成第一版并发布。"""
    if store.count_access_runs() > 0:
        return store.latest_access_run()
    return execute()


def latest() -> dict | None:
    return store.latest_access_run()


def published() -> dict | None:
    return store.get_published_access()


def decisions_csv_text(run: dict) -> str:
    return csv_out.decisions_csv(run)


def ledger_csv_text(run: dict) -> str:
    return csv_out.ledger_csv(run)


def republish(run_id: str, abort: bool = False) -> dict:
    run = store.access_run_by_id(run_id)
    if run is None:
        raise errors.NotFound(f"授权批次不存在：{run_id}")
    return publish.publish(run, abort=abort)
