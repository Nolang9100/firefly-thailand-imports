# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Nolan Guo
"""Check scoped, externally attested bank controls without inventing evidence."""
from pathlib import Path
from decimal import Decimal
import datetime as dt
import hashlib
import json
import re

METRICS = ("opening_balance", "closing_balance", "withdrawal_count", "deposit_count", "withdrawal_total", "deposit_total")
SOURCE_KINDS = {"previous_bank_statement", "next_bank_statement", "bank_balance_confirmation", "bank_control_summary"}

class EvidenceError(ValueError):
    pass

def validate_evidence(bundle_path, text, rows, source_sha256=None):
    bundle_path = Path(bundle_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict): raise EvidenceError("control_evidence_invalid_object")
    target_hash = hashlib.sha256(text.encode()).hexdigest()
    if (bundle.get("version") != 1 or bundle.get("bank") != "lh-bank" or bundle.get("currency") != "THB"
            or bundle.get("statement_text_sha256") != target_hash):
        raise EvidenceError("control_evidence_scope_mismatch")
    controls = bundle.get("controls")
    if not isinstance(controls, dict) or set(controls) != set(METRICS):
        raise EvidenceError("control_evidence_incomplete")
    result, sources = {}, []
    for metric in METRICS:
        item = controls[metric]
        if not isinstance(item, dict): raise EvidenceError("control_evidence_invalid_metric")
        value = item.get("value")
        count = metric.endswith("_count")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+" if count else r"-?[0-9]+\.[0-9]{2}", value):
            raise EvidenceError("control_value_invalid")
        expected = int(value) if count else Decimal(value)
        if (count or metric.endswith("_total")) and expected < 0:
            raise EvidenceError("control_value_negative")
        source = item.get("evidence", {})
        if not isinstance(source, dict): raise EvidenceError("control_provenance_invalid_object")
        if (source.get("kind") not in SOURCE_KINDS or not source.get("locator") or
                not source.get("reviewed_by") or not source.get("reviewed_at")):
            raise EvidenceError("control_provenance_incomplete")
        try:
            reviewed = dt.datetime.fromisoformat(source["reviewed_at"].replace("Z", "+00:00"))
            if reviewed.tzinfo is None: raise ValueError()
        except (ValueError, TypeError): raise EvidenceError("control_review_timestamp_invalid") from None
        name = source.get("file")
        digest = source.get("sha256")
        if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise EvidenceError("control_source_reference_invalid")
        path = (bundle_path.parent / name).resolve()
        if not path.is_file(): raise EvidenceError("control_source_missing")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest: raise EvidenceError("control_source_hash_mismatch")
        if actual in {target_hash, source_sha256} or path == bundle_path.resolve():
            raise EvidenceError("control_source_is_target_statement")
        result[metric] = expected
        sources.append({"metric": metric, "source_sha256": digest, "kind": source["kind"],
                        "locator": source["locator"], "reviewed_by": source["reviewed_by"],
                        "reviewed_at": source["reviewed_at"]})
    withdrawals = [r for r in rows if Decimal(r["amount"]) < 0]
    deposits = [r for r in rows if Decimal(r["amount"]) > 0]
    derived_opening = Decimal(rows[0]["balance"]) - Decimal(rows[0]["amount"])
    observed = {"opening_balance": derived_opening, "closing_balance": Decimal(rows[-1]["balance"]),
                "withdrawal_count": len(withdrawals), "deposit_count": len(deposits),
                "withdrawal_total": sum((-Decimal(r["amount"]) for r in withdrawals), Decimal(0)),
                "deposit_total": sum((Decimal(r["amount"]) for r in deposits), Decimal(0))}
    if any(observed[key] != result[key] for key in METRICS):
        raise EvidenceError("external_control_value_mismatch")
    if result["opening_balance"] + result["deposit_total"] - result["withdrawal_total"] != result["closing_balance"]:
        raise EvidenceError("external_control_balance_equation_mismatch")
    return {"status": "MATCH", "checks": {key: "MATCH" for key in METRICS}, "sources": sources,
            "assurance": "file_hashes_and_scope_checked_values_manually_attested",
            "semantic_source_extraction_verified_by_converter": False}
