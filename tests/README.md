# Local checks

Use the project virtual environment with the dependencies declared in
`pyproject.toml` (Python >=3.12, Flask >=3.1,<4):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node tests/test_import_restore.js
node --check static/wallet.js
git diff --check
```

`test_import_restore.js` executes the saved-import callback from `wallet.js`
with a minimal DOM substitute. It checks fixed credit, switching back to Excel
cashback, and older drafts without `amount_mode`, including form visibility and
preservation of the draft/preview. It does not verify browser layout.

## Session rotation review, 2026-11-01

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
