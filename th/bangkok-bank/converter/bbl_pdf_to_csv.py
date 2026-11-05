#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Nolan Guo
"""Local draft: convert one supported Bangkok Bank PDF layout, without network access."""
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
    return dict(date=tx_date, description=kind, amount=str(signed), currency="THB",
                external_id="", balance=str(balance), note=channel,
                direction=direction, withdrawal=str(withdrawal), deposit=str(deposit))

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

def parse_statement_period_from_bangkok_text(text: str, fallback: str) -> str:
    match = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not match:
        return fallback
    start = dt.datetime.strptime(match.group(1), "%d/%m/%Y").date()
    end = dt.datetime.strptime(match.group(2), "%d/%m/%Y").date()
    start_label = start.strftime("%Y-%m")
    end_label = end.strftime("%Y-%m")
    return start_label if start_label == end_label else f"{start_label}_to_{end_label}"

def parse_expected_from_bangkok_text(text: str) -> SourceCheck:
    """Read the verified native BBL footer reading order, not derived totals.

    The debit count precedes its label; credit count/amount and debit amount
    follow the three label lines. An unknown or duplicate footer is unverified.
    """
    money = r"([0-9][0-9,]*\.\d{2})"
    pattern = (r"(?m)^(\d+)[^\d\n]*Total No\. of Debits[^\n]*\n"
               r"[^\n]*Total No\. of Credits[^\n]*Total Credit Amount[^\n]*\n"
               r"[^\n]*Total Debit Amount[^\n]*\n\s*(\d+)\s+" + money +
               r"\s*\n\s*" + money + r"(?=\s|$)")
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        return SourceCheck()
    debit_count, credit_count, credit_total, debit_total = matches[0]
    return SourceCheck(expected_withdrawal_count=int(debit_count),
                       expected_deposit_count=int(credit_count),
                       expected_withdrawal_total=Decimal(debit_total.replace(",", "")),
                       expected_deposit_total=Decimal(credit_total.replace(",", "")))

def parse_bangkok_bank_pdf(path: Path, text: str, period: str, rule: dict[str, Any], account: dict[str, Any]) -> tuple[list[dict[str, Any]], SourceCheck]:
    statement_period = parse_statement_period_from_bangkok_text(text, period)
    check = parse_expected_from_bangkok_text(text)
    date_line = re.compile(r"^\d{2}/\d{2}/\d{2}(?=\s)")
    money = re.compile(r"(?<![\d.])-?[0-9][0-9,]*\.\d{2}(?!\d)")
    period_match = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not period_match:
        raise BankStatementControlError("bbl_statement_period_missing")
    start, end = [dt.datetime.strptime(x, "%d/%m/%Y").date() for x in period_match.groups()]
    if start > end:
        raise BankStatementControlError("bbl_statement_period_invalid")
    rows: list[dict[str, Any]] = []
    sid = source_file_id(period, path, rule["account_id"])
    previous_balance: Decimal | None = None
    previous_date: dt.date | None = None
    source_transaction_count = 0
    pages = text.split("\f")
    printed_pages = []
    for page_number, page in enumerate(pages, 1):
        markers = re.findall(r"Page\s+(\d+)\s*/\s*(\d+)", page, re.I)
        if len(markers) != 1:
            raise BankStatementControlError(f"bbl_page_marker_missing_or_ambiguous:page={page_number}")
        printed_pages.append(tuple(map(int, markers[0])))
        page_transactions = 0
        for line_number, raw_line in enumerate(page.splitlines(), 1):
            line = raw_line.strip()
            if not date_line.match(line):
                continue
            location = f"page={page_number},line={line_number}"
            try:
                date = dt.datetime.strptime(line[:8], "%d/%m/%y").date()
            except ValueError:
                raise BankStatementControlError(f"bbl_invalid_transaction_date:{location}") from None
            if not start <= date <= end or (previous_date and date < previous_date):
                raise BankStatementControlError(f"bbl_transaction_date_out_of_order_or_period:{location}")
            tail = line[8:].strip()
            numbers = list(money.finditer(tail))
            description = tail[:numbers[0].start()].strip() if numbers else tail
            if re.fullmatch(r"B/F|Beginning Balance", description, re.I):
                if len(numbers) != 1 or page_transactions:
                    raise BankStatementControlError(f"bbl_invalid_brought_forward:{location}")
                brought = Decimal(numbers[0].group().replace(",", ""))
                if previous_balance is None:
                    if page_number != 1:
                        raise BankStatementControlError(f"bbl_opening_balance_missing:{location}")
                    check.opening_balance = brought
                elif brought != previous_balance:
                    raise BankStatementControlError(f"bbl_carried_balance_mismatch:{location}")
                previous_balance = brought
                previous_date = date
                continue
            source_transaction_count += 1
            if previous_balance is None:
                raise BankStatementControlError(f"bbl_opening_balance_missing:{location}")
            if len(numbers) != 2:
                raise BankStatementControlError(f"bbl_transaction_amount_layout_unsupported:{location}")
            amount, balance = [Decimal(value.group().replace(",", "")) for value in numbers]
            delta = balance - previous_balance
            if amount <= 0 or abs(delta) != amount:
                raise BankStatementControlError(f"bbl_row_balance_mismatch:{location}")
            direction = "deposit" if delta > 0 else "withdrawal"
            row = base_transaction_row(
                sid, path, rule, account, statement_period, len(rows) + 1,
                date.isoformat(), "", direction,
                amount if direction == "withdrawal" else Decimal("0"),
                amount if direction == "deposit" else Decimal("0"),
                delta, balance, tail[numbers[-1].end():].strip(), description,
                f"balance_delta={delta}", "high")
            row["source_page"] = str(page_number)
            # Preserve a real zero closing balance (the shared builder uses blanks).
            row["balance"] = str(balance)
            rows.append(row)
            previous_balance = balance
            previous_date = date
            page_transactions += 1
    if not rows or check.opening_balance is None:
        raise BankStatementControlError("bbl_opening_balance_or_transactions_missing")
    check.page_check = "OK" if printed_pages == [(n, len(pages)) for n in range(1, len(pages) + 1)] else "MISMATCH"
    check.closing_balance = previous_balance
    check.row_balance_check = "OK"
    check.row_coverage_check = "OK" if len(rows) == source_transaction_count else "MISMATCH"
    finish_check(rows, check)
    if check.expected_withdrawal_total is not None and check.expected_deposit_total is not None:
        check.balance_check = ("OK" if check.opening_balance + check.expected_deposit_total -
                               check.expected_withdrawal_total == check.closing_balance else "MISMATCH")
    return rows, check

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

