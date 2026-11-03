"""Build the standalone WordPress ZIP, excluding configuration and test data."""
import hashlib
import re
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / 'wordpress' / 'afrand-faraz-wallet-bridge'
    source = (folder / 'afrand-faraz-wallet-bridge.php').read_text(encoding='utf-8')
    version = re.search(r'^ \* Version: ([0-9.]+)$', source, re.MULTILINE).group(1)
    if f"const VERSION = '{version}';" not in source:
        raise ValueError('Plugin header and runtime versions differ')
    output = root / 'outputs' / 'faraz-release'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'afrand-faraz-wallet-bridge-{version}.zip'
    members = ('afrand-faraz-wallet-bridge.php', 'admin-wallets.php')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name in members:
            info = zipfile.ZipInfo(f'{folder.name}/{name}', date_time=(2026, 11, 3, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, (folder / name).read_bytes())
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        assert package.namelist() == [f'{folder.name}/{name}' for name in members]
        for name in members:
            assert package.read(f'{folder.name}/{name}') == (folder / name).read_bytes()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(f'{archive}\nSHA256 {digest}')


if __name__ == '__main__':
    main()
