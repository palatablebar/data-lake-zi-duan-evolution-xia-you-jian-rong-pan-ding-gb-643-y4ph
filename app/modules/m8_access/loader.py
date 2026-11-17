"""授权登记读取：受保护字段、引用证据、旧授权，逐项校验。

只放解析与结构校验；一条消费方该保留、补审还是阻断，在 judge.py 里判。
"""

from __future__ import annotations

import json

from app import config, errors, manifest

_VALID_VERIFY = (True, False)


def load(rel_path: str) -> dict:
    path = config.resolve_sample(rel_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise errors.BadInput("授权登记文件无法解析",
                              detail={"file": path.name, "reason": exc.msg})

    protected = _protected(payload, path.name)
    channels = _channels(payload)
    references = _references(payload, channels, path.name)
    grants = _grants(payload, channels, path.name)
    return {
        "source": path.name,
        "sha256": manifest.sha256_file(path),
        "registered_at": payload.get("registered_at"),
        "protected": protected,
        "channels": list(channels),
        "references": references,
        "grants": grants,
    }


def _protected(payload: dict, file_name: str) -> dict:
    raw = payload.get("protected_field")
    if not isinstance(raw, dict):
        raise errors.BadInput("授权登记缺少 protected_field", detail={"file": file_name})
    field_id = str(raw.get("field_id") or "").strip()
    table = str(raw.get("table") or "").strip()
    column = str(raw.get("column") or "").strip()
    if not field_id or not table or not column:
        raise errors.BadInput("protected_field 必须给全 field_id/table/column",
                              detail={"file": file_name})
    return {
        "field_id": field_id,
        "table": table,
        "column": column,
        "label": str(raw.get("label") or column),
        "note": str(raw.get("note") or ""),
    }


def _channels(payload: dict) -> tuple[str, ...]:
    raw = payload.get("channels") or list(config.ACCESS_CHANNELS)
    if not isinstance(raw, list) or not raw:
        raise errors.BadInput("channels 必须是非空列表")
    channels = [str(item).strip() for item in raw]
    if any(not item for item in channels) or len(set(channels)) != len(channels):
        raise errors.BadInput("channels 含空值或重复")
    unknown = sorted(set(channels) - set(config.ACCESS_CHANNELS))
    if unknown:
        raise errors.BadInput("出现未登记的访问渠道", detail={"channels": unknown})
    return tuple(channels)


def _references(payload: dict, channels: tuple[str, ...], file_name: str) -> list[dict]:
    raw = payload.get("references")
    if not isinstance(raw, list) or not raw:
        raise errors.BadInput("授权登记缺少 references 列表", detail={"file": file_name})
    seen: set[str] = set()
    refs: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise errors.BadInput("references 里出现非对象项", detail={"file": file_name})
        consumer = str(item.get("consumer") or "").strip()
        ref = str(item.get("ref") or "").strip()
        if not consumer or not ref:
            raise errors.BadInput("引用证据缺 consumer 或 ref", detail={"file": file_name})
        if consumer in seen:
            raise errors.BadInput("同一消费方对受保护字段重复登记引用",
                                  detail={"consumer": consumer})
        seen.add(consumer)
        refs.append({"consumer": consumer,
                     "kind": str(item.get("kind") or "task"),
                     "ref": ref})
    return refs


def _grants(payload: dict, channels: tuple[str, ...], file_name: str) -> list[dict]:
    raw = payload.get("grants")
    if not isinstance(raw, list):
        raise errors.BadInput("授权登记缺少 grants 列表", detail={"file": file_name})
    seen: set[str] = set()
    grants: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise errors.BadInput("grants 里出现非对象项", detail={"file": file_name})
        consumer = str(item.get("consumer") or "").strip()
        field_id = str(item.get("field_id") or "").strip()
        if not consumer or not field_id:
            raise errors.BadInput("授权项缺 consumer 或 field_id",
                                  detail={"file": file_name})
        if consumer in seen:
            raise errors.BadInput("同一消费方重复授权登记", detail={"consumer": consumer})
        seen.add(consumer)
        granted = [str(x).strip() for x in (item.get("granted_channels") or [])]
        unknown = sorted(set(granted) - set(channels))
        if unknown:
            raise errors.BadInput("授权渠道超出登记渠道",
                                  detail={"consumer": consumer, "channels": unknown})
        verified = item.get("verified")
        if verified not in _VALID_VERIFY:
            raise errors.BadInput("verified 必须是布尔值", detail={"consumer": consumer})
        grants.append({
            "consumer": consumer,
            "field_id": field_id,
            "granted_channels": sorted(set(granted)),
            "verified": bool(verified),
            "review_batch": str(item.get("review_batch") or ""),
            "note": str(item.get("note") or ""),
        })
    return grants
