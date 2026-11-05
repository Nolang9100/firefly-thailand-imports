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


BANK = 'kbank'

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

def parse_kbank_csv_date(raw: str) -> str:
    text = str(raw).strip()
    for separator in ("/", "-"):
        parts = text.split(separator)
        if len(parts) != 3:
            continue
        day = int(parts[0][-2:])
        month = int(parts[1])
        year_part = int(parts[2])
        year = 2000 + year_part if year_part < 100 else year_part
        return dt.date(year, month, day).isoformat()
    raise ValueError(f"Unexpected KBank CSV date: {raw}")

def parse_expected_from_kbank_text(text: str) -> SourceCheck:
    # This verified native layout prints withdrawal and ending balances before
    # their labels, and deposit total after Ending Balance. Do not derive them.
    header = text.split("\f")[0].split("PAGE/OF")[0]
    compact = " ".join(header.split())
    thai = re.findall(
        r"([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+"
        r"รวมถอนเงิน\s+([0-9,]+)\s+รายการ\s+"
        r"รวมฝากเงิน\s+([0-9,]+)\s+รายการ\s+"
        r"ยอดยกไป\s+([0-9,]+\.\d{2})", compact)
    if len(thai) == 1:
        withdrawal, closing, withdrawal_count, deposit_count, deposit = thai[0]
        return SourceCheck(expected_withdrawal_count=int(withdrawal_count.replace(",", "")),
                           expected_deposit_count=int(deposit_count.replace(",", "")),
                           expected_withdrawal_total=Decimal(withdrawal.replace(",", "")),
                           expected_deposit_total=Decimal(deposit.replace(",", "")),
                           expected_closing_balance=Decimal(closing.replace(",", "")))
    matches = re.findall(
        r"([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+"
        r"Total Withdrawal\s+([0-9,]+)\s+Items\s+"
        r"Total Deposit\s+([0-9,]+)\s+Items\s+"
        r"Ending Balance\s+([0-9,]+\.\d{2})",
        compact,
    )
    if len(matches) != 1:
        return SourceCheck()
    withdrawal, closing, withdrawal_count, deposit_count, deposit = matches[0]
    return SourceCheck(
        expected_withdrawal_count=int(withdrawal_count.replace(",", "")),
        expected_deposit_count=int(deposit_count.replace(",", "")),
        expected_withdrawal_total=Decimal(withdrawal.replace(",", "")),
        expected_deposit_total=Decimal(deposit.replace(",", "")),
        expected_closing_balance=Decimal(closing.replace(",", "")),
    )

def parse_statement_period_from_kbank_text(text: str, fallback: str) -> str:
    match = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not match:
        return fallback
    start = dt.datetime.strptime(match.group(1), "%d/%m/%Y").date()
    end = dt.datetime.strptime(match.group(2), "%d/%m/%Y").date()
    start_label = start.strftime("%Y-%m")
    end_label = end.strftime("%Y-%m")
    return start_label if start_label == end_label else f"{start_label}_to_{end_label}"

