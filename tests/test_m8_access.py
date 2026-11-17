"""M8 授权裁定：逐消费方保留/补审/阻断、页面导出同结论、幂等、发布回退。"""

from __future__ import annotations

import csv
import io

import pytest

from app import errors, store
from app.modules.m8_access import judge, loader, publish, service


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _by_consumer(run: dict) -> dict[str, dict]:
    return {item["consumer"]: item for item in run["adjudication"]["decisions"]}


# ---------- 裁定规则 ----------

def test_seeded_access_run_adjudicates_all_consumers(run):
    access = service.latest()
    assert access is not None
    assert access["adjudication"]["total"] == 7
    assert access["protected"]["field_id"] == "3001"
    buckets = access["access_ledger"]["buckets"]
    assert buckets == {"RETAIN": 2, "REVIEW": 3, "BLOCK": 2}


def test_verified_identity_match_is_retained(run):
    decisions = _by_consumer(service.latest())
    for name in ("dw.mv_order_daily", "dw.task_settle"):
        item = decisions[name]
        assert item["verdict"] == "RETAIN"
        assert item["primary"] == "ACCESS_RETAIN"
        assert item["reasons"] == ["GRANT_VERIFIED"]
        assert item["channel_gates"] == {"page": "ALLOW", "export": "ALLOW"}


def test_unverified_grant_is_review_not_retained(run):
    decisions = _by_consumer(service.latest())
    item = decisions["dw.mv_pay_daily"]
    assert item["verdict"] == "REVIEW"
    assert "GRANT_UNVERIFIED" in item["reasons"]
    # 未核清不得放行，两个出口都挂起，不扩大范围
    assert item["channel_gates"] == {"page": "PENDING_REVIEW",
                                     "export": "PENDING_REVIEW"}


def test_channel_scope_gap_is_review_and_no_scope_expansion(run):
    decisions = _by_consumer(service.latest())
    item = decisions["dw.mv_order_stat"]
    assert item["verdict"] == "REVIEW"
    assert set(item["reasons"]) == {"GRANT_UNVERIFIED", "GRANT_CHANNEL_SCOPE"}
    # 旧授权只覆盖页面；未核清前导出不得自动扩面
    assert item["granted_channels"] == ["page"]
    assert item["channel_gates"]["export"] == "PENDING_REVIEW"


def test_missing_grant_is_blocked(run):
    decisions = _by_consumer(service.latest())
    item = decisions["dw.task_self"]
    assert item["verdict"] == "BLOCK"
    assert item["reasons"] == ["GRANT_MISSING"]
    assert all(gate == "DENY" for gate in item["channel_gates"].values())


def test_identity_mismatch_blocks_even_if_verified(run):
    decisions = _by_consumer(service.latest())
    item = decisions["dw.mv_user_dim"]
    assert item["verdict"] == "BLOCK"
    # 授权本身已核清，但身份对不上，禁止按列名悄悄重绑
    assert item["verified"] is True
    assert item["reasons"] == ["GRANT_ID_MISMATCH"]
    assert item["grant_field_id"] == "9999"
    assert all(gate == "DENY" for gate in item["channel_gates"].values())


def test_review_pending_consumer(run):
    decisions = _by_consumer(service.latest())
    item = decisions["ads.rpt_pay_channel"]
    assert item["verdict"] == "REVIEW"
    assert item["reasons"] == ["GRANT_UNVERIFIED"]


# ---------- 页面 / 导出同一结论 ----------

def test_page_and_export_share_one_verdict_per_identity(run):
    access = service.latest()
    for item in access["adjudication"]["decisions"]:
        judge.assert_consistent_channels(item)
        page, export_gate = item["channel_gates"]["page"], item["channel_gates"]["export"]
        # 同档结论：要么都放行、要么都挂起、要么都关闭
        assert judge._gate_level(page) == judge._gate_level(export_gate)
    assert access["access_ledger"]["channel_consistent"] is True
    channel_rows = access["access_ledger"]["channels"]
    assert channel_rows[0]["buckets"] == channel_rows[1]["buckets"]


