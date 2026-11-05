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


BANK = 'scb'

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

def parse_date(value: str) -> str:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text

def parse_statement_period_from_bangkok_text(text: str, fallback: str) -> str:
    match = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not match:
        return fallback
    start = dt.datetime.strptime(match.group(1), "%d/%m/%Y").date()
    end = dt.datetime.strptime(match.group(2), "%d/%m/%Y").date()
    start_label = start.strftime("%Y-%m")
    end_label = end.strftime("%Y-%m")
    return start_label if start_label == end_label else f"{start_label}_to_{end_label}"

def parse_statement_period_from_scb_text(text: str, fallback: str) -> str:
    return parse_statement_period_from_bangkok_text(text, fallback)

def parse_expected_from_scb_text(text: str) -> SourceCheck:
    compact = " ".join(text.split())
    debit = re.search(r"TOTAL AMOUNTS \(Debit\)\s+([0-9,]+\.\d{2})", compact)
    credit = re.search(r"TOTAL AMOUNTS \(Credit\)\s+([0-9,]+\.\d{2})", compact)
    items = re.search(r"TOTAL ITEMS\s+([0-9,]+)\s+([0-9,]+)", compact)
    return SourceCheck(
        expected_withdrawal_count=int(items.group(1).replace(",", "")) if items else None,
        expected_deposit_count=int(items.group(2).replace(",", "")) if items else None,
        expected_withdrawal_total=as_decimal(debit.group(1)) if debit else None,
        expected_deposit_total=as_decimal(credit.group(1)) if credit else None,
    )

def parse_scb_pdf(path: Path, text: str, period: str, rule: dict[str, Any], account: dict[str, Any]) -> tuple[list[dict[str, Any]], SourceCheck]:
    check = parse_expected_from_scb_text(text)
    statement_period = parse_statement_period_from_scb_text(text, period)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start_re = re.compile(
        r"^(?P<date>\d{2}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2})\s+"
        r"(?P<code>X[12]|IN|TX)\s+(?P<channel>\S+)\s+"
        r"(?P<amount>[0-9,]+\.\d{2})\s+(?P<balance>[0-9,]+\.\d{2})\s+"
        r"DESC\s*:\s*(?P<description>.*)$"
    )
    transaction_texts: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        match = start_re.match(line)
        if match:
            if current:
                transaction_texts.append(current)
            current = match.groupdict()
            continue
        if current and not line.startswith("NOTE :"):
            if line.startswith(("TOTAL AMOUNTS", "TOTAL ITEMS", "This document", "Page ", "หน้า ")):
                transaction_texts.append(current)
                current = None
            elif not any(marker in line for marker in ("THE SIAM COMMERCIAL BANK", "STATEMENT OF SAVING ACCOUNT", "BALANCE BROUGHT FORWARD")):
                current["description"] = f"{current['description']} {line}".strip()
    if current:
        transaction_texts.append(current)

    rows: list[dict[str, Any]] = []
    sid = source_file_id(period, path, rule["account_id"])
    for index, item in enumerate(transaction_texts, 1):
        dt.datetime.strptime(item["time"], "%H:%M")
        amount = as_decimal(item["amount"])
        balance = as_decimal(item["balance"])
        direction = "deposit" if item["code"] in {"X1", "IN"} else "withdrawal"
        withdrawal = amount if direction == "withdrawal" else Decimal("0")
        deposit = amount if direction == "deposit" else Decimal("0")
        rows.append(base_transaction_row(
            sid, path, rule, account, statement_period, index,
            parse_date(item["date"]), item["time"], direction,
            withdrawal, deposit, deposit - withdrawal, balance,
            item["channel"], item["code"], item["description"], "high"
        ))
    finish_check(rows, check)
    return rows, check