def parse_kbank_pdf(path: Path, text: str, period: str, rule: dict[str, Any], account: dict[str, Any]) -> tuple[list[dict[str, Any]], SourceCheck]:
    check = parse_expected_from_kbank_text(text)
    statement_period = parse_statement_period_from_kbank_text(text, period)
    period_match = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not period_match:
        raise BankStatementControlError("kbank_statement_period_missing")
    start, end = [dt.datetime.strptime(x, "%d/%m/%Y").date() for x in period_match.groups()]
    if start > end:
        raise BankStatementControlError("kbank_statement_period_invalid")
    anchor = re.compile(r"^\d{2}-\d{2}-\d{2}(?=\s)")
    money = re.compile(r"(?<![\d.])-?[0-9][0-9,]*\.\d{2}(?!\d)")
    beginning = re.compile(r"^\d{2}-\d{2}-\d{2}\s+(-?[0-9][0-9,]*\.\d{2})\s*(?:Beginning Balance|ยอดยกมา)$")
    kinds = ("Inter-Region Transfer Deposit", "Transfer Withdrawal", "Automatic Deposit",
             "Transfer Deposit", "Interest Deposit", "Withholding Tax Payable",
             "SMS Alert Fee / Other Fee", "Cheque Withdrawal", "Service Fee", "Direct Debit",
             "Payment", "Withdrawal", "Deposit", "Bahtnet Deposit",
             "รับโอนเงินอัตโนมัติ", "คืนค่าสินค้าและบริการ", "รับโอนเงิน", "โอนเงิน", "ชำระเงิน", "หักบัญชี")
    kind_re = re.compile("(" + "|".join(re.escape(kind) for kind in kinds) + r")\s+([0-9][0-9,]*\.\d{2})(?!\d)")
    deposit_kinds = {"Inter-Region Transfer Deposit", "Transfer Deposit", "Automatic Deposit", "Interest Deposit", "Bahtnet Deposit", "Deposit", "รับโอนเงินอัตโนมัติ", "คืนค่าสินค้าและบริการ", "รับโอนเงิน"}
    sid = source_file_id(period, path, rule["account_id"])
    pages = text.split("\f")
    printed_pages = []
    rows: list[dict[str, Any]] = []
    previous_balance: Decimal | None = None
    previous_date: dt.date | None = None
    source_count = 0
    for page_number, page in enumerate(pages, 1):
        markers = re.findall(r"PAGE/OF\)?\s+(\d+)\s*/\s*(\d+)", page)
        if len(markers) != 1:
            raise BankStatementControlError(f"kbank_page_marker_missing_or_ambiguous:page={page_number}")
        printed_pages.append(tuple(map(int, markers[0])))
        blocks = []
        current = None
        for line_number, raw_line in enumerate(page.splitlines(), 1):
            line = raw_line.strip()
            if anchor.match(line):
                if current:
                    blocks.append(current)
                current = {"line": line_number, "text": line}
            elif current and line.startswith(("KBPDF", "Issued by K PLUS", "For more information")):
                blocks.append(current)
                current = None
            elif current and line:
                current["text"] += " " + line
        if current:
            blocks.append(current)
        page_opening_seen = False
        page_transactions = 0
        for block in blocks:
            item = block["text"]
            location = f"page={page_number},line={block['line']}"
            try:
                date = dt.datetime.strptime(item[:8], "%d-%m-%y").date()
            except ValueError:
                raise BankStatementControlError(f"kbank_invalid_date:{location}") from None
            if not start <= date <= end or (previous_date and date < previous_date):
                raise BankStatementControlError(f"kbank_date_out_of_order_or_period:{location}")
            opening = beginning.fullmatch(item)
            if opening:
                if page_opening_seen or page_transactions:
                    raise BankStatementControlError(f"kbank_duplicate_or_misplaced_beginning:{location}")
                value = Decimal(opening.group(1).replace(",", ""))
                if previous_balance is None:
                    if page_number != 1:
                        raise BankStatementControlError(f"kbank_opening_missing:{location}")
                    check.opening_balance = value
                elif value != previous_balance:
                    raise BankStatementControlError(f"kbank_carried_balance_mismatch:{location}")
                previous_balance = value
                previous_date = date
                page_opening_seen = True
                continue
            source_count += 1
            if previous_balance is None or not page_opening_seen:
                raise BankStatementControlError(f"kbank_opening_missing:{location}")
            dated = re.match(r"^\d{2}-\d{2}-\d{2}\s+(?:(\d{2}:\d{2})\s+)?", item)
            if not dated:
                raise BankStatementControlError(f"kbank_transaction_time_missing:{location}")
            transaction_time = dated.group(1) or ""
            if transaction_time:
                try:
                    dt.datetime.strptime(transaction_time, "%H:%M")
                except ValueError:
                    raise BankStatementControlError(f"kbank_transaction_time_invalid:{location}") from None
            body = item[dated.end():]
            matches = list(kind_re.finditer(body))
            if len(matches) != 1:
                raise BankStatementControlError(f"kbank_kind_or_amount_unsupported_or_ambiguous:{location}")
            match = matches[0]
            kind = match.group(1)
            amount = Decimal(match.group(2).replace(",", ""))
            prefix = body[:match.start()].strip()
            balances = list(money.finditer(prefix))
            if len(balances) != 1:
                raise BankStatementControlError(f"kbank_balance_missing_or_ambiguous:{location}")
            balance_token = balances[0]
            balance = Decimal(balance_token.group().replace(",", ""))
            channel = prefix[:balance_token.start()].strip()
            description = " ".join(part for part in
                                   (prefix[balance_token.end():].strip(), body[match.end():].strip()) if part)
            direction = "deposit" if kind in deposit_kinds else "withdrawal"
            delta = amount if direction == "deposit" else -amount
            if amount <= 0 or balance - previous_balance != delta:
                raise BankStatementControlError(f"kbank_row_balance_mismatch:{location}")
            row = base_transaction_row(
                sid, path, rule, account, statement_period, len(rows) + 1,
                date.isoformat(), transaction_time, direction,
                amount if direction == "withdrawal" else Decimal("0"),
                amount if direction == "deposit" else Decimal("0"),
                delta, balance, channel, kind, description, "high")
            row["source_page"] = str(page_number)
            row["balance"] = str(balance)
            if not transaction_time:
                row["review_reason"] += "; transaction_time_missing_in_source_text"
            rows.append(row)
            previous_balance = balance
            previous_date = date
            page_transactions += 1
        if not page_opening_seen:
            raise BankStatementControlError(f"kbank_page_beginning_missing:page={page_number}")
    if not rows or check.opening_balance is None:
        raise BankStatementControlError("kbank_opening_or_transactions_missing")
    check.closing_balance = previous_balance
    check.page_check = "OK" if printed_pages == [(n, len(pages)) for n in range(1, len(pages) + 1)] else "MISMATCH"
    check.row_balance_check = "OK"
    check.row_coverage_check = "OK" if source_count == len(rows) else "MISMATCH"
    finish_check(rows, check)
    if all(value is not None for value in (check.expected_withdrawal_total, check.expected_deposit_total, check.expected_closing_balance)):
        expected = check.opening_balance + check.expected_deposit_total - check.expected_withdrawal_total
        check.balance_check = "OK" if expected == check.closing_balance == check.expected_closing_balance else "MISMATCH"
    return rows, check