def test_csv_matches_page_decisions_for_same_identity(client):
    access = service.latest()
    page_rows = {item["consumer"]: item for item in access["adjudication"]["decisions"]}
    csv_rows = {row["consumer"]: row for row in _rows(service.decisions_csv_text(access))}
    assert set(page_rows) == set(csv_rows)
    for consumer, item in page_rows.items():
        row = csv_rows[consumer]
        assert row["field_id"] == item["field_id"]
        assert row["verdict"] == item["verdict"]
        assert row["page_gate"] == item["channel_gates"]["page"]
        assert row["export_gate"] == item["channel_gates"]["export"]


def test_access_page_renders_same_table(client):
    response = client.get("/access")
    assert response.status_code == 200
    assert "授权裁定" in response.text
    access = service.latest()
    for item in access["adjudication"]["decisions"]:
        assert item["consumer"] in response.text


# ---------- 授权账守恒 ----------

def test_access_ledger_is_conserved(run):
    ledger = service.latest()["access_ledger"]
    assert ledger["gap"] == 0
    assert ledger["balanced"] is True
    assert ledger["accounted"] == ledger["total"] == 7


# ---------- 幂等：重跑不新增结论 ----------

def test_rerun_same_batch_adds_no_new_conclusion(client):
    before = service.latest()
    again = service.execute()
    assert again["reused"] is True
    assert again["run_id"] == before["run_id"]
    assert store.count_access_runs() == 1
    assert again["adjudication"]["decisions"] == before["adjudication"]["decisions"]
    assert again["access_ledger"]["buckets"] == before["access_ledger"]["buckets"]


def test_repeated_reruns_keep_single_batch_and_publish_pointer(client):
    first = service.latest()
    pointer = store.get_published_access()["run_id"]
    for _ in range(3):
        rerun = service.execute()
        assert rerun["reused"] is True
        assert rerun["run_id"] == first["run_id"]
    assert store.count_access_runs() == 1
    assert store.get_published_access()["run_id"] == pointer
    # 中断重跑同样不挪发布指针
    aborted = service.execute(abort_publish=True)
    assert aborted["publish"]["kept_previous"] is True
    assert store.get_published_access()["run_id"] == pointer


def test_adjudicate_api_idempotent(client):
    first = client.post("/api/access/adjudicate").json()
    second = client.post("/api/access/adjudicate").json()
    assert first["run_id"] == second["run_id"]
    assert second["reused"] is True
    assert len(client.get("/api/access/batches").json()["runs"]) == 1


def test_new_grants_file_produces_new_batch(client, tmp_path, monkeypatch):
    import json

    from app import config

    data = json.loads((config.SAMPLE_DIR / "access_grants.json").read_text("utf-8"))
    # 把未核清项核清，授权登记内容变了 -> 新批次、新结论
    for grant in data["grants"]:
        grant["verified"] = True
    isolated = tmp_path / "samples"
    isolated.mkdir()
    target = isolated / "access_grants_v2.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "SAMPLE_DIR", isolated)

    before = store.count_access_runs()
    second = service.execute("access_grants_v2.json")
    assert second["reused"] is False
    assert store.count_access_runs() == before + 1
    decisions = {d["consumer"]: d for d in second["adjudication"]["decisions"]}
    # 全部核清后未核清类补审归零；只剩 mv_order_stat 的渠道缺口补审
    assert all("GRANT_UNVERIFIED" not in d["reasons"]
               for d in second["adjudication"]["decisions"])
    assert decisions["dw.mv_order_stat"]["reasons"] == ["GRANT_CHANNEL_SCOPE"]
    assert second["access_ledger"]["buckets"] == {
        "RETAIN": 4, "REVIEW": 1, "BLOCK": 2}


