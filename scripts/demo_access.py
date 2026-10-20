"""Authenticate opt-in local verification tools without storing passwords."""
import secrets


def local_test_client(app):
    client = app.test_client()
    if app.config.get('PANEL_AUTH_ENABLED'):
        with client.session_transaction() as session:
            session['panel_user'] = app.config['PANEL_USERNAME']
            session['auth_csrf'] = secrets.token_urlsafe(32)
    return client


def authorize_local_browser(app, page):
    if app.config.get('PANEL_AUTH_ENABLED'):
        serializer = app.session_interface.get_signing_serializer(app)
        value = serializer.dumps({'panel_user':app.config['PANEL_USERNAME'], 'auth_csrf':secrets.token_urlsafe(32)})
        page.context.add_cookies([{'name':app.config['SESSION_COOKIE_NAME'], 'value':value,
                                  'url':'http://127.0.0.1:5055', 'httpOnly':True, 'sameSite':'Lax'}])
