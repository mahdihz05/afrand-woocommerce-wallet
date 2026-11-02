"""Consistent daily ledger backup; retain the latest seven daily copies."""
import datetime
import pathlib
import sqlite3

base = pathlib.Path('/opt/afrand-woo-demo')
backup = base / 'backups' / ('daily-' + datetime.date.today().isoformat() + '.sqlite3')
with sqlite3.connect(f'file:{base}/data/panel.sqlite3?mode=ro', uri=True) as source:
    with sqlite3.connect(backup) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
backup.chmod(0o600)
for old in sorted((base / 'backups').glob('daily-*.sqlite3'))[:-7]:
    old.unlink()
print('Ledger backup integrity: ok')