def parse_expected_from_kbank_csv(raw_rows: list[list[str]]) -> SourceCheck:
    check = SourceCheck()
    for row in raw_rows:
        joined = " ".join(cell.strip() for cell in row if cell)
        if "TOTAL WITHDRAWAL" in joined:
            numbers = [cell for cell in row if re.fullmatch(r"[0-9,]+(?:\.\d+)?", cell.strip())]
            if len(numbers) >= 2:
                check.expected_withdrawal_count = int(numbers[-2].replace(",", ""))
                check.expected_withdrawal_total = as_decimal(numbers[-1])
        if "TOTAL DEPOSIT" in joined:
            numbers = [cell for cell in row if re.fullmatch(r"[0-9,]+(?:\.\d+)?", cell.strip())]
            if len(numbers) >= 2:
                check.expected_deposit_count = int(numbers[-2].replace(",", ""))
                check.expected_deposit_total = as_decimal(numbers[-1])
    return check

def parse_statement_period_from_kbank_csv(raw_rows: list[list[str]], fallback: str) -> str:
    for row in raw_rows:
        for index, cell in enumerate(row):
            if str(cell).strip() != "Period":
                continue
            for value in row[index + 1:]:
                text = str(value).strip()
                match = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
                if not match:
                    continue
                start = dt.datetime.strptime(match.group(1), "%d/%m/%Y").date()
                end = dt.datetime.strptime(match.group(2), "%d/%m/%Y").date()
                start_label = start.strftime("%Y-%m")
                end_label = end.strftime("%Y-%m")
                return start_label if start_label == end_label else f"{start_label}_to_{end_label}"
    return fallback

def parse_kbank_csv(path: Path, period: str, rule: dict[str, Any], account: dict[str, Any]) -> tuple[list[dict[str, Any]], SourceCheck]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        raw_rows = list(csv.reader(f))
    check = parse_expected_from_kbank_csv(raw_rows)
    statement_period = parse_statement_period_from_kbank_csv(raw_rows, period)
    sid = source_file_id(period, path, rule["account_id"])
    rows: list[dict[str, Any]] = []
    sequence = 0
    for raw in raw_rows:
        if len(raw) < 9:
            continue
        raw_date = raw[1].strip() if len(raw) > 1 else ""
        kind = raw[3].strip() if len(raw) > 3 else ""
        if not re.fullmatch(r"(?:\d{4}/\d{1,2}/\d{2}|\d{2}-\d{2}-\d{2})", raw_date):
            continue
        if kind == "Beginning Balance":
            continue
        withdrawal = as_decimal(raw[4]) if len(raw) > 4 and raw[4].strip() else Decimal("0")
        deposit = as_decimal(raw[6]) if len(raw) > 6 and raw[6].strip() else Decimal("0")
        if not withdrawal and not deposit:
            continue
        sequence += 1
        direction = "withdrawal" if withdrawal else "deposit"
        signed = deposit - withdrawal
        rows.append(base_transaction_row(
            sid, path, rule, account, statement_period, sequence, parse_kbank_csv_date(raw_date),
            raw[2].strip() if len(raw) > 2 else "", direction, withdrawal, deposit, signed,
            as_decimal(raw[8]) if len(raw) > 8 and raw[8].strip() else Decimal("0"),
            raw[10].strip() if len(raw) > 10 else "", kind, raw[12].strip() if len(raw) > 12 else "",
            "high"
        ))
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


