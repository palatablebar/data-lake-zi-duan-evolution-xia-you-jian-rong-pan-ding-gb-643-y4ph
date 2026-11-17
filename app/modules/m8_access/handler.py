"""M8 入口：授权裁定页、裁定接口、发布接口、CSV 导出。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app import errors, store, web
from app.modules.m8_access import service

router = APIRouter()

ALLOWED_KEYS = {"grants_file", "abort_publish"}


def _csv(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/access", response_class=HTMLResponse)
def access_page(request: Request):
    run = store.latest_access_run()
    published = store.get_published_access()
    return web.page_or_404(
        request,
        "access.html",
        "access",
        run,
        adjudication=(run or {}).get("adjudication") or {},
        access_ledger=(run or {}).get("access_ledger") or {},
        published=published,
        runs=store.list_access_runs(),
    )


@router.post("/api/access/adjudicate")
def adjudicate(payload: dict | None = None):
    body = payload or {}
    unknown = set(body) - ALLOWED_KEYS
    if unknown:
        raise errors.BadInput("出现未知参数", detail={"keys": sorted(unknown)})
    run = service.execute(
        grants_file=str(body.get("grants_file") or service.DEFAULT_GRANTS),
        abort_publish=bool(body.get("abort_publish")),
    )
    return {
        "run_id": run["run_id"],
        "reused": bool(run.get("reused")),
        "base_run_id": run.get("base_run_id"),
        "field_id": run["protected"]["field_id"],
        "buckets": run["access_ledger"]["buckets"],
        "balanced": run["access_ledger"]["balanced"],
        "channel_consistent": run["access_ledger"]["channel_consistent"],
        "publish": run.get("publish"),
    }


@router.get("/api/access")
def access_api():
    run = store.latest_access_run()
    if not run:
        return {"run_id": None, "adjudication": None, "published": None}
    return {
        "run_id": run["run_id"],
        "base_run_id": run.get("base_run_id"),
        "protected": run["protected"],
        "channels": run["channels"],
        "adjudication": run["adjudication"],
        "access_ledger": run["access_ledger"],
        "published_run_id": (store.get_published_access() or {}).get("run_id"),
        "created_at": run["created_at"],
    }


@router.get("/api/access/batches")
def access_batches_api():
    return {"runs": store.list_access_runs(),
            "published_run_id": (store.get_published_access() or {}).get("run_id")}


@router.post("/api/access/publish")
def republish(payload: dict | None = None):
    body = payload or {}
    run_id = body.get("run_id")
    if not run_id:
        latest = store.latest_access_run()
        if not latest:
            raise errors.NotFound("还没有授权批次，先跑一次裁定")
        run_id = latest["run_id"]
    result = service.republish(str(run_id), abort=bool(body.get("abort")))
    return result


@router.get("/api/access/published")
def published_api():
    run = store.get_published_access()
    if not run:
        raise errors.NotFound("还没有发布过授权裁定")
    return {
        "run_id": run["run_id"],
        "field_id": run["protected"]["field_id"],
        "buckets": run["access_ledger"]["buckets"],
        "decisions": run["adjudication"]["decisions"],
    }


@router.get("/api/export/access.csv")
def export_access_csv():
    run = service.latest()
    if not run:
        raise errors.NotFound("还没有授权批次，先跑一次裁定")
    return _csv(service.decisions_csv_text(run), "fieldmap-access.csv")


@router.get("/api/export/access-ledger.csv")
def export_access_ledger_csv():
    run = service.latest()
    if not run:
        raise errors.NotFound("还没有授权批次，先跑一次裁定")
    return _csv(service.ledger_csv_text(run), "fieldmap-access-ledger.csv")
