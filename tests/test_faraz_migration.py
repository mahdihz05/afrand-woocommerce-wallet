"""Provider isolation at the real Flask/SQLite boundary (synthetic remote)."""
from unittest.mock import patch
import unittest
import test_wallet


class FarazMigrationTests(unittest.TestCase):
    setUp = test_wallet.WalletTests.setUp
    tearDown = test_wallet.WalletTests.tearDown
    preview = test_wallet.WalletTests.preview
    payload = test_wallet.WalletTests.payload
    def test_old_provider_draft_cannot_dispatch(self):
        preview = self.preview([['OLD', 'Customer', '09121234567', 100, 'IRT']]).json
        ledger = self.app.extensions['ledger']
        with ledger._connect() as db:
            import json
            old = dict(preview)
            old.pop('wallet_provider')
            db.execute('UPDATE wallet_imports SET preview_json=? WHERE draft_id=?',
                       (json.dumps(old), preview['draft_id']))
        response = self.client.post('/api/import/commit', json={'draft_id': preview['draft_id'], 'confirm': True})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.woo.transactions)

    def test_terawallet_status_is_rejected(self):
        request = self.woo.request
        def wrong_provider(method, path, **kwargs):
            if path.endswith('/status'):
                return {'connected': True, 'wallet': 'TeraWallet', 'currency': 'IRT'}
            return request(method, path, **kwargs)
        with patch.object(self.woo, 'request', side_effect=wrong_provider):
            self.assertEqual(self.client.get('/api/status').status_code, 502)

    def test_provider_namespaced_keys(self):
        import hashlib
        self.client.post('/api/wallet/credits', json=self.payload())
        old = hashlib.sha256((self.woo.base_url + ':manual-wallet:' + 'a'*32).encode()).hexdigest()
        self.assertNotIn(old, self.woo.operations)
