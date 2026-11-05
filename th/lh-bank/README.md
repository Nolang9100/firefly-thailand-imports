# LH Bank — Thailand

## Scope

Local tools for one native THB account e-Statement PDF layout. No online banking connection, credit cards, scanned PDFs, or foreign currencies. `default.json` maps normalized CSV columns to Firefly III Data Importer 2.3.5 format v3.

This layout has running balances and page numbering but does not print an opening balance, debit/credit summary counts or totals, or a separate closing-balance summary. The tool distinguishes those missing source controls from successful row arithmetic. A derived opening is never presented as independent confirmation.

## Install

Use Python 3.10 or later from this bank directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Encrypted PDFs use `--ask-password` for a hidden prompt. Passwords are not saved. Output directories must not already exist.

## Review-only conversion

When independent controls are unavailable:

```sh
.venv/bin/python converter/statement_to_csv.py statement.pdf --currency THB --review-only --output-dir review-output
```

Outputs are `review-candidates.csv` and `controls.json`. Rows must pass date, signed-amount, running-balance, coverage, and pagination checks. The report retains `REVIEW_ONLY_NOT_IMPORT_READY`, `csv_export_ready: false`, and the missing source-control list. No `transactions.csv` is produced in this mode. Review candidates are not a completed source verification.

An optional `--opening-balance` is only an arithmetic cross-check; it does not supply or establish independent evidence. Default conversion refuses missing external control evidence even when this number is supplied.

## Conversion with externally attested controls

```sh
.venv/bin/python converter/statement_to_csv.py statement.pdf --currency THB --controls-json controls.json --output-dir checked-output
```

The bundle must cover six controls: opening balance, closing balance, withdrawal count, deposit count, withdrawal total, and deposit total. Use `templates/controls.template.json` and the contract in `templates/control-evidence.schema.json`. The blank template is intentionally invalid until genuine controls and provenance are supplied.

`statement_text_sha256` binds the bundle to the exact normalized statement text: CRLF/CR is converted to LF and leading/trailing whitespace is stripped. The hash is included in the review report. All controls are THB values represented as decimal strings, and counts are nonnegative integer strings.

Each control references a supporting bank document with a file path, SHA-256, source kind, precise locator, reviewer, and timezone-aware review timestamp. Paths are relative to the bundle. The tool checks file existence, hashes, statement scope, completeness, arithmetic, and every control's agreement with parsed rows. It refuses missing, changed, mismatched, or target-statement self-references. Do not use the converter output or a first-row-derived value as supporting evidence.

**Assurance boundary:** the bundle contains manually attested values. Hash validation proves which supporting file was referenced; it does not prove that a person transcribed or interpreted that file correctly. The converter does not extract or authenticate the supporting document's meaning. Reports therefore say `file_hashes_and_scope_checked_values_manually_attested`, retain missing printed-source controls, and keep `formal_source_approval: false`.

Successful local conversion writes `transactions.csv` and reports `LOCAL_CONVERSION_WITH_ATTESTED_CONTROLS`. `csv_export_ready` describes only this local output gate; it is not posting authorization or a completed finance review. Supplied but invalid evidence is refused even in review-only mode.

## Fictional examples

```sh
.venv/bin/python converter/statement_to_csv.py examples/synthetic-statement.txt --text-fixture --currency THB --review-only --output-dir sample-review
.venv/bin/python converter/statement_to_csv.py examples/synthetic-statement.txt --text-fixture --currency THB --controls-json examples/synthetic-controls.json --output-dir sample-attested
```

The fixture, supporting control document, bundle, reviewer identity, and all amounts are fictional test data. They must never be reused for real statements. `examples/review-output/` and `examples/attested-output/` show the two distinct output types.

## CSV mapping and duplicate handling

Columns are `date,description,amount,currency,external_id,balance,note`. Withdrawals are negative; deposits positive. The importer ignores the inspection-only balance column. Select your own THB asset account: `default_account` is `0`, mappings are empty, and user-rule execution is disabled. No opening balance, category, counterparty, or transfer relationship is automatically created.

External IDs combine a statement-text fingerprint and row number. Identical text gives stable IDs; different or overlapping exports require separate duplicate review.

## Validation

The selected real 20-page source produced 451 review candidates with consistent row arithmetic and pagination. Its independent control evidence is still absent, so default export remains blocked. No genuine external evidence bundle has been fabricated or marked as tested.

Synthetic tests cover both modes, complete attestation, mismatched values, incomplete or stale evidence, wrong statement binding, missing provenance, and self-reference rejection. Actual Firefly import/re-import remains untested. Run:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Real source files and outputs are private and are not distributed here. No formal ledger or external system was changed, and publication is separate from these validation results. See the root LICENSE, CONFIG-LICENSE, and NOTICE.md.
