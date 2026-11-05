# Siam Commercial Bank (SCB) — Thailand

## Status

Selected native-layout real samples passed local conversion and all six statement controls. No separate independent closing-summary balance exists in those samples.

Local contribution draft only. No real statements, account identifiers, credentials, bank connections, or Firefly writes are included. The fixture uses fictional data. The target configuration is Firefly III Data Importer 2.3.5, format v3.

## Supported inputs

One native THB text PDF layout adapted from the existing internal parser. Scanned PDFs, credit cards, foreign currencies, and unknown layouts are unsupported. Source-language labels and transaction kinds are retained in parser patterns; comments and documentation are English. Thai-font PDF extraction still needs real-layout verification.

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

The converter reads the printed `BALANCE BROUGHT FORWARD` on each page. An optional `--opening-balance` is a cross-check and cannot replace a missing printed opening.

## Controls and limitations

Printed debit/credit counts and totals, dated-row coverage, each page's printed opening, page sequence (Thai or English), cross-page carry balances, and row balances are checked. The opening plus printed credit totals minus printed debit totals must equal the last printed transaction balance. This equation is `balance_check`; the distinct `independent_closing_summary_check` remains `NOT_AVAILABLE` because no separately labeled closing summary was present in the tested sources. Output is `LOCAL_CONVERSION_PASSED` only when all six required statement controls pass.

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

Upstream path: `th/scb/default.json`, following the country/bank convention. This configuration consumes the companion converter's output, not the original PDF. Companion tools are hosted in the root repository, with upstream acceptance tracked separately. See the root licensing notices; upstream acceptance is separate.

## Independent closing through the next statement

A separate closing-summary field is absent in the tested source layout. The converter can additionally check the closing balance against the next consecutive same-account statement's printed opening:

```sh
.venv/bin/python converter/statement_to_csv.py current.pdf --currency THB --next-statement-text next-statement.txt --require-independent-closing --output-dir crosschecked-output
```

`next-statement.txt` must contain native extracted text with page breaks preserved as form-feed characters. The account number and bank must match; the next period must start on the day after the current period ends. The next statement must pass its own complete printed-opening, pagination, carry, count, amount, and balance controls. Its printed opening must equal the current terminal balance. Missing or conflicting supplied evidence is refused, rather than ignored.

The report distinguishes `independent_closing_summary_check: NOT_AVAILABLE` from `independent_closing_balance_check: MATCH` with method `next_statement_printed_opening`. Without next-statement evidence, the latter is `NOT_PROVIDED`. Add `--require-independent-closing` when this additional evidence is mandatory; it refuses output if none is supplied. A passing local conversion alone does not assert independent closing evidence.

Selected real July/August statements passed this adjacent-period check for July. Independent closing evidence for August is not supplied. The fictional pair under `examples/synthetic-current-with-account.txt` and `examples/synthetic-next-statement.txt` demonstrates the same contract without including real account or transaction data.
