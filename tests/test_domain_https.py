"""DNS gating and rollback for the isolated deployment virtual host."""
import importlib.util
import io
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PROJECT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('domain_https', PROJECT / 'deploy/enable_domain_https.py')
https = importlib.util.module_from_spec(spec)
spec.loader.exec_module(https)


class DomainHttpsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = pathlib.Path(self.folder.name)
        self.patches = [patch.object(https, 'BASE', root),
                        patch.object(https, 'CERT_ROOT', root / 'certificates'),
                        patch.object(https, 'VHOST', root / 'vhost')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        (root / 'config').mkdir()
        self.original = (PROJECT / 'deploy/afrand-new-server.nginx').read_text()
        (root / 'config/http.nginx').write_text(self.original)
        https.VHOST.write_text(self.original)

    def routed_response(self, *args, **kwargs):
        probe = next((https.BASE / 'acme/.well-known/acme-challenge').iterdir())
        return io.BytesIO(probe.read_bytes())

    def test_waits_for_correct_public_route_without_changing_config(self):
        with patch.object(https.urllib.request, 'urlopen', return_value=io.BytesIO(b'other server')), patch.object(https, 'run') as run:
            https.main()
        run.assert_not_called()
        self.assertEqual(https.VHOST.read_text(), self.original)
        self.assertFalse(list((https.BASE / 'acme/.well-known/acme-challenge').iterdir()))

    def test_domain_https_preserves_ip_port_and_acme_route(self):
        with patch.object(https.urllib.request, 'urlopen', side_effect=self.routed_response), patch.object(https, 'run') as run:
            https.main()
        config = https.VHOST.read_text()
        self.assertIn('listen 8087;', config)
        self.assertIn('listen 443 ssl;', config)
        self.assertIn('location ^~ /.well-known/acme-challenge/', config)
        self.assertIn('location / { return 301 https://$host$request_uri; }', config)
        self.assertEqual(config.count('include /opt/afrand-woo-demo/config/proxy.inc;'), 2)
        self.assertEqual(run.call_args_list[-2].args, ('nginx', '-t'))
        self.assertEqual(run.call_args_list[-1].args, ('systemctl', 'reload', 'nginx'))

    def test_failed_nginx_validation_restores_previous_vhost(self):
        def run(*args):
            if args == ('nginx', '-t'):
                raise subprocess.CalledProcessError(1, args)
        with patch.object(https.urllib.request, 'urlopen', side_effect=self.routed_response), patch.object(https, 'run', side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                https.main()
        self.assertEqual(https.VHOST.read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
