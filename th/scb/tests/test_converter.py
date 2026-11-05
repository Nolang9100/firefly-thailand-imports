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
        self.assertEqual(report["status"],"LOCAL_CONVERSION_PASSED" if bank.BANK in {"kbank","scb"} else "EXPERIMENTAL_REVIEW_REQUIRED")
        self.assertEqual(str(report["controls"]["closing_balance"]),"120.00")
        if bank.BANK=="lh-bank": self.assertEqual(report["checks"]["balance_check"],"NOT_CHECKED")

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
        elif bank.BANK=="scb":
            self.assertEqual(len(bank.convert_text(TEXT)[0]),2)
            with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT,"101.00")
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


    def two_pages(self):
        header="THE SIAM COMMERCIAL BANK\n01/09/2026 - 30/09/2026\n"
        return (header+"BALANCE BROUGHT FORWARD) 100.00\n02/09/26 10:00 X2 APP 10.00 90.00 DESC : Example payment\nหน้า 1 / 2\n\f\n"+
                header+"BALANCE BROUGHT FORWARD) 90.00\n03/09/26 11:00 X1 APP 30.00 120.00 DESC : Example receipt\nTOTAL AMOUNTS (Debit) 10.00\nTOTAL AMOUNTS (Credit) 30.00\nTOTAL ITEMS 1 1\nหน้า 2 / 2")

    def test_native_thai_pagination_and_printed_openings(self):
        rows,report=bank.convert_text(self.two_pages())
        self.assertEqual(len(rows),2)
        self.assertTrue(all(v=="OK" for v in report["checks"].values()))
        self.assertEqual(report["opening_balance_source"],"statement")
        self.assertEqual(report["independent_closing_summary_check"],"NOT_AVAILABLE")
        self.assertEqual(report["closing_balance_source"],"last_transaction_row")

    def test_missing_duplicate_out_of_order_page_refused(self):
        t=self.two_pages()
        for bad in [t.replace("หน้า 2 / 2",""),t.replace("หน้า 2 / 2","หน้า 1 / 2"),t.replace("หน้า 1 / 2","หน้า 2 / 2")]:
            with self.subTest(case=bad),self.assertRaises(bank.BankStatementControlError):bank.convert_text(bad)

    def test_wrong_cross_page_carry_refused(self):
        with self.assertRaises(bank.BankStatementControlError):
            bank.convert_text(self.two_pages().replace("FORWARD) 90.00","FORWARD) 91.00"))

    def test_missing_printed_opening_refused_even_with_caller_value(self):
        with self.assertRaises(bank.BankStatementControlError):
            bank.convert_text(TEXT.replace("BALANCE BROUGHT FORWARD) 100.00\n",""),"100.00")

    def test_missing_summary_side_and_duplicate_summary_refused(self):
        for bad in [TEXT.replace("TOTAL AMOUNTS (Credit)","Unknown"),TEXT+"\nTOTAL ITEMS 1 1"]:
            with self.subTest(case=bad),self.assertRaises(bank.BankStatementControlError):bank.convert_text(bad)


    def adjacent_pair(self):
        current=TEXT.replace("THE SIAM COMMERCIAL BANK","THE SIAM COMMERCIAL BANK\nAccount No.\n000-000000-0")
        following=("THE SIAM COMMERCIAL BANK\nAccount No.\n000-000000-0\n01/10/2026 - 31/10/2026\n"
                   "BALANCE BROUGHT FORWARD) 120.00\n02/10/26 10:00 X2 APP 10.00 110.00 DESC : Fictional payment\n"
                   "TOTAL AMOUNTS (Debit) 10.00\nTOTAL AMOUNTS (Credit) 0.00\nTOTAL ITEMS 1 0\nPage 1 / 1")
        return current,following

    def test_adjacent_printed_opening_independently_checks_closing(self):
        current,following=self.adjacent_pair()
        _,report=bank.convert_text(current,next_statement_text=following,require_independent_closing=True)
        self.assertEqual(report["independent_closing_balance_check"],"MATCH")
        self.assertEqual(report["independent_closing_summary_check"],"NOT_AVAILABLE")
        self.assertEqual(report["independent_closing_evidence"]["method"],"next_statement_printed_opening")

    def test_adjacent_account_date_balance_and_summary_mismatch_refused(self):
        current,following=self.adjacent_pair()
        cases=[following.replace("000-000000-0","000-000001-0"),following.replace("01/10/2026","02/10/2026"),
               following.replace("FORWARD) 120.00","FORWARD) 121.00").replace("10.00 110.00","10.00 111.00"),
               following.replace("TOTAL ITEMS 1 0","TOTAL ITEMS 2 0")]
        for bad in cases:
            with self.subTest(statement=bad),self.assertRaises(bank.BankStatementControlError):
                bank.convert_text(current,next_statement_text=bad)
        with self.assertRaises(bank.BankStatementControlError):bank.convert_text(current,require_independent_closing=True)

    def test_adjacent_text_cli(self):
        current,following=self.adjacent_pair()
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"current.txt";src.write_text(current)
            nxt=Path(d)/"next.txt";nxt.write_text(following)
            out=Path(d)/"out"
            result=subprocess.run([sys.executable,str(ROOT/"converter/statement_to_csv.py"),str(src),"--text-fixture","--currency","THB","--output-dir",str(out),"--next-statement-text",str(nxt),"--require-independent-closing"],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((out/"controls.json").read_text())["independent_closing_balance_check"],"MATCH")

if __name__=="__main__": unittest.main()