def convert_text(text):
    # Only this native English-label layout is supported; no OCR or auto-repair.
    if not re.search(r"Bangkok\s+Bank\s+Public\s+Company\s+Limited", text, re.I):
        raise BankStatementControlError("unsupported_bank_header")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    rows, check = parse_bangkok_bank_pdf(Path("statement.pdf"), text, "", {"account_id": "local"}, {})
    if any(getattr(check, name) != "OK" for name in CHECKS):
        raise BankStatementControlError("statement_controls_failed")
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
    output = []
    for i, row in enumerate(rows, 1):
        row["external_id"] = f"bblpdf-v1-{fingerprint}-{i:06d}"
        output.append({key: row[key] for key in COLUMNS})
    report = {"status": "LOCAL_CONVERSION_PASSED", "layout": "native-bbl-english-labels-v1",
              "rows": len(output), "currency": "THB", "text_sha256": fingerprint,
              "checks": {name: getattr(check, name) for name in CHECKS},
              "controls": asdict(check), "firefly_import_tested": False}
    return output, report


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; existing directories are refused")
    parser.add_argument("--currency", required=True, choices=["THB"], help="Explicitly confirm this is a THB statement")
    parser.add_argument("--ask-password", action="store_true")
    parser.add_argument("--text-fixture", action="store_true", help="Use an extracted-text fixture instead of PDF")
    args = parser.parse_args()
    try:
        text = args.input.read_text(encoding="utf-8") if args.text_fixture else read_pdf(args.input, args.ask_password)
        rows, report = convert_text(text)
        report["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
        # Create outputs only after every statement control passes.
        args.output_dir.mkdir(parents=True, exist_ok=False)
        with (args.output_dir / "transactions.csv").open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        (args.output_dir / "controls.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    except BankStatementControlError as exc:
        print(f"Conversion refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Avoid leaking file paths, PDF contents, or credentials in diagnostics.
        print(f"Conversion failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "rows": len(rows), "checks": report["checks"]}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
