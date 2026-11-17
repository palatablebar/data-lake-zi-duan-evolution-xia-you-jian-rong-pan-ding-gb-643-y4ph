"""授权裁定 CSV：页面与导出同源，同一 field_id 的结论逐字一致。"""

from __future__ import annotations

import csv
import io

DECISION_HEADER = [
    "consumer", "kind", "field_id", "ref", "verdict", "primary_reason",
    "page_gate", "export_gate", "grant_field_id", "verified",
    "granted_channels", "review_batch", "reasons",
]

LEDGER_HEADER = ["item", "value"]


def _buffer() -> tuple[io.StringIO, "csv.writer"]:
    buffer = io.StringIO()
    return buffer, csv.writer(buffer, lineterminator="\n")


def decisions_csv(run: dict) -> str:
    buffer, writer = _buffer()
    writer.writerow(DECISION_HEADER)
    for item in (run.get("adjudication") or {}).get("decisions", []):
        gates = item.get("channel_gates") or {}
        writer.writerow([
            item["consumer"],
            item.get("kind") or "",
            str(item.get("field_id") or ""),
            item.get("ref") or "",
            item["verdict"],
            item["primary"],
            gates.get("page", ""),
            gates.get("export", ""),
            str(item.get("grant_field_id") or ""),
            "Y" if item.get("verified") else "N",
            "|".join(item.get("granted_channels") or []),
            item.get("review_batch") or "",
            "|".join(item.get("reasons") or []),
        ])
    return buffer.getvalue()


def ledger_csv(run: dict) -> str:
    ledger = run.get("access_ledger") or {}
    buffer, writer = _buffer()
    writer.writerow(LEDGER_HEADER)
    writer.writerow(["total", ledger.get("total", 0)])
    for name in ("RETAIN", "REVIEW", "BLOCK"):
        writer.writerow([name, (ledger.get("buckets") or {}).get(name, 0)])
    writer.writerow(["accounted", ledger.get("accounted", 0)])
    writer.writerow(["gap", ledger.get("gap", 0)])
    writer.writerow(["balanced", "Y" if ledger.get("balanced") else "N"])
    writer.writerow(["channel_consistent",
                     "Y" if ledger.get("channel_consistent") else "N"])
    for row in ledger.get("channels") or []:
        for verdict in ("RETAIN", "REVIEW", "BLOCK"):
            writer.writerow([f"{row['channel']}_{verdict}",
                             (row.get("buckets") or {}).get(verdict, 0)])
    return buffer.getvalue()
