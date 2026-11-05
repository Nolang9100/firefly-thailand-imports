# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Nolan Guo
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from jsonschema import validate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "converter"))
import bbl_pdf_to_csv as bbl
TEXT = (ROOT / "examples/synthetic-statement.txt").read_text()

class ConverterTests(unittest.TestCase):
    def test_balances_and_direction(self):
        rows, report = bbl.convert_text(TEXT)
        self.assertEqual([r["amount"] for r in rows], ["-10.00", "50.00", "-20.00"])
        self.assertEqual(report["controls"]["closing_balance"], bbl.Decimal("120.00"))
        self.assertTrue(all(v == "OK" for v in report["checks"].values()))

    def test_missing_or_bad_controls_are_refused(self):
        for text in [TEXT.replace("2Total", "3Total"), TEXT.replace("1 50.00", "1 51.00"),
                     TEXT.replace("Total Credit Amount", "Unknown"), TEXT.replace("Page 2 / 2", "Page 1 / 2"),
                     TEXT.replace("Page 2 / 2", ""), TEXT.replace("01/09/26 B/F 100.00\n", ""),
                     TEXT.replace("10.00 90.00", "11.00 90.00"),
                     TEXT.replace("02/09/26 PAYMENT 10.00 90.00 mPhone\n", ""),
                     TEXT.replace("03/09/26 TRANSFER 50.00 140.00", "03/09/26 TRANSFER unreadable"),
                     TEXT.replace("04/09/26", "01/08/26")]:
            with self.subTest(case=text):
                with self.assertRaises(bbl.BankStatementControlError): bbl.convert_text(text)

    def test_foreign_header_refused(self):
        with self.assertRaises(bbl.BankStatementControlError):
            bbl.convert_text(TEXT.replace("Bangkok Bank", "Other Bank"))

    def test_zero_closing_balance(self):
        text = TEXT.split("01/09/2026")[0] + "01/09/2026 - 30/09/2026\n01/09/26 B/F 10.00\n02/09/26 PAYMENT 10.00 0.00\n1Total No. of Debits\nTotal No. of Credits Total Credit Amount\nTotal Debit Amount\n0 0.00\n10.00\nPage 1 / 1"
        rows, _ = bbl.convert_text(text)
        self.assertEqual(rows[0]["balance"], "0.00")

    def test_thai_description(self):
        rows, _ = bbl.convert_text(TEXT.replace("PAYMENT", "ชำระเงิน"))
        self.assertEqual(rows[0]["description"], "ชำระเงิน")

    def test_same_input_stable_ids_and_distinct_rows(self):
        rows, _ = bbl.convert_text(TEXT)
        again, _ = bbl.convert_text(TEXT)
        self.assertEqual(rows, again)
        self.assertEqual(len({r["external_id"] for r in rows}), 3)

    def test_config_schema_and_roles(self):
        cfg = json.loads((ROOT / "default.json").read_text())
        validate(cfg, json.loads((ROOT / "validation/importer-v2.3.5-v3.schema.json").read_text()))
        self.assertEqual(cfg["default_account"], 0)
        self.assertEqual(cfg["mapping"], [])
        self.assertEqual(len(cfg["roles"]), len(bbl.COLUMNS))
        self.assertEqual(cfg["roles"][cfg["unique_column_index"]], "external-id")

    def test_pdf_and_encrypted_pdf_end_to_end(self):
        from reportlab.pdfgen.canvas import Canvas
        from pypdf import PdfReader, PdfWriter
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "synthetic.pdf"
            canvas = Canvas(str(pdf))
            for page in TEXT.split("\f"):
                block = canvas.beginText(40, 800)
                for line in page.strip().splitlines(): block.textLine(line)
                canvas.drawText(block)
                canvas.showPage()
            canvas.save()
            plain = bbl.read_pdf(pdf)
            self.assertEqual(len(bbl.convert_text(plain)[0]), 3)
            writer = PdfWriter()
            for page in PdfReader(pdf).pages: writer.add_page(page)
            writer.encrypt("synthetic-test-only")
            encrypted = Path(d) / "encrypted.pdf"
            with encrypted.open("wb") as f: writer.write(f)
            with self.assertRaises(bbl.BankStatementControlError): bbl.read_pdf(encrypted)
            from unittest.mock import patch
            with patch.object(bbl.getpass, "getpass", return_value="synthetic-test-only"):
                self.assertEqual(len(bbl.convert_text(bbl.read_pdf(encrypted, True))[0]), 3)
            out = Path(d) / "out"
            command = [sys.executable, str(ROOT / "converter/bbl_pdf_to_csv.py"), str(pdf), "--currency", "THB", "--output-dir", str(out)]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            with (out / "transactions.csv").open(encoding="utf-8", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 3)
            before = (out / "transactions.csv").read_bytes()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual(before, (out / "transactions.csv").read_bytes())

    def test_bad_input_creates_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/"bad.txt"
            src.write_text(TEXT.replace("Total Credit Amount", "Unknown"))
            out = Path(d)/"out"
            result = subprocess.run([sys.executable, str(ROOT/"converter/bbl_pdf_to_csv.py"), str(src), "--text-fixture", "--currency", "THB", "--output-dir", str(out)], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(out.exists())

if __name__ == "__main__": unittest.main()