# ---------- 发布中断保留上一份完整旧版 ----------

def test_publish_abort_keeps_previous_full_version(client):
    first = service.latest()
    assert store.get_published_access()["run_id"] == first["run_id"]
    result = service.execute(abort_publish=True)
    # 中断的重跑命中同批次键，只复用；发布版保持第一版
    assert result["reused"] is True
    assert store.get_published_access()["run_id"] == first["run_id"]


def test_publish_failure_never_overwrites_previous(client, tmp_path):
    import json

    from app import config

    first = service.latest()
    # 构造一个账不平的坏批次，直接打发布闸门
    broken = dict(first)
    broken["run_id"] = "ac-broken000000"
    broken["access_ledger"] = dict(first["access_ledger"], balanced=False)
    outcome = publish.publish(broken)
    assert outcome["published"] is False
    assert outcome["kept_previous"] is True
    assert outcome["run_id"] == first["run_id"]
    assert store.get_published_access()["run_id"] == first["run_id"]

    # 显式 abort 同样保留旧版
    aborted = publish.publish(first, abort=True)
    assert aborted["kept_previous"] is True
    assert store.get_published_access()["run_id"] == first["run_id"]


def test_publish_without_previous_raises_conflict(client):
    # 清掉发布版后，失败发布没有旧版可保留 -> 冲突
    from app import store as store_mod

    run = service.latest()
    with store_mod._connect() as conn:
        conn.execute("DELETE FROM access_published")
        conn.commit()
    broken = dict(run, access_ledger=dict(run["access_ledger"], balanced=False))
    with pytest.raises(errors.Conflict):
        publish.publish(broken)


def test_republish_endpoint_keeps_old_on_abort(client):
    first = service.latest()
    response = client.post("/api/access/publish",
                           json={"run_id": first["run_id"], "abort": True})
    assert response.status_code == 200
    body = response.json()
    assert body["kept_previous"] is True
    assert body["run_id"] == first["run_id"]


def test_published_api_serves_last_good_version(client):
    response = client.get("/api/access/published")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == service.latest()["run_id"]
    assert len(body["decisions"]) == 7


# ---------- 装载校验 ----------

def test_loader_rejects_bad_channels():
    access = loader.load("access_grants.json")
    assert access["channels"] == ["page", "export"]
    assert len(access["references"]) == 7
    assert len(access["grants"]) == 6


def test_judge_priority_block_over_review():
    access = loader.load("access_grants.json")
    # 同时制造身份不一致（阻断）与未核清（补审），阻断必须优先
    access["grants"][0]["field_id"] = "8888"
    access["grants"][0]["verified"] = False
    result = judge.adjudicate(access)
    item = next(d for d in result["decisions"]
                if d["consumer"] == access["grants"][0]["consumer"])
    assert item["verdict"] == "BLOCK"


# ---------- 导出 ----------

def test_ledger_csv_values(client):
    text = service.ledger_csv_text(service.latest())
    rows = {row[0]: row[1] for row in csv.reader(io.StringIO(text))}
    assert rows["total"] == "7"
    assert rows["RETAIN"] == "2"
    assert rows["REVIEW"] == "3"
    assert rows["BLOCK"] == "2"
    assert rows["gap"] == "0"
    assert rows["channel_consistent"] == "Y"
    # 两个渠道逐桶相等
    for verdict in ("RETAIN", "REVIEW", "BLOCK"):
        assert rows[f"page_{verdict}"] == rows[f"export_{verdict}"]


def test_csv_export_endpoints(client):
    decisions = client.get("/api/export/access.csv")
    assert decisions.status_code == 200
    assert decisions.headers["content-type"].startswith("text/csv")
    assert len(_rows(decisions.text)) == 7
    ledger_resp = client.get("/api/export/access-ledger.csv")
    assert ledger_resp.status_code == 200
