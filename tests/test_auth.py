import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash
from panel.app import create_app


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'SECRET_KEY': secrets.token_hex(32),
            'DATABASE': str(Path(self.folder.name) / 'db.sqlite3'), 'PANEL_AUTH_ENABLED': True,
            'PANEL_USERNAME': 'demo', 'PANEL_PASSWORD_HASH': generate_password_hash('test-pass')})
        self.client = self.app.test_client()

    def tearDown(self):
        self.folder.cleanup()

    def token(self):
        response = self.client.get('/login')
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)

    def login(self, password='test-pass', username='demo'):
        return self.client.post('/login', data={'username': username, 'password': password, 'csrf_token': self.token()})

    def test_anonymous_ui_download_and_api_are_protected_without_browser_prompt(self):
        for path in ['/', '/template.xlsx', '/demo.xlsx']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers['Location'], '/login')
            self.assertNotIn('WWW-Authenticate', response.headers)
        for path in ['/api/status', '/api/customers', '/api/products']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json['category'], 'authentication')
            self.assertNotIn('WWW-Authenticate', response.headers)
        self.assertEqual(self.client.post('/api/orders', json={}).status_code, 401)

    def test_login_session_survives_reload_and_logout_revokes_it(self):
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.client.session_transaction() as session:
            token = session['auth_csrf']
        self.assertEqual(self.client.post('/logout', data={'csrf_token': token}).status_code, 302)
        self.assertEqual(self.client.get('/api/status').status_code, 401)

    def test_wrong_credentials_and_non_ascii_input_do_not_authenticate(self):
        for username,password in [('demo','wrong'),('wrong','test-pass'),('نام','test-pass')]:
            self.assertEqual(self.login(password, username).status_code, 401)
            self.assertEqual(self.client.get('/api/status').status_code, 401)

    def test_login_and_logout_require_csrf_and_cross_origin_is_blocked(self):
        self.token()
        self.assertEqual(self.client.post('/login', data={'username':'demo','password':'test-pass'}).status_code, 400)
        self.assertEqual(self.client.post('/login', headers={'Origin':'https://elsewhere.test'},
                                        data={'username':'demo','password':'test-pass','csrf_token':self.token()}).status_code, 403)
        self.login()
        self.assertEqual(self.client.post('/logout').status_code, 403)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_https_proxy_sets_secure_cookie_and_same_origin_login_works(self):
        self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_proto=1)
        client = self.app.test_client()
        response = client.get('/login', headers={'X-Forwarded-Proto':'https'})
        self.assertIn('Secure;', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly;', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        token = re.search(r'name="csrf_token" value="([^"]+)"',response.get_data(as_text=True)).group(1)
        response = client.post('/login',headers={'X-Forwarded-Proto':'https','Origin':'https://localhost'},
                               data={'username':'demo','password':'test-pass','csrf_token':token})
        self.assertEqual(response.status_code, 302)

    def test_login_page_is_mobile_ready_and_not_cached(self):
        response = self.client.get('/login')
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertIn('autocomplete="current-password"',response.get_data(as_text=True))

    def test_auth_configuration_requires_password_hash(self):
        with self.assertRaisesRegex(ValueError, 'PANEL_PASSWORD_HASH'):
            create_app({'PANEL_AUTH_ENABLED':True, 'SECRET_KEY':secrets.token_hex(32),
                        'DATABASE':str(Path(self.folder.name)/'other.sqlite3')})

    def test_unsafe_signing_keys_fail_before_database_initialization(self):
        for wallet_mode in [False, True]:
            for key in [None, '', ' ' * 40, 'local-demo-change-me',
                        'short-secret', 'a' * 64, b'local-demo-change-me']:
                with self.subTest(wallet_mode=wallet_mode, key_type=type(key).__name__), patch('panel.app.Ledger') as ledger:
                    with self.assertRaisesRegex(ValueError, 'SECRET_KEY'):
                        create_app({**self.app.config, 'WALLET_MODE': wallet_mode, 'SECRET_KEY': key})
                    ledger.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'SECRET_KEY'):
            create_app({'PANEL_AUTH_ENABLED': True,
                        'PANEL_PASSWORD_HASH': self.app.config['PANEL_PASSWORD_HASH']})

    def test_unsafe_rotation_keys_are_rejected(self):
        for fallbacks in [['local-demo-change-me'], [''], ['x' * 64], 'not-a-list']:
            with self.subTest(fallback_type=type(fallbacks).__name__):
                with self.assertRaisesRegex(ValueError, 'SECRET_KEY'):
                    create_app({**self.app.config, 'SECRET_KEY_FALLBACKS': fallbacks})

    def test_cookie_forged_with_public_default_does_not_authenticate(self):
        signer_app = Flask(__name__)
        signer_app.secret_key = 'local-demo-change-me'
        signer = SecureCookieSessionInterface().get_signing_serializer(signer_app)
        cookie = signer.dumps({'panel_user': 'demo'})
        for wallet_mode in [False, True]:
            with self.subTest(wallet_mode=wallet_mode):
                app = create_app({**self.app.config, 'WALLET_MODE': wallet_mode,
                                  'SECRET_KEY_FALLBACKS': [secrets.token_hex(32)]})
                client = app.test_client()
                client.set_cookie(app.config['SESSION_COOKIE_NAME'], cookie)
                self.assertEqual(client.get('/api/status').status_code, 401)
                self.assertEqual(client.get('/').status_code, 302)

    def test_random_byte_key_and_safe_rotation_preserve_valid_sessions(self):
        # Edge whitespace is legitimate entropy in raw byte keys, not padding.
        old_key = b' ' + bytes(range(1, 32))
        app = create_app({**self.app.config, 'SECRET_KEY': bytes(range(32)),
                          'SECRET_KEY_FALLBACKS': [old_key]})
        signer_app = Flask(__name__)
        signer_app.secret_key = old_key
        signer = SecureCookieSessionInterface().get_signing_serializer(signer_app)
        client = app.test_client()
        client.set_cookie(app.config['SESSION_COOKIE_NAME'], signer.dumps({'panel_user': 'demo'}))
        self.assertEqual(client.get('/').status_code, 200)