def convert_text(text, opening_balance=None, csv_path=None):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    period = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if not period:
        raise BankStatementControlError("statement_period_missing")
    start, end = [dt.datetime.strptime(v, "%d/%m/%Y").date() for v in period.groups()]
    if start > end: raise BankStatementControlError("statement_period_invalid")
    rule = {"account_id": "local"}
    if BANK == "kbank":
        branded = bool(re.search(r"KASIKORN|KBank", text, re.I))
        # Some native English exports identify the issuer in the K PLUS footer,
        # while the bank logo itself has no extractable text.
        pdf_signature = ("KBPDF" in text and "Issued by K PLUS" in text
                         and "PAGE/OF" in text and "Beginning Balance" in text
                         and "Total Withdrawal" in text and "Total Deposit" in text)
        csv_signature = "K-DEPOSIT STATEMENT OF SAVING ACCOUNT (WITH DETAIL)" in text
        if not (branded or (pdf_signature if csv_path is None else csv_signature)):
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
            if any(value is None for value in (check.expected_deposit_total, check.expected_withdrawal_total,
                                                check.expected_deposit_count, check.expected_withdrawal_count)):
                raise BankStatementControlError("csv_printed_summary_missing")
            check.balance_check="OK" if opening+check.expected_deposit_total-check.expected_withdrawal_total==check.closing_balance else "MISMATCH"
            required=tuple(n for n in CHECKS if n!="page_check")
    elif BANK == "scb":
        if "THE SIAM COMMERCIAL BANK" not in text:
            raise BankStatementControlError("unsupported_bank_header")
        if opening_balance is None: raise BankStatementControlError("independent_opening_balance_required")
        rows, check = parse_scb_pdf(Path("statement.pdf"), text, "", rule, {})
        dated_lines = re.findall(r"(?m)^\s*\d{2}/\d{2}/\d{2}\s+",text)
        if len(dated_lines)!=len(rows): raise BankStatementControlError("row_coverage_mismatch")
        check.opening_balance=Decimal(opening_balance)
        check.closing_balance=validate_rows(rows,check.opening_balance,start,end)
        check.row_balance_check=check.row_coverage_check="OK"
        required=("count_check","amount_check","row_balance_check","row_coverage_check")
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
    full = all(getattr(check,n)=="OK" for n in required) and BANK=="kbank"
    report={"status":"LOCAL_CONVERSION_PASSED" if full else "EXPERIMENTAL_REVIEW_REQUIRED",
            "bank":BANK,"rows":len(rows),"currency":"THB","checks":{n:getattr(check,n) for n in CHECKS},
            "controls":asdict(check),"input_format":"csv" if csv_path else "pdf-text",
            "opening_balance_source":"caller_confirmed" if BANK!="kbank" else "statement",
            "firefly_import_tested":False,"validation_scope":"single_input_conversion_only"}
    return output,report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--currency",choices=["THB"],required=True)
    parser.add_argument("--ask-password",action="store_true")
    parser.add_argument("--text-fixture",action="store_true")
    parser.add_argument("--opening-balance",help="Independently confirmed opening balance, required for SCB and LH Bank")
    if BANK=="kbank": parser.add_argument("--csv",action="store_true",help="Normalize the supported KBank CSV layout")
    args=parser.parse_args()
    try:
        is_csv=getattr(args,"csv",False)
        if is_csv and args.text_fixture: raise BankStatementControlError("input_modes_conflict")
        text=args.input.read_text(encoding="utf-8-sig") if args.text_fixture or is_csv else read_pdf(args.input,args.ask_password)
        rows,report=convert_text(text,args.opening_balance,args.input if is_csv else None)
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
