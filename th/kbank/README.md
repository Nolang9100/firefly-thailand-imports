# Kasikornbank (KBank) — Thailand

## Status

Local conversion controls implemented; selected real samples tested locally; Firefly acceptance pending.

Local contribution draft only. No real statements, account identifiers, credentials, bank connections, or Firefly writes are included. The fixture uses fictional data. The target configuration is Firefly III Data Importer 2.3.5, format v3.

## Supported inputs

One native THB text PDF layout adapted from the existing internal parser. Scanned PDFs, credit cards, foreign currencies, and unknown layouts are unsupported. Source-language labels and transaction kinds are retained in parser patterns; comments and documentation are English. Thai-font PDF extraction still needs real-layout verification.

KBank also includes normalization of one known CSV export layout. It is a preprocessing step, not a direct mapping for every KBank CSV export.

## Use

From this bank directory, use Python 3.10 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python converter/statement_to_csv.py statement.pdf --currency THB --output-dir converted-output
```

For encrypted PDFs, append `--ask-password` and enter the password through the hidden prompt. No credential file is read. The destination directory must not already exist. Real conversion outputs are private financial data.

Synthetic text example:

```sh
.venv/bin/python converter/statement_to_csv.py examples/synthetic-statement.txt --text-fixture --currency THB --output-dir sample-output
```

For the known CSV layout, replace the input with `examples/synthetic-export.csv` and use `--csv` instead of `--text-fixture`.

## Controls and limitations

PDF: all six controls are required. CSV: counts, amount totals, opening and running balances, dates, and dated-row coverage are required. Page control is not applicable to CSV and remains NOT_CHECKED. CSV does not provide an independently parsed closing-balance summary.

The output CSV columns are `date,description,amount,currency,external_id,balance,note`. Withdrawals are negative, deposits positive. Dates are Gregorian. Balances are retained for inspection and ignored by the import configuration. No opening balance, counterparty account, category, or transfer relationship is created automatically.

`default_account` is `0`, `mapping` is empty, user-rule application is disabled, and the import form remains visible. Select your own THB account in the importer.

External IDs use a full extracted-text fingerprint plus row sequence. Identical input gives stable IDs; changed exports or overlapping periods require separate duplicate review. These are not bank-issued transaction IDs.

## Tests

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

The distributable test suite uses only synthetic inputs; real-source checks remain private. See `validation/results.json` and `validation/tests.txt` for the executed checks. Selected local real-source validation is documented in the root `VALIDATION.md`. Actual isolated Firefly import/re-import acceptance remains pending.

For the fixture, expect withdrawal 10.00, deposit 30.00, net change +20.00, opening 100.00, closing 120.00 THB. Verify those values in a separate test ledger before considering any real import.

## Proposed contribution

Upstream path: `th/kbank/default.json`, following the country/bank convention. This configuration consumes the companion converter's output, not the original PDF. Companion tools are hosted in the root repository, with upstream acceptance tracked separately. See the root licensing notices; upstream acceptance is separate.
