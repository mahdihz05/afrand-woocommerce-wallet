"""Loopback-only synthetic demo; never loads .env or contacts WordPress."""
import argparse
import json
import secrets
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from panel.app import create_app
from test_wallet import FakeWallet
from werkzeug.security import generate_password_hash


class PersistentDemoWallet(FakeWallet):
    """Keep synthetic wallet state when this local demo is restarted."""
    base_url = 'https://fake-wallet.invalid'

    def __init__(self, state_file):
        super().__init__()
        self.state_file = Path(state_file)
        self.state_lock = threading.RLock()
        if self.state_file.exists():
            state = json.loads(self.state_file.read_text(encoding='utf-8'))
            for key in ('customers', 'transactions', 'operations', 'currency'):
                setattr(self, key, state[key])

    def request(self, method, path, params=None, payload=None):
        with self.state_lock:
            result = super().request(method, path, params, payload)
            if method != 'GET':
                state = {key: getattr(self, key) for key in
                         ('customers', 'transactions', 'operations', 'currency')}
                temporary = self.state_file.with_suffix('.tmp')
                temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
                temporary.replace(self.state_file)
            return result


def create_demo_app(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    wallet = PersistentDemoWallet(root / 'fake-wallet.json')
    return create_app({
        'WALLET_MODE': True, 'DATABASE': str(root / 'demo.sqlite3'),
        'WC_WRITES_ENABLED': True, 'PANEL_AUTH_ENABLED': True,
        'PANEL_USERNAME': 'admin',
        'PANEL_PASSWORD_HASH': generate_password_hash('admin1234'),
        'SECRET_KEY': secrets.token_urlsafe(48),
    }, woo_client=wallet)

if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5067)
    parser.add_argument('--data-dir', type=Path, default=Path('tmp/browser-wallet'))
    args = parser.parse_args()
    app = create_demo_app(args.data_dir.resolve())
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False, load_dotenv=False)
