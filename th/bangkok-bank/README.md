# Bangkok Bank (Thailand)

## Scope and status

This local draft converts one Bangkok Bank native text PDF layout into CSV for Firefly III Data Importer. It is **not** a configuration for a bank-native CSV export and does not connect to Bangkok Bank online.

Supported scope: THB statements with English date, balance, summary, and page labels. Scanned documents, credit-card statements, foreign currencies, Buddhist Era dates, and unknown layouts are not supported. Thai descriptions are preserved when they are successfully extracted; Thai-font PDF extraction has not yet been validated on real statements.

Target: Data Importer 2.3.5, configuration format v3. Schema validation and synthetic local conversion tests passed. Selected real-statement samples have now been tested locally; actual Firefly import/re-import tests are still pending.

## Files

| File or directory | Purpose |
| --- | --- |
| `default.json` | Import configuration for the converter's seven-column CSV |
| `converter/bbl_pdf_to_csv.py` | Standalone local PDF-to-CSV converter |
| `examples/synthetic-statement.txt` | Entirely fictional two-page statement text |
| `examples/converted/transactions.csv` | Three fictional transactions generated from the fixture |
| `examples/converted/controls.json` | Local statement-control results for the fixture |
| `tests/test_converter.py` | Positive, negative, PDF, encryption, and schema tests |
| `validation/` | Official schema, provenance, and local test results |

## Installation

Use Python 3.10 or later. From this bank directory, create an isolated environment and install dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Convert a statement

```sh
.venv/bin/python converter/bbl_pdf_to_csv.py statement.pdf --currency THB --output-dir converted-output
```

The currency option explicitly confirms that the input is a THB statement. For an encrypted PDF, add `--ask-password`; the password is entered through a hidden prompt and is not saved or passed on the command line. No internal account configuration, credential file, database, or network service is required.

The destination directory must not already exist. Conversion creates `transactions.csv` and `controls.json` only after all statement controls pass. Outputs created from real statements contain private financial data and do not belong in this contribution package.

To run the fictional text fixture:

```sh
.venv/bin/python converter/bbl_pdf_to_csv.py examples/synthetic-statement.txt --text-fixture --currency THB --output-dir sample-output
```

## Statement checks

The converter reads the printed statement period, opening balance, dated transaction rows, amounts, running balances, summary totals, and page markers. It determines each row's direction from its balance movement and verifies that the absolute movement equals the printed transaction amount.

All six controls must pass:

- Debit and credit transaction counts.
- Debit and credit amount totals.
- Opening balance plus credits minus debits equals closing balance.
- Every transaction's running-balance movement.
- Coverage of recognized dated transaction rows.
- Complete, ordered page numbering.

A missing or unrecognized summary, unsupported transaction row, balance discrepancy, or incomplete page sequence prevents CSV output. This does not guarantee support for other statement layouts.

## CSV mapping

| Position | CSV header | Importer role | Meaning |
| --- | --- | --- | --- |
| 1 | `date` | `date_transaction` | Gregorian date in `YYYY-MM-DD` format |
| 2 | `description` | `description` | Extracted transaction description |
| 3 | `amount` | `amount` | Negative for withdrawals; positive for deposits |
| 4 | `currency` | `currency-code` | `THB` |
| 5 | `external_id` | `external-id` | Converter-generated statement/row identifier |
| 6 | `balance` | `_ignore` | Running balance retained only for local inspection |
| 7 | `note` | `note` | Extracted channel text |

The JSON sets `default_account` to `0`; choose your own THB asset account in the importer. The mapping array is empty. Automatic user-rule application is disabled and the configuration form remains visible. No opening-balance transaction, category, counterparty account, or internal-transfer relationship is created by the converter.

## Duplicate handling

Each external ID combines a SHA-256 fingerprint of the extracted statement text and a transaction sequence number. Reprocessing identical text gives stable IDs while preserving separate rows.

These are not bank-issued transaction IDs. Different exports, changed text extraction, or overlapping statement periods may produce different IDs for the same transaction. Such overlaps require a separate review. The configuration requests cell-based duplicate detection using `external-id`; actual duplicate behavior in Firefly remains to be tested in an isolated ledger.

## Isolated importer acceptance test (pending)

1. Create a separate THB test account in an isolated Firefly ledger.
2. Select File import and upload `examples/converted/transactions.csv` together with `default.json`.
3. Select the test account and inspect the date, amount, currency, description, and external-ID mappings.
4. Expect withdrawal 10.00, deposit 50.00, withdrawal 20.00: net movement +20.00 THB.
5. Import into the isolated ledger and read back all three transactions.
6. Re-import the same file and verify that no duplicate transactions are created.

The CSV does not set an opening balance. If a fictional opening balance of 100.00 THB is separately established, the expected closing balance is 120.00 THB.

## Development tests

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Only synthetic data is included. Local tests cover schema structure, amounts and balances, Thai text preservation, stable IDs, zero closing balance, invalid controls, plain/encrypted PDF conversion, overwrite refusal, and failure without creating output.

## Contribution plan

The proposed upstream configuration path is `th/bangkok-bank/default.json`. It must be described as a configuration for this converter's output, rather than direct PDF support. Companion-tool hosting and documentation placement require coordination with upstream maintainers when publication is authorized.

Additional statement layouts and isolated import/re-import acceptance remain to be verified. Upstream contribution status is tracked separately from converter validation.
