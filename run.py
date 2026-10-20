import os
from dotenv import load_dotenv
from panel.app import create_app
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()
app = create_app({
    "SECRET_KEY": os.getenv("FLASK_SECRET_KEY", "local-demo-change-me"),
    "WC_BASE_URL": os.getenv("WC_BASE_URL", ""),
    "WC_STAGING_HOST": os.getenv("WC_STAGING_HOST", ""),
    "WC_CONSUMER_KEY": os.getenv("WC_CONSUMER_KEY", ""),
    "WC_CONSUMER_SECRET": os.getenv("WC_CONSUMER_SECRET", ""),
    "WC_WRITES_ENABLED": os.getenv("WC_WRITES_ENABLED", "false"),
    "WC_ENVIRONMENT": os.getenv("WC_ENVIRONMENT", "staging"),
    "WC_TIMEOUT": os.getenv("WC_TIMEOUT", "15"),
    "WC_READ_TIMEOUT": os.getenv("WC_READ_TIMEOUT", "5"),
    "PANEL_AUTH_ENABLED": os.getenv("PANEL_AUTH_ENABLED", "true").lower() == "true",
    "PANEL_USERNAME": os.getenv("PANEL_USERNAME", "demo"),
    "PANEL_PASSWORD_HASH": os.getenv("PANEL_PASSWORD_HASH", ""),
    "WALLET_MODE": os.getenv("WALLET_MODE", "true").lower() == "true",
})

# Enable only behind the deployment's single, trusted Nginx proxy.
if os.getenv("AFRAND_TRUST_PROXY", "false").lower() == "true":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5055")), debug=False)
