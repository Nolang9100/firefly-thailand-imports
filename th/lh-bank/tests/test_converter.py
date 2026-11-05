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
        rows,report=bank.convert_text(TEXT,OPENING,review_only=True)
        self.assertEqual([r["amount"] for r in rows],["-10.00","30.00"])
        self.assertEqual(report["status"],"LOCAL_CONVERSION_PASSED" if bank.BANK=="kbank" else "REVIEW_ONLY_NOT_IMPORT_READY")
        self.assertEqual(str(report["controls"]["closing_balance"]),"120.00")
        if bank.BANK!="kbank": self.assertEqual(report["checks"]["balance_check"],"NOT_CHECKED")

    def test_bad_balance_refused(self):
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT.replace("120.00","121.00"),OPENING)

    def test_bad_date_refused(self):
        bad=TEXT.replace("03-09-26","03-10-26") if bank.BANK=="kbank" else TEXT.replace("03/09/26","03/10/26") if bank.BANK=="scb" else TEXT.replace("03/09/2026","03/10/2026")
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad,OPENING,review_only=True)

    def test_source_controls_refused(self):
        bad=TEXT.replace("Withdrawal 1 Items","Withdrawal 2 Items") if bank.BANK=="kbank" else TEXT.replace("TOTAL ITEMS 1 1","TOTAL ITEMS 2 1") if bank.BANK=="scb" else TEXT.replace("Page 1 / 1","Page 1 / 2")
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad,OPENING,review_only=True)

    def test_header_refused(self):
        bad=TEXT.replace("KASIKORNBANK","OTHER") if bank.BANK=="kbank" else TEXT.replace("THE SIAM COMMERCIAL BANK","OTHER") if bank.BANK=="scb" else TEXT.replace("LH BANK","OTHER")
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad,OPENING,review_only=True)

    def test_opening_confirmation(self):
        if bank.BANK=="kbank":
            bad=TEXT.replace("01-09-26 100.00Beginning Balance\n","")
            with self.assertRaises(bank.BankStatementControlError): bank.convert_text(bad)
        else:
            with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT)
            with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT,"101.00",review_only=True)

    def test_config_and_stable_ids(self):
        cfg=json.loads((ROOT/"default.json").read_text())
        validate(cfg,json.loads((ROOT/"validation/importer-v2.3.5-v3.schema.json").read_text()))
        self.assertEqual(cfg["default_account"],0); self.assertEqual(cfg["mapping"],[])
        first,_=bank.convert_text(TEXT,OPENING,review_only=True); second,_=bank.convert_text(TEXT,OPENING,review_only=True)
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
            self.assertEqual(len(bank.convert_text(bank.read_pdf(pdf),OPENING,review_only=True)[0]),2)
            writer=PdfWriter()
            for page in PdfReader(pdf).pages: writer.add_page(page)
            writer.encrypt("synthetic-test-only"); encrypted=Path(d)/"encrypted.pdf"
            with encrypted.open("wb") as f: writer.write(f)
            with self.assertRaises(bank.BankStatementControlError): bank.read_pdf(encrypted)
            with patch.object(bank.getpass,"getpass",return_value="synthetic-test-only"):
                self.assertEqual(len(bank.convert_text(bank.read_pdf(encrypted,True),OPENING,review_only=True)[0]),2)
            out=Path(d)/"out"
            cmd=[sys.executable,str(ROOT/"converter/statement_to_csv.py"),str(pdf),"--review-only","--currency","THB","--output-dir",str(out)]
            if OPENING: cmd += ["--opening-balance",OPENING]
            result=subprocess.run(cmd,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            with (out/"review-candidates.csv").open() as f: self.assertEqual(len(list(csv.DictReader(f))),2)
            before=(out/"review-candidates.csv").read_bytes()
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,2)
            self.assertEqual(before,(out/"review-candidates.csv").read_bytes())

    def test_failure_no_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"bad.txt";src.write_text("Unsupported bank")
            out=Path(d)/"out"
            cmd=[sys.executable,str(ROOT/"converter/statement_to_csv.py"),str(src),"--review-only","--text-fixture","--currency","THB","--output-dir",str(out)]
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


    def test_external_evidence_schema(self):
        from jsonschema import FormatChecker
        validate(json.loads((ROOT/"examples/synthetic-controls.json").read_text()),
                 json.loads((ROOT/"templates/control-evidence.schema.json").read_text()),
                 format_checker=FormatChecker())

    def test_default_refuses_missing_full_evidence_even_with_opening(self):
        with self.assertRaises(bank.BankStatementControlError): bank.convert_text(TEXT,"100.00")
        rows,report=bank.convert_text(TEXT,review_only=True)
        self.assertEqual(len(rows),2)
        self.assertFalse(report["csv_export_ready"])
        self.assertEqual(report["opening_balance_source"],"first_row_derived_not_independent")

    def test_complete_attested_controls_match_without_relabeling_source(self):
        rows,report=bank.convert_text(TEXT,controls_json=ROOT/"examples/synthetic-controls.json")
        self.assertEqual(report["status"],"LOCAL_CONVERSION_WITH_ATTESTED_CONTROLS")
        self.assertTrue(report["csv_export_ready"])
        self.assertFalse(report["formal_source_approval"])
        self.assertEqual(report["checks"]["count_check"],"NOT_CHECKED")
        self.assertEqual(report["checks"]["balance_check"],"NOT_CHECKED")
        self.assertEqual(report["external_controls"]["status"],"MATCH")
        self.assertFalse(report["external_controls"]["semantic_source_extraction_verified_by_converter"])

    def test_scoped_provenance_wrong_or_stale_controls_rejected(self):
        import copy,hashlib
        original=json.loads((ROOT/"examples/synthetic-controls.json").read_text())
        mutations=[]
        wrong=copy.deepcopy(original);wrong["statement_text_sha256"]="0"*64;mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["bank"]="scb";mutations.append(wrong)
        wrong=copy.deepcopy(original);del wrong["controls"]["deposit_total"];mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["opening_balance"]["value"]="101.00";mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["withdrawal_count"]["value"]="2";mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["closing_balance"]["value"]="NaN";mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["deposit_count"]["value"]="-1";mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["opening_balance"]["evidence"]["sha256"]="0"*64;mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["opening_balance"]["evidence"]["reviewed_by"]="";mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["opening_balance"]["evidence"]["reviewed_at"]="2026-11-05T00:00:00";mutations.append(wrong)
        wrong=copy.deepcopy(original);wrong["controls"]["opening_balance"]["evidence"]["kind"]="converter_output";mutations.append(wrong)
        with tempfile.TemporaryDirectory() as d:
            from shutil import copyfile
            copyfile(ROOT/"examples/synthetic-independent-controls.txt",Path(d)/"synthetic-independent-controls.txt")
            path=Path(d)/"controls.json"
            for wrong in mutations:
                path.write_text(json.dumps(wrong))
                with self.subTest(bundle=wrong),self.assertRaises(bank.BankStatementControlError):
                    bank.convert_text(TEXT,controls_json=path)
            # The exact target statement cannot serve as independent evidence.
            target=Path(d)/"target.txt";target.write_text(TEXT.strip())
            wrong=copy.deepcopy(original)
            wrong["controls"]["opening_balance"]["evidence"].update(file="target.txt",sha256=hashlib.sha256(target.read_bytes()).hexdigest())
            path.write_text(json.dumps(wrong))
            with self.assertRaises(bank.BankStatementControlError):bank.convert_text(TEXT,controls_json=path)

    def test_verified_cli_and_review_cli_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/"verified"
            command=[sys.executable,str(ROOT/"converter/statement_to_csv.py"),str(ROOT/"examples/synthetic-statement.txt"),"--text-fixture","--currency","THB","--output-dir",str(out),"--controls-json",str(ROOT/"examples/synthetic-controls.json")]
            result=subprocess.run(command,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((out/"transactions.csv").exists())
            self.assertFalse((out/"review-candidates.csv").exists())
            report=json.loads((out/"controls.json").read_text())
            self.assertFalse(report["formal_source_approval"])

if __name__=="__main__": unittest.main()
