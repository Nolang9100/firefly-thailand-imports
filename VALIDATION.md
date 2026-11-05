# Local validation status — 2026-11-05

This is a converter code-validation record, not a formal finance audit or a posting approval. Real statements and all resulting private data are stored separately and are not included in this package.

## Selected real sources

| Bank | Inputs tested | Source rows checked | Result |
| --- | --- | --- | --- |
| Bangkok Bank | Two native PDFs | 259 | Conversion and six controls pass |
| KBank | Two native PDFs and one CSV | 124 | Conversion passes; page control is not applicable to CSV |
| SCB | Two native PDFs | 73 | Conversion, printed openings, page order, cross-page carries, row balances, counts and amount totals pass |
| LH Bank | One 20-page native PDF | 451 | Low-level parser diagnostics pass; standalone export remains blocked |

There were 907 source rows checked by date, signed amount, and running balance against the existing parser, with no differences. These are test-file row counts, not a deduplicated ledger. The existing parser shares logic with the companion converter, so this comparison is a regression check rather than independent proof of source accuracy. Printed controls and selected source-page renders were also inspected locally. Original file hashes remained unchanged.

Seven files, containing 456 rows, passed the standalone conversion controls. LH Bank's 451 rows were initially parser diagnostics; the completed review-only mode now exports them as review candidates, while the standard transactions.csv gate remains closed without complete evidence.

## Fixes prompted by real inputs

- KBank: recognize the native English PDF issuer footer when the bank logo is not extractable text, and the known K-DEPOSIT CSV title. Missing CSV summary totals are explicitly refused.
- SCB: require printed per-page brought-forward balances, Thai or English page markers, correct page ordering, matching cross-page carries, complete dated-row coverage, and a unique complete printed summary. Check the source balance equation against the final transaction row. Reject missing or conflicting source controls.
- SCB: separately report that an independent closing-summary balance was not available. A passing balance equation does not assert the presence of such a summary.
- LH Bank: retain the independent-opening requirement; no source-derived value was supplied as independent confirmation.

## Remaining limits

SCB's tested sources have no separately labeled closing-balance summary. The last printed transaction balance is used for the balance equation; one adjacent-statement opening was also checked against the preceding statement's last balance locally.

LH Bank's tested source has no printed opening balance, debit/credit aggregate counts or amounts, or independent closing-balance summary. A first-row-derived opening cannot close these evidence gaps. Page, row coverage, dates, signed amounts, and running balances can be checked, but the standalone real-source export remains blocked.

These are selected samples, not exhaustive account/history coverage. No Firefly ledger, formal SQLite database, or external system was changed. Publication and upstream acceptance are separate from these local validation results.

## Synthetic regression checks

52 effective tests pass: Bangkok Bank 9, KBank 12, SCB 17, LH Bank 14. Two KBank-only cases are not applicable to the other bank suites and are skipped. The regression suite includes fictional reproductions of native KBank issuer signatures and SCB pagination/opening controls; no real transaction content is embedded in fixtures.

## Control-path completion

SCB now has a separate adjacent-statement check. On the selected real pair, July's terminal balance matched August's printed opening after same-account, consecutive-period, and complete next-statement controls passed. August's independent closing evidence is not supplied. This does not create a separately printed closing-summary field.

LH Bank now produces 451 explicitly review-only candidates from the selected real source. Missing independent controls remain visible and normal CSV export is still blocked. No external control bundle was fabricated for this real source. The complete six-control attestation path was tested only with fictional evidence; supporting-document semantics remain human-attested, not automatically extracted or formally approved.

Added synthetic regressions cover SCB wrong-account/nonconsecutive/incorrect next-statement evidence and required-evidence refusal, plus LH strict/review output separation, schema, evidence completeness, value/hash/scope mismatch, provenance, and source self-reference rejection. No formal ledger or external system was changed.
