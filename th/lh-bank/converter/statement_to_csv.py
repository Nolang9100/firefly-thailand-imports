#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Nolan Guo
"""Local draft: normalize supported Thai bank statement layouts without network access."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import getpass
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from decimal import Decimal
from pathlib import Path
from typing import Any
from control_evidence import validate_evidence, EvidenceError

CHECKS = ("count_check", "amount_check", "balance_check", "row_balance_check", "row_coverage_check", "page_check")
COLUMNS = ["date", "description", "amount", "currency", "external_id", "balance", "note"]

def as_decimal(value):
    return Decimal(str(value or "0").replace(",", ""))

def source_file_id(period, path, account_id):
    return "unused-in-public-output"

def base_transaction_row(sid, path, rule, account, period, sequence, tx_date, tx_time,
                         direction, withdrawal, deposit, signed, balance, channel,
                         kind, description, confidence):
    return dict(date=tx_date, description=description or kind, amount=str(signed), currency="THB",
                external_id="", balance=str(balance), note=channel,
                review_reason="", amount_signed=str(signed), direction=direction, withdrawal=str(withdrawal), deposit=str(deposit))


BANK = 'lh-bank'

class BankStatementControlError(ValueError):
    """Unsafe/unrecognized statement layout; diagnostics contain locations only."""

@dataclass
class SourceCheck:
    expected_withdrawal_count: int | None = None
    expected_deposit_count: int | None = None
    expected_withdrawal_total: Decimal | None = None
    expected_deposit_total: Decimal | None = None
    count_check: str = "NOT_CHECKED"
    amount_check: str = "NOT_CHECKED"
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    expected_closing_balance: Decimal | None = None
    balance_check: str = "NOT_CHECKED"
    row_balance_check: str = "NOT_CHECKED"
    row_coverage_check: str = "NOT_CHECKED"
    page_check: str = "NOT_CHECKED"

def finish_check(rows: list[dict[str, Any]], check: SourceCheck) -> None:
    withdrawal_rows = [r for r in rows if r["direction"] == "withdrawal"]
    deposit_rows = [r for r in rows if r["direction"] == "deposit"]
    withdrawal_total = sum(as_decimal(r["withdrawal"]) for r in withdrawal_rows)
    deposit_total = sum(as_decimal(r["deposit"]) for r in deposit_rows)
    count_checked = check.expected_withdrawal_count is not None or check.expected_deposit_count is not None
    amount_checked = check.expected_withdrawal_total is not None or check.expected_deposit_total is not None
    count_ok = True
    amount_ok = True
    if check.expected_withdrawal_count is not None:
        count_ok = count_ok and len(withdrawal_rows) == check.expected_withdrawal_count
    if check.expected_deposit_count is not None:
        count_ok = count_ok and len(deposit_rows) == check.expected_deposit_count
    if check.expected_withdrawal_total is not None:
        amount_ok = amount_ok and withdrawal_total == check.expected_withdrawal_total
    if check.expected_deposit_total is not None:
        amount_ok = amount_ok and deposit_total == check.expected_deposit_total
    check.count_check = "OK" if count_checked and count_ok else ("MISMATCH" if count_checked else "NOT_CHECKED")
    check.amount_check = "OK" if amount_checked and amount_ok else ("MISMATCH" if amount_checked else "NOT_CHECKED")

def parse_lhb_pdf(path: Path, text: str, period: str, rule: dict[str, Any], account: dict[str, Any]) -> tuple[list[dict[str, Any]], SourceCheck]:
    """Parse the LH Bank e-Statement layout; opening is derived, not independently confirmed."""
    header = text.split('\f', 1)[0]
    account_match = re.search(r'Account Number\s*:\s*([\d-]+)', header)
    period_match = re.search(r'Date\s*:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})', header)
    if not account_match or re.sub(r'\D', '', account_match.group(1))[-4:] != str(rule.get('masked_tail', '')):
        raise BankStatementControlError('lhb_account_header_mismatch')
    if not period_match or 'Account e-Statement' not in header or 'Balance (THB)' not in header:
        raise BankStatementControlError('lhb_header_missing')
    start, end = [dt.datetime.strptime(s, '%d/%m/%Y').date() for s in period_match.groups()]
    if start > end:
        raise BankStatementControlError('lhb_period_invalid')
    pages = text.split('\f')
    row_re = re.compile(r'(?m)^\s*(\d{2}/\d{2}/\d{4})\s*\n\s*(\d{2}:\d{2})\s*\n\s*(\d{2}/\d{2}/\d{4})\s+(.+?)(?=^\s*\d{2}/\d{2}/\d{4}\s*\n\s*\d{2}:\d{2}\s*\n|^(?:หากพบรายการเดินบัญชี|Page\s)|\Z)', re.S)
    money = re.compile(r'(?<!\S)([+-][\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([^\n]+)\s*$')
    rows, previous, previous_date = [], None, None
    sid = source_file_id(period, path, rule['account_id'])
    for page_num, page in enumerate(pages, 1):
        markers = re.findall(r'Page\s+(\d+)\s*/\s*(\d+)', page)
        if markers != [(str(page_num), str(len(pages)))]:
            raise BankStatementControlError(f'lhb_page_marker_invalid:page={page_num}')
        anchors = re.findall(r'(?m)^\s*\d{2}/\d{2}/\d{4}\s*\n\s*\d{2}:\d{2}\s*\n', page)
        matches = list(row_re.finditer(page))
        amount_lines = re.findall(r'(?m)^\s*[^\n]*?(?<!\S)[+-][\d,]+\.\d{2}\s+[\d,]+\.\d{2}\s+[^\n]+$', page)
        dated_lines = re.findall(r'(?m)^\s*\d{2}/\d{2}/\d{4}\s*$', page)
        if len(matches) != len(anchors) or len(matches) != len(amount_lines) or len(matches) != len(dated_lines) or not matches:
            raise BankStatementControlError(f'lhb_row_coverage_mismatch:page={page_num}')
        for match in matches:
            d, tm, effective, body = match.groups()
            date = dt.datetime.strptime(d, '%d/%m/%Y').date()
            dt.datetime.strptime(tm, '%H:%M'); dt.datetime.strptime(effective, '%d/%m/%Y')
            if not start <= date <= end or (previous_date and date < previous_date):
                raise BankStatementControlError(f'lhb_date_invalid:page={page_num}')
            m = money.search(body.strip())
            if not m:
                raise BankStatementControlError(f'lhb_amount_or_balance_missing:page={page_num}')
            delta, balance = [Decimal(v.replace(',', '')) for v in m.group(1, 2)]
            if not delta or (previous is not None and previous + delta != balance):
                raise BankStatementControlError(f'lhb_row_balance_mismatch:page={page_num}')
            if previous is None:
                opening = balance - delta
            description = ' '.join(body[:m.start()].split())
            row = base_transaction_row(sid, path, rule, account, f'{start.isoformat()} to {end.isoformat()}', len(rows)+1, date.isoformat(), tm, 'deposit' if delta > 0 else 'withdrawal', -delta if delta < 0 else Decimal(0), delta if delta > 0 else Decimal(0), delta, balance, m.group(3).strip(), '', description, 'high')
            row['source_page'] = str(page_num); row['balance'] = str(balance)
            row['review_reason'] += '; opening_balance_derived_not_independently_confirmed; effective_date=' + effective
            rows.append(row); previous = balance; previous_date = date
    check = SourceCheck(opening_balance=opening, closing_balance=previous, page_check='OK', row_coverage_check='OK', row_balance_check='OK')
    finish_check(rows, check)
    return rows, check

def read_pdf(path, ask_password=False):
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        password = getpass.getpass("PDF password (not saved): ") if ask_password else ""
        try:
            unlocked = reader.decrypt(password)
        finally:
            password = None
        if not unlocked:
            raise BankStatementControlError("pdf_unlock_required_or_failed")
    if not reader.pages:
        raise BankStatementControlError("empty_pdf")
    return "\f".join(page.extract_text() or "" for page in reader.pages)


def validate_rows(rows, opening, start, end):
    previous = opening
    previous_date = None
    for row in rows:
        date = dt.date.fromisoformat(row["date"])
        amount = Decimal(row["amount"])
        balance = Decimal(row["balance"])
        if not start <= date <= end or (previous_date and date < previous_date) or not amount:
            raise BankStatementControlError("invalid_transaction_date_or_amount")
        if previous + amount != balance:
            raise BankStatementControlError("row_balance_mismatch")
        previous, previous_date = balance, date
    return previous


def convert_text(text, opening_balance=None, csv_path=None, *, review_only=False, controls_json=None, source_sha256=None):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    period = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not period:
        raise BankStatementControlError("statement_period_missing")
    start, end = [dt.datetime.strptime(v, "%d/%m/%Y").date() for v in period.groups()]
    if start > end: raise BankStatementControlError("statement_period_invalid")
    if "LH BANK" not in text.upper(): raise BankStatementControlError("unsupported_bank_header")
    account = re.search(r'Account Number\s*:\s*([\d-]+)', text)
    if not account: raise BankStatementControlError("account_header_missing")
    rule = {"account_id": "local", "masked_tail": re.sub(r"\D", "", account.group(1))[-4:]}
    rows, check = parse_lhb_pdf(Path("statement.pdf"), text, "", rule, {})
    validate_rows(rows, check.opening_balance, start, end)
    if opening_balance is not None and check.opening_balance != Decimal(opening_balance):
        raise BankStatementControlError("opening_balance_crosscheck_mismatch")
    if not rows or any(getattr(check, key) != "OK" for key in ("row_balance_check", "row_coverage_check", "page_check")):
        raise BankStatementControlError("statement_controls_failed")
    external = {"status": "NOT_PROVIDED"}
    if controls_json is not None:
        try:
            external = validate_evidence(controls_json, text, rows, source_sha256)
        except (EvidenceError, ValueError, OSError, TypeError, KeyError):
            # Never include source paths or evidence contents in CLI diagnostics.
            raise BankStatementControlError("external_control_evidence_invalid_or_mismatched") from None
    if external["status"] != "MATCH" and not review_only:
        raise BankStatementControlError("independent_control_evidence_required_use_review_only")
    fingerprint=hashlib.sha256(text.encode()).hexdigest()
    output=[]
    for i,row in enumerate(rows,1):
        row["external_id"]=f"{BANK}-v1-{fingerprint}-{i:06d}"
        if BANK=="lh-bank": row["note"] += "; " + row["review_reason"].lstrip("; ")
        output.append({k:row[k] for k in COLUMNS})
    missing = ["printed_opening_balance", "printed_debit_credit_counts_and_totals", "separately_printed_closing_summary"]
    report = {"status": "REVIEW_ONLY_NOT_IMPORT_READY" if review_only else "LOCAL_CONVERSION_WITH_ATTESTED_CONTROLS",
              "bank": BANK, "rows": len(rows), "currency": "THB", "statement_text_sha256": fingerprint,
              "checks": {key: getattr(check, key) for key in CHECKS}, "controls": asdict(check),
              "opening_balance_source": "first_row_derived_not_independent",
              "closing_balance_source": "last_transaction_row",
              "source_control_gaps": missing, "external_controls": external,
              "csv_export_ready": not review_only and external["status"] == "MATCH",
              "formal_source_approval": False, "firefly_import_tested": False,
              "validation_scope": "local_conversion_not_posting_approval"}
    return output, report



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--currency",choices=["THB"],required=True)
    parser.add_argument("--ask-password",action="store_true")
    parser.add_argument("--text-fixture",action="store_true")
    parser.add_argument("--opening-balance",help="Optional arithmetic cross-check; does not supply independent evidence")
    parser.add_argument("--controls-json", type=Path, help="Scoped externally attested control bundle")
    parser.add_argument("--review-only", action="store_true", help="Write review candidates while retaining missing independent controls")
    if BANK=="kbank": parser.add_argument("--csv",action="store_true",help="Normalize the supported KBank CSV layout")
    args=parser.parse_args()
    try:
        is_csv=getattr(args,"csv",False)
        if is_csv and args.text_fixture: raise BankStatementControlError("input_modes_conflict")
        text=args.input.read_text(encoding="utf-8-sig") if args.text_fixture or is_csv else read_pdf(args.input,args.ask_password)
        rows,report=convert_text(text, args.opening_balance, review_only=args.review_only,
                                controls_json=args.controls_json,
                                source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest())
        report["input_sha256"]=hashlib.sha256(args.input.read_bytes()).hexdigest()
        args.output_dir.mkdir(parents=True,exist_ok=False)
        with (args.output_dir/("review-candidates.csv" if args.review_only else "transactions.csv")).open("x",encoding="utf-8",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=COLUMNS); writer.writeheader(); writer.writerows(rows)
        (args.output_dir/"controls.json").write_text(json.dumps(report,indent=2,default=str)+"\n")
        print(json.dumps({"status":report["status"],"rows":len(rows),"checks":report["checks"]}))
        return 0
    except BankStatementControlError as exc:
        print(f"Conversion refused: {exc}",file=sys.stderr); return 2
    except Exception as exc:
        print(f"Conversion failed: {type(exc).__name__}",file=sys.stderr); return 2

if __name__=="__main__": raise SystemExit(main())
