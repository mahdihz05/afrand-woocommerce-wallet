"""Enable only Afrand's virtual host after public ACME routing is verified."""
import pathlib
import secrets
import subprocess
import urllib.request

BASE = pathlib.Path('/opt/afrand-woo-demo')
HOST = 'wp-woo-demo.mahdihz.ir'
CERT = 'afrand-woo-demo-20261101'
VHOST = pathlib.Path('/etc/nginx/sites-available/zzz-afrand-woo-demo')
CERT_ROOT = pathlib.Path('/etc/letsencrypt/live')


def run(*args):
    subprocess.run(args, check=True, timeout=180)


def main():
    fullchain = CERT_ROOT / CERT / 'fullchain.pem'
    if not fullchain.exists():
        token = secrets.token_hex(24)
        probe = BASE / 'acme/.well-known/acme-challenge' / token
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(token)
        try:
            with urllib.request.urlopen(f'http://{HOST}/.well-known/acme-challenge/{token}', timeout=15) as response:
                if response.read().decode() != token:
                    print('Waiting for domain DNS to route to this application.')
                    return
        except Exception:
            print('Waiting for domain DNS to route to this application.')
            return
        finally:
            probe.unlink(missing_ok=True)
        run('certbot', 'certonly', '--webroot', '-w', str(BASE / 'acme'), '-d', HOST,
            '--cert-name', CERT, '--non-interactive', '--agree-tos',
            '--register-unsafely-without-email', '--keep-until-expiring')
    config = (BASE / 'config/http.nginx').read_text()
    # Keep the dedicated IP port and ACME endpoint; redirect domain HTTP only.
    marker = '    include /opt/afrand-woo-demo/config/proxy.inc;'
    index = config.rfind(marker)
    config = config[:index] + '    location / { return 301 https://$host$request_uri; }' + config[index + len(marker):]
    config += f'''\nserver {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {HOST};
    ssl_certificate /etc/letsencrypt/live/{CERT}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{CERT}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    include /opt/afrand-woo-demo/config/proxy.inc;
}}
'''
    if VHOST.read_text() == config:
        print('Domain HTTPS already configured.')
        return
    original = VHOST.read_text()
    VHOST.write_text(config)
    try:
        run('nginx', '-t')
    except Exception:
        VHOST.write_text(original)
        raise
    run('systemctl', 'reload', 'nginx')
    print('Domain HTTPS enabled.')


if __name__ == '__main__':
    main()