def checked_scb_statement(text, opening_balance=None):
    """Verify printed page openings, page sequence, rows, and summary equation."""
    pages = text.split("\f")
    rows = []
    previous = None
    opening = None
    for number, page in enumerate(pages, 1):
        markers = re.findall(r"(?:หน้า|Page)\s+(\d+)\s*/\s*(\d+)", page)
        if markers != [(str(number), str(len(pages)))]:
            raise BankStatementControlError(f"scb_page_marker_invalid:page={number}")
        brought = re.findall(r"BALANCE BROUGHT FORWARD\)\s+([0-9,]+\.\d{2})", page)
        if len(brought) != 1:
            raise BankStatementControlError(f"scb_printed_opening_missing_or_ambiguous:page={number}")
        value = Decimal(brought[0].replace(",", ""))
        if previous is None:
            opening = value
            if opening_balance is not None and value != Decimal(opening_balance):
                raise BankStatementControlError("scb_opening_confirmation_mismatch")
        elif value != previous:
            raise BankStatementControlError(f"scb_carried_balance_mismatch:page={number}")
        page_rows, _ = parse_scb_pdf(Path("statement.pdf"), page, "", {"account_id":"local"}, {})
        anchors = re.findall(r"(?m)^\s*\d{2}/\d{2}/\d{2}\s+", page)
        if len(anchors) != len(page_rows):
            raise BankStatementControlError(f"scb_row_coverage_mismatch:page={number}")
        period = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
        start, end = [dt.datetime.strptime(v, "%d/%m/%Y").date() for v in period.groups()]
        previous = validate_rows(page_rows, value, start, end)
        rows.extend(page_rows)
    if not rows:
        raise BankStatementControlError("scb_transactions_missing")
    for label in (r"TOTAL AMOUNTS \(Debit\)", r"TOTAL AMOUNTS \(Credit\)", r"TOTAL ITEMS"):
        if len(re.findall(label, text)) != 1:
            raise BankStatementControlError("scb_printed_summary_missing_or_ambiguous")
    check = parse_expected_from_scb_text(text)
    if any(value is None for value in (check.expected_withdrawal_count, check.expected_deposit_count,
                                       check.expected_withdrawal_total, check.expected_deposit_total)):
        raise BankStatementControlError("scb_printed_summary_missing")
    finish_check(rows, check)
    check.opening_balance, check.closing_balance = opening, previous
    check.row_balance_check = check.row_coverage_check = check.page_check = "OK"
    # Closing is a printed transaction-row balance, not a separate summary.
    check.balance_check = "OK" if opening + check.expected_deposit_total - check.expected_withdrawal_total == previous else "MISMATCH"
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



def crosscheck_next_statement(text, rows, next_text):
    """Use the next same-account statement's printed opening as closing evidence."""
    def identity(value):
        if "THE SIAM COMMERCIAL BANK" not in value:
            raise BankStatementControlError("scb_adjacent_bank_mismatch")
        matches = re.findall(r"Account No\.[\s\S]{0,100}?(\d{3}-\d{6}-\d)", value)
        values = {re.sub(r"\D", "", item) for item in matches}
        if len(values) != 1:
            raise BankStatementControlError("scb_adjacent_account_missing_or_ambiguous")
        return next(iter(values))
    if identity(text) != identity(next_text):
        raise BankStatementControlError("scb_adjacent_account_mismatch")
    period = r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})"
    current_period = re.search(period, text)
    next_period = re.search(period, next_text)
    if not current_period or not next_period:
        raise BankStatementControlError("scb_adjacent_period_missing")
    end = dt.datetime.strptime(current_period.group(2), "%d/%m/%Y").date()
    start = dt.datetime.strptime(next_period.group(1), "%d/%m/%Y").date()
    if start != end + dt.timedelta(days=1):
        raise BankStatementControlError("scb_adjacent_period_not_consecutive")
    _, next_check = checked_scb_statement(next_text)
    if any(getattr(next_check, name) != "OK" for name in CHECKS):
        raise BankStatementControlError("scb_adjacent_statement_controls_failed")
    if Decimal(rows[-1]["balance"]) != next_check.opening_balance:
        raise BankStatementControlError("scb_adjacent_closing_mismatch")
    return {"status": "MATCH", "method": "next_statement_printed_opening",
            "next_statement_text_sha256": hashlib.sha256(next_text.encode()).hexdigest(),
            "next_statement_controls": {name: getattr(next_check, name) for name in CHECKS}}

