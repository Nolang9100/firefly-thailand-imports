# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Nolan Guo
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from jsonschema import validate
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"converter"))
import statement_to_csv as bank
TEXT=(ROOT/"examples/synthetic-statement.txt").read_text()
OPENING=None if bank.BANK=="kbank" else "100.00"

class Tests(unittest.TestCase):
    def test_synthetic_amounts_and_status(self):
        rows,report=bank.convert_text(TEXT,OPENING)
        self.assertEqual([r["amount"] for r in rows],["-10.00","30.00"])
        self.assertEqual(report["status"],"LOCAL_CONVERSION_PASSED" if bank.BANK=="kbank" else "EXPERIMENTAL_REVIEW_REQUIRED")
        self.assertEqual(str(report["controls"]["closing_balance"]),"120.00")
        if bank.BANK!="kbank": self.assertEqual(report["checks"]["balance_check"],"NOT_CHECKED")

    def test_bad_balance_refused(self):
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT.replace("120.00","121.00"),OPENING)

    def test_bad_date_refused(self):
        bad=TEXT.replace("03-09-26","03-10-26") if bank.BANK=="kbank" else TEXT.replace("03/09/26","03/10/26") if bank.BANK=="scb" else TEXT.replace("03/09/2026","03/10/2026")
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad,OPENING)

    def test_source_controls_refused(self):
        bad=TEXT.replace("Withdrawal 1 Items","Withdrawal 2 Items") if bank.BANK=="kbank" else TEXT.replace("TOTAL ITEMS 1 1","TOTAL ITEMS 2 1") if bank.BANK=="scb" else TEXT.replace("Page 1 / 1","Page 1 / 2")
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad,OPENING)

    def test_header_refused(self):
        bad=TEXT.replace("KASIKORNBANK","OTHER") if bank.BANK=="kbank" else TEXT.replace("THE SIAM COMMERCIAL BANK","OTHER") if bank.BANK=="scb" else TEXT.replace("LH BANK","OTHER")
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad,OPENING)

    def test_opening_confirmation(self):
        if bank.BANK=="kbank":
            bad=TEXT.replace("01-09-26 100.00Beginning Balance\n","")
            with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad)
        else:
            with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT)
            with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT,"101.00")

    def test_config_and_stable_ids(self):
        cfg=json.loads((ROOT/"default.json").read_text())
        validate(cfg,json.loads((ROOT/"validation/importer-v2.3.5-v3.schema.json").read_text()))
        self.assertEqual(cfg["default_account"],0); self.assertEqual(cfg["mapping"],[])
        first,_=bank.convert_text(TEXT,OPENING); second,_=bank.convert_text(TEXT,OPENING)
        self.assertEqual(first,second); self.assertEqual(len({r["external_id"] for r in first}),2)
        self.assertEqual(len(cfg["roles"]),len(bank.COLUMNS))

    def test_plain_encrypted_pdf_and_cli(self):
        from reportlab.pdfgen.canvas import Canvas
        from pypdf import PdfReader,PdfWriter
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            pdf=Path(d)/"synthetic.pdf"; c=Canvas(str(pdf))
            for page in TEXT.split("\f"):
                block=c.beginText(30,800)
                for line in page.strip().splitlines(): block.textLine(line)
                c.drawText(block); c.showPage()
            c.save()
            self.assertEqual(len(bank.convert_text(bank.read_pdf(pdf),OPENING)[0]),2)
            writer=PdfWriter()
            for page in PdfReader(pdf).pages: writer.add_page(page)
            writer.encrypt("synthetic-test-only"); encrypted=Path(d)/"encrypted.pdf"
            with encrypted.open("wb") as f: writer.write(f)
            with self.assertRaises(bank.BankStatementControlError): bank.read_pdf(encrypted)
            with patch.object(bank.getpass,"getpass",return_value="synthetic-test-only"):
                self.assertEqual(len(bank.convert_text(bank.read_pdf(encrypted,True),OPENING)[0]),2)
            out=Path(d)/"out"
            cmd=[sys.executable,str(ROOT/"converter/statement_to_csv.py"),str(pdf),"--currency","THB","--output-dir",str(out)]
            if OPENING: cmd += ["--opening-balance",OPENING]
            result=subprocess.run(cmd,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            with (out/"transactions.csv").open() as f: self.assertEqual(len(list(csv.DictReader(f))),2)
            before=(out/"transactions.csv").read_bytes()
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,2)
            self.assertEqual(before,(out/"transactions.csv").read_bytes())

    def test_failure_no_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"bad.txt";src.write_text("Unsupported bank")
            out=Path(d)/"out"
            cmd=[sys.executable,str(ROOT/"converter/statement_to_csv.py"),str(src),"--text-fixture","--currency","THB","--output-dir",str(out)]
            if OPENING: cmd += ["--opening-balance",OPENING]
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,2)
            self.assertFalse(out.exists())

    @unittest.skipUnless(bank.BANK=="kbank","KBank CSV only")
    def test_csv_normalization_and_bad_balance(self):
        p=ROOT/"examples/synthetic-export.csv"
        rows,report=bank.convert_text(p.read_text(),csv_path=p)
        self.assertEqual([r["amount"] for r in rows],["-10.00","30.00"])
        self.assertEqual(report["checks"]["page_check"],"NOT_CHECKED")
        for bad in [p.read_text().replace('90.00','91.00'),p.read_text().replace('TOTAL DEPOSIT,1','TOTAL DEPOSIT,2'),p.read_text().replace('10.00,,,,90.00','10.00,,5.00,,90.00')]:
            with tempfile.TemporaryDirectory() as d:
                q=Path(d)/"bad.csv";q.write_text(bad)
                with self.assertRaises((bank.BankStatementControlError,TypeError)): bank.convert_text(bad,csv_path=q)


    def test_native_english_issuer_footer_without_brand(self):
        native=TEXT.replace("KASIKORNBANK\n","")+"\nIssued by K PLUS"
        self.assertEqual(len(bank.convert_text(native)[0]),2)
        with self.assertRaises(bank.BankStatementControlError):
            bank.convert_text(native.replace("Issued by K PLUS",""))

    def test_native_csv_title_without_brand_and_missing_summary(self):
        p=ROOT/"examples/synthetic-export.csv"
        text=p.read_text().replace("KASIKORNBANK","K-DEPOSIT STATEMENT OF SAVING ACCOUNT (WITH DETAIL)")
        with tempfile.TemporaryDirectory() as d:
            q=Path(d)/"native.csv";q.write_text(text)
            self.assertEqual(len(bank.convert_text(text,csv_path=q)[0]),2)
            bad=text.replace("TOTAL DEPOSIT","Unknown")
            q.write_text(bad)
            with self.assertRaises(bank.BankStatementControlError):bank.convert_text(bad,csv_path=q)

if __name__=="__main__": unittest.main()
