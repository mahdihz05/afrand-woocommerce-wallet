"""Small session login for the hosted demonstration."""
import secrets
from datetime import timedelta
from hmac import compare_digest

from flask import jsonify, redirect, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.security import check_password_hash


class SchemeCookieSession(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        return request.is_secure


def validate_login_configuration(app):
    if not app.config.get('PANEL_AUTH_ENABLED'):
        return
    # Rotation keys can also authenticate cookies; validate every signer.
    keys = [app.config.get('SECRET_KEY')]
    fallbacks = app.config.get('SECRET_KEY_FALLBACKS') or []
    if not isinstance(fallbacks, (list, tuple)):
        raise ValueError('SECRET_KEY_FALLBACKS must be a list of strong session keys')
    keys.extend(fallbacks)
    for key in keys:
        if isinstance(key, str):
            key = key.encode('utf-8')
        if (not isinstance(key, bytes) or len(key) < 32
                or key.strip() == b'local-demo-change-me'
                or len(set(key)) < 8):
            raise ValueError('SECRET_KEY and any SECRET_KEY_FALLBACKS must be independently '
                             'generated random keys of at least 32 bytes; public defaults '
                             'and repetitive keys are not allowed when panel login is enabled')
    if not app.config.get('PANEL_PASSWORD_HASH'):
        raise ValueError('PANEL_PASSWORD_HASH is required when panel login is enabled')


def configure_login(app):
    if not app.config.get('PANEL_AUTH_ENABLED'):
        return
    app.session_interface = SchemeCookieSession()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))

    def csrf_valid():
        expected = session.get('auth_csrf', '')
        supplied = request.form.get('csrf_token', '')
        return bool(expected and supplied and compare_digest(expected.encode(), supplied.encode()))

    @app.before_request
    def require_login():
        if request.endpoint in {'login', 'static'}:
            return None
        if session.get('panel_user') == app.config['PANEL_USERNAME']:
            return None
        if request.path.startswith('/api/'):
            return jsonify(error='برای ادامه، وارد پنل شوید.', category='authentication',
                           login_url=url_for('login')), 401
        return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if session.get('panel_user') == app.config['PANEL_USERNAME']:
            return redirect(url_for('index'))
        session.setdefault('auth_csrf', secrets.token_urlsafe(32))
        error = None
        status = 200
        if request.method == 'POST':
            if not csrf_valid():
                error, status = 'صفحه را تازه کنید و دوباره وارد شوید.', 400
            else:
                username = request.form.get('username', '')
                password = request.form.get('password', '')
                valid_password = check_password_hash(app.config['PANEL_PASSWORD_HASH'], password)
                if compare_digest(username.encode(), app.config['PANEL_USERNAME'].encode()) and valid_password:
                    session.clear()
                    session['panel_user'] = app.config['PANEL_USERNAME']
                    session['auth_csrf'] = secrets.token_urlsafe(32)
                    session.permanent = True
                    return redirect(url_for('index'))
                error, status = 'نام کاربری یا رمز عبور درست نیست.', 401
        return render_template('login.html', error=error), status

    @app.post('/logout')
    def logout():
        if not csrf_valid():
            return jsonify(error='درخواست خروج معتبر نیست.', category='forbidden'), 403
        session.clear()
        return redirect(url_for('login'))

    @app.after_request
    def prevent_login_caching(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if app.config.get('WALLET_MODE') or request.endpoint == 'login':
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
                "frame-ancestors 'none'; form-action 'self'")
        # The bundled font is public and identical for every user. Keep private
        # pages and API responses uncached, without re-downloading it on login.
        if (request.endpoint == 'static'
                and request.view_args.get('filename', '').endswith('.woff2')
                and response.status_code in {200, 304}):
            response.headers['Cache-Control'] = 'public, max-age=86400'
        else:
            response.headers['Cache-Control'] = 'no-store'
        return response