def convert_text(text, opening_balance=None, csv_path=None, *, next_statement_text=None, require_independent_closing=False):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    period = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not period:
        raise BankStatementControlError("statement_period_missing")
    start, end = [dt.datetime.strptime(v, "%d/%m/%Y").date() for v in period.groups()]
    if start > end: raise BankStatementControlError("statement_period_invalid")
    rule = {"account_id": "local"}
    if BANK == "kbank":
        if not re.search(r"KASIKORN|KBank", text, re.I):
            raise BankStatementControlError("unsupported_bank_header")
        if csv_path is None:
            rows, check = parse_kbank_pdf(Path("statement.pdf"), text, "", rule, {})
            required = CHECKS
        else:
            rows, check = parse_kbank_csv(csv_path, "", rule, {})
            with csv_path.open(encoding="utf-8-sig", newline="") as stream:
                raw = list(csv.reader(stream))
            opening_rows = [r for r in raw if len(r)>8 and r[3].strip()=="Beginning Balance"]
            if len(opening_rows)!=1 or not opening_rows[0][8].strip():
                raise BankStatementControlError("csv_opening_balance_missing_or_ambiguous")
            opening = Decimal(opening_rows[0][8].replace(",", ""))
            dates = [r for r in raw if len(r)>1 and re.match(r"(?:\d{4}/\d{1,2}/\d{2}|\d{2}-\d{2}-\d{2})$",r[1].strip()) and (len(r)<4 or r[3].strip()!="Beginning Balance")]
            if len(dates)!=len(rows): raise BankStatementControlError("csv_row_coverage_mismatch")
            for r in dates:
                debit=Decimal(r[4].replace(",", "") or "0"); credit=Decimal(r[6].replace(",", "") or "0")
                if debit<0 or credit<0 or bool(debit)==bool(credit):
                    raise BankStatementControlError("csv_invalid_debit_credit")
            check.opening_balance=opening
            check.closing_balance=validate_rows(rows,opening,start,end)
            check.row_balance_check=check.row_coverage_check="OK"
            check.balance_check="OK" if opening+check.expected_deposit_total-check.expected_withdrawal_total==check.closing_balance else "MISMATCH"
            required=tuple(n for n in CHECKS if n!="page_check")
    elif BANK == "scb":
        if "THE SIAM COMMERCIAL BANK" not in text:
            raise BankStatementControlError("unsupported_bank_header")
        rows, check = checked_scb_statement(text, opening_balance)
        validate_rows(rows, check.opening_balance, start, end)
        required=CHECKS
    else:
        if "LH BANK" not in text.upper(): raise BankStatementControlError("unsupported_bank_header")
        if opening_balance is None: raise BankStatementControlError("independent_opening_balance_required")
        account = re.search(r'Account Number\s*:\s*([\d-]+)',text)
        if not account: raise BankStatementControlError("account_header_missing")
        rule["masked_tail"]=re.sub(r"\D","",account.group(1))[-4:]
        rows, check = parse_lhb_pdf(Path("statement.pdf"),text,"",rule,{})
        if check.opening_balance!=Decimal(opening_balance):
            raise BankStatementControlError("independent_opening_balance_mismatch")
        validate_rows(rows,Decimal(opening_balance),start,end)
        required=("row_balance_check","row_coverage_check","page_check")
    if not rows or any(getattr(check,n)!="OK" for n in required):
        raise BankStatementControlError("statement_controls_failed")
    fingerprint=hashlib.sha256(text.encode()).hexdigest()
    output=[]
    for i,row in enumerate(rows,1):
        row["external_id"]=f"{BANK}-v1-{fingerprint}-{i:06d}"
        if BANK=="lh-bank": row["note"] += "; " + row["review_reason"].lstrip("; ")
        output.append({k:row[k] for k in COLUMNS})
    full = all(getattr(check,n)=="OK" for n in required) and BANK in {"kbank", "scb"}
    report={"status":"LOCAL_CONVERSION_PASSED" if full else "EXPERIMENTAL_REVIEW_REQUIRED",
            "bank":BANK,"rows":len(rows),"currency":"THB","checks":{n:getattr(check,n) for n in CHECKS},
            "controls":asdict(check),"input_format":"csv" if csv_path else "pdf-text",
            "opening_balance_source":"caller_confirmed" if BANK=="lh-bank" else "statement",
            "closing_balance_source":"last_transaction_row" if BANK=="scb" else "statement",
            "independent_closing_summary_check":"NOT_CHECKED" if BANK=="scb" else "NOT_APPLICABLE",
            "firefly_import_tested":False,"validation_scope":"single_input_conversion_only"}
    report["independent_closing_summary_check"] = "NOT_AVAILABLE"
    report["independent_closing_balance_check"] = "NOT_PROVIDED"
    report["source_control_gaps"] = ["separately_printed_closing_summary"]
    if next_statement_text is not None:
        adjacent = crosscheck_next_statement(text, rows, next_statement_text)
        report["independent_closing_balance_check"] = adjacent["status"]
        report["independent_closing_evidence"] = adjacent
    elif require_independent_closing:
        raise BankStatementControlError("scb_independent_closing_evidence_required")
    report["validation_scope"] = "single_statement_conversion_with_explicit_control_sources"
    report.pop("real_statement_compatibility_tested", None)
    return output,report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--currency",choices=["THB"],required=True)
    parser.add_argument("--ask-password",action="store_true")
    parser.add_argument("--text-fixture",action="store_true")
    parser.add_argument("--opening-balance",help="Optional cross-check of the printed opening")
    parser.add_argument("--next-statement-text", type=Path, help="Extracted native text of the next consecutive same-account statement")
    parser.add_argument("--require-independent-closing", action="store_true")
    if BANK=="kbank": parser.add_argument("--csv",action="store_true",help="Normalize the supported KBank CSV layout")
    args=parser.parse_args()
    try:
        is_csv=getattr(args,"csv",False)
        if is_csv and args.text_fixture: raise BankStatementControlError("input_modes_conflict")
        text=args.input.read_text(encoding="utf-8-sig") if args.text_fixture or is_csv else read_pdf(args.input,args.ask_password)
        next_text = args.next_statement_text.read_text(encoding="utf-8-sig") if args.next_statement_text else None
        rows,report=convert_text(text,args.opening_balance,args.input if is_csv else None,
                                next_statement_text=next_text, require_independent_closing=args.require_independent_closing)
        report["input_sha256"]=hashlib.sha256(args.input.read_bytes()).hexdigest()
        args.output_dir.mkdir(parents=True,exist_ok=False)
        with (args.output_dir/"transactions.csv").open("x",encoding="utf-8",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=COLUMNS); writer.writeheader(); writer.writerows(rows)
        (args.output_dir/"controls.json").write_text(json.dumps(report,indent=2,default=str)+"\n")
        print(json.dumps({"status":report["status"],"rows":len(rows),"checks":report["checks"]}))
        return 0
    except BankStatementControlError as exc:
        print(f"Conversion refused: {exc}",file=sys.stderr); return 2
    except Exception as exc:
        print(f"Conversion failed: {type(exc).__name__}",file=sys.stderr); return 2

if __name__=="__main__": raise SystemExit(main())
