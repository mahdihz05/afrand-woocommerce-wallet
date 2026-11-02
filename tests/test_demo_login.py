"""Regression checks for the authenticated local-only wallet demo."""
import re
import tempfile
import unittest
from pathlib import Path
from serve_wallet_fake import create_demo_app, PersistentDemoWallet


class DemoLoginTests(unittest.TestCase):
    def test_demo_requires_requested_login_and_serves_local_font(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_demo_app(directory)
            client = app.test_client()
            public_font = client.get('/static/fonts/Vazirmatn-VF.woff2?v=33.003')
            try:
                self.assertEqual(public_font.status_code, 200)
                self.assertEqual(public_font.mimetype, 'font/woff2')
                self.assertTrue(public_font.cache_control.public)
                self.assertEqual(public_font.cache_control.max_age, 86400)
            finally:
                public_font.close()
            self.assertTrue(client.get('/login').cache_control.no_store)
            self.assertEqual(client.get('/').status_code, 302)
            unauthorized = client.get('/api/status')
            self.assertEqual(unauthorized.status_code, 401)
            self.assertTrue(unauthorized.cache_control.no_store)
            token = re.search(r'name="csrf_token" value="([^"]+)"',
                              client.get('/login').text).group(1)
            self.assertEqual(client.post('/login', data={'username':'admin',
                'password':'wrong', 'csrf_token':token}).status_code, 401)
            self.assertEqual(client.post('/login', data={'username':'admin',
                'password':'admin1234', 'csrf_token':token}).status_code, 302)
            self.assertEqual(client.get('/').status_code, 200)
            self.assertTrue(client.get('/').cache_control.no_store)
            self.assertTrue(client.get('/api/status').json['writes_enabled'])
            font = client.get('/static/fonts/Vazirmatn-VF.woff2')
            try:
                self.assertEqual(font.status_code, 200)
                self.assertEqual(font.data[:4], b'wOF2')
            finally:
                font.close()
            with client.session_transaction() as session:
                token = session['auth_csrf']
            self.assertEqual(client.post('/logout', data={'csrf_token':token}).status_code, 302)
            self.assertEqual(client.get('/api/status').status_code, 401)

    def test_fake_wallet_preserves_customer_credit_and_replay_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fake-wallet.json'
            wallet = PersistentDemoWallet(path)
            customer = wallet.request('POST', 'afrand-wallet/customers/create', payload={
                'first_name':'Demo', 'phone':'09123334455', 'company':'Example'})['customer']
            payload = {'request_id':'a'*32, 'customer_id':customer['id'],
                       'amount':'5000', 'currency':'IRT', 'note':'demo'}
            wallet.request('POST', 'afrand-wallet/credits', payload=payload)
            restored = PersistentDemoWallet(path)
            self.assertTrue(restored.request('POST', 'afrand-wallet/credits', payload=payload)['replayed'])
            self.assertEqual(len(restored.transactions), 1)
            self.assertEqual(restored.customers[-1]['company'], 'Example')
