# Local checks

Use the project virtual environment with the dependencies declared in
`pyproject.toml` (Python >=3.12, Flask >=3.1,<4):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node tests/test_import_restore.js
node --check static/wallet.js
git diff --check
```

`test_excel_completeness.py` commits 1,000 customers in each Excel mode, then
reuploads both files in reverse order. It reconciles each customer's amount,
saved result and total, including existing/new customers. Cashback reuploads
replay the same documents; fixed-credit reuploads create 1,000 new credits.
It also verifies recovery of a failed middle row, physical row numbers around
blank/invalid rows, whole-file rejection over 1,000 rows and explicit rejection
of multiple worksheets. The capacity check uses a synthetic wallet and SQLite;
it is not a WordPress load benchmark.

```powershell
.\.venv\Scripts\python.exe tests/browser_excel_batches.py
.\.venv\Scripts\python.exe tests/browser_import.py
```

`browser_excel_batches.py` uses an isolated synthetic wallet. In both modes,
the second two-row batch is committed on the server and its HTTP response is
deliberately lost. After a browser reload, the saved import resumes at row index
4. All seven valid customers receive exactly one correct credit; two invalid
rows remain visible and all nine records appear in the on-page result. No TSV download is offered.
No live customer accounts are used.

`test_import_restore.js` executes the saved-import callback from `wallet.js`
with a minimal DOM substitute. It checks fixed credit, switching back to Excel
cashback, and older drafts without `amount_mode`, including form visibility and
preservation of the draft/preview. It does not verify browser layout.

## Session rotation review, 2026-11-01

Hosted checks use login, templates and previews without committing real credits.
Uploading a fixed-credit file again intentionally creates a new run; the old
`--live --replay-existing` workflow is blocked. Recover an existing saved import
to retry an interrupted run safely.

`python -m unittest discover -s tests -p test_domain_https.py` checks the
deployment helper's public DNS gate, isolated domain redirect, and rollback on
Nginx validation failure without accessing a server or issuing certificates.

The global Python environment had Flask 3.0.3, below the declared minimum.
`python -m unittest discover -s tests -p test_auth.py -q` reproduced a single
failure: `test_random_byte_key_and_safe_rotation_preserve_valid_sessions`
returned 302 instead of 200 (10 other tests passed).

Inspection of the installed `SecureCookieSessionInterface.get_signing_serializer`
showed that 3.0.3 only passes `app.secret_key` to the serializer, ignoring
`SECRET_KEY_FALLBACKS`. The project's virtual environment has Flask 3.1.3,
whose implementation includes the fallback keys. All 136 tests passed there,
including session rotation and rejection of cookies signed with public defaults.
No authentication logic was changed or weakened to accommodate the unsupported
environment. These checks used local synthetic data, without live wallet writes.

## Duplicate mobile behavior, 2026-11-02

Run `python -m unittest discover -s tests -p test_import_duplicate_modes.py`
for direct-credit deduplication, cashback purchase multiplicity, required document numbers, creation of one customer for repeated purchases, and replay
protection after reordering. Explicit duplicate invoice numbers remain invalid
for cashback; separate purchases should use separate invoice numbers.

Run `python tests/browser_duplicate_modes.py` for isolated Chromium acceptance
of both modes, skipped-row visibility, complete on-page results without TSV downloads, and three uploads per mode (six fixed credits, five cashback credits). Screenshots and receipts are stored under
`tmp/browser-duplicate-modes`. This test uses synthetic wallets only.
# Monetary input regression

Run `.venv/Scripts/python tests/browser_amount_inputs.py` for isolated Chromium
checks of three-digit grouping in both monetary inputs, Persian/Arabic digits,
middle edits, selection replacement, deletion beside separators and invalid
signs/decimals. It verifies the unformatted manual and multipart payloads and
exact fake-wallet cashback and fixed credits; no real wallet is contacted.
