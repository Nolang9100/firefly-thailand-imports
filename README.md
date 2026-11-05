# Firefly III import tools for Thailand

Local PDF/CSV normalization tools and import configurations for **Bangkok Bank,
Kasikornbank (KBank), Siam Commercial Bank (SCB), and LH Bank**. These are
companion tools for Firefly III Data Importer, not direct online-bank connections.

## Supported formats

| Bank | Input | Guide | Import configuration |
| --- | --- | --- | --- |
| Bangkok Bank | One native THB text-PDF layout | [Guide](th/bangkok-bank/README.md) | [default.json](th/bangkok-bank/default.json) |
| KBank | Native THB text PDF and one known CSV export layout | [Guide](th/kbank/README.md) | [default.json](th/kbank/default.json) |
| SCB | Native THB text PDF with printed openings and Thai/English pagination | [Guide](th/scb/README.md) | [default.json](th/scb/default.json) |
| LH Bank | Native THB account e-Statement; review output or externally attested controls | [Guide](th/lh-bank/README.md) | [default.json](th/lh-bank/default.json) |

The JSON files consume the **normalized CSV produced by these tools**, not the
original PDF or an arbitrary bank CSV. Scanned PDFs, credit cards, foreign
currencies, and unknown layouts are unsupported. Documentation is English;
Thai text is retained where required by source formats or language tests.

## Quick start

Use Python 3.10 or later. From the chosen bank directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

For Bangkok Bank:

```sh
.venv/bin/python converter/bbl_pdf_to_csv.py statement.pdf --currency THB --output-dir converted-output
```

For KBank or SCB:

```sh
.venv/bin/python converter/statement_to_csv.py statement.pdf --currency THB --output-dir converted-output
```

For LH Bank without complete external controls:

```sh
.venv/bin/python converter/statement_to_csv.py statement.pdf --currency THB --review-only --output-dir review-output
```

Add `--ask-password` for encrypted PDFs. A hidden prompt reads the password;
no password is saved or passed on the command line. Output directories must be
new. Real converted outputs are private financial records and are not fixtures
for this repository.

For successful standard conversion, upload `transactions.csv` and the bank's
`default.json` to the Data Importer's File flow. Select your own THB asset
account. `default_account` is `0`, mappings are empty, and automatic user-rule
execution is disabled. The balance column is inspection-only, not an opening
balance transaction. No category or internal-transfer interpretation is added.

**LH Bank's `review-candidates.csv` is review-only.** Its report retains missing
independent controls and `csv_export_ready: false`. Standard output instead
requires six externally attested controls. Source hashes, target binding,
provenance, and values are checked; supporting-document semantics remain
human-attested, not automatically verified or formally approved.

## Controls and duplicate handling

Bangkok Bank, KBank PDF, and SCB verify printed totals/counts, balance equations,
row coverage, running balances, and pagination in their supported layouts.
KBank CSV has no applicable page control. SCB can additionally compare the
closing balance with the next consecutive same-account statement's printed
opening. LH Bank's source lacks printed independent summary controls; see its
guide for the separate review and attestation modes.

External IDs use a normalized full-statement-text fingerprint plus row sequence.
Identical input text produces stable IDs. Changed exports, different text
extraction, and overlapping statement periods need separate duplicate review;
these are not bank-issued transaction IDs.

## Validation

- Target configuration: Data Importer **2.3.5**, configuration format **v3**.
- **52 effective tests** pass: Bangkok Bank 9, KBank 12, SCB 17, LH Bank 14;
  two bank-inapplicable tests are skipped.
- Plain and encrypted synthetic PDFs, wrong balances, missing controls,
  pagination, evidence scope/hashes, and review/export separation are tested.
- Selected real-source checks covered eight files and 907 test-file rows.
  Seven files/456 rows passed standard local conversion; LH Bank's 451 rows
  produced review candidates while standard output remained blocked.
- Existing-parser comparisons share implementation logic and are regression
  checks, not independent formal finance audits. Actual Firefly import/re-import
  acceptance remains untested. No real statement or transaction content is
  distributed. See [VALIDATION.md](VALIDATION.md).

Run all suites from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/run_tests.py
```

## Upstream configuration contribution

The `th/<bank>/default.json` layout follows
[firefly-iii/import-configurations](https://github.com/firefly-iii/import-configurations).
That repository is the proposed home for the configuration files and short
format-specific notes; this repository hosts the required companion tools.
An upstream pull request is a contribution proposal, not a claim of acceptance.

## License

Companion code and original supporting material: **AGPL-3.0-only**.
The original `th/*/default.json` files: **CC-BY-SA-4.0**, matching the upstream
configuration repository. See [LICENSE](LICENSE), [CONFIG-LICENSE](CONFIG-LICENSE),
and [NOTICE.md](NOTICE.md) for the included upstream schema attribution.
