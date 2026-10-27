"""Prepare local-network deployment files; preserves existing keys and passwords."""
import argparse
import ipaddress
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def private_file(path, data):
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', default='10.81.211.237')
    args = parser.parse_args()
    ip = str(ipaddress.IPv4Address(args.ip))
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('Execute na VM Kali Linux com sudo python3 deploy/prepare.py.')
    secret_dir = ROOT / '.secrets'
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(secret_dir, 0o700)
    # Refuse to replace the key of an existing encrypted database.
    if (ROOT / 'data' / 'market.db').exists() and not (secret_dir / 'market.key').exists():
        parser.error('Copie a chave original .secrets/market.key antes de preparar esta migração.')
    private_file(secret_dir / 'market.key', secrets.token_bytes(32))
    for name in ('db_admin_password', 'db_app_password'):
        private_file(secret_dir / name, secrets.token_urlsafe(48).encode())
    # Compose bind-mounted secrets retain host permissions. Give only the app UID access.
    for name in ('market.key', 'db_app_password'):
        os.chown(secret_dir / name, 10001, 10001)
        os.chmod(secret_dir / name, 0o400)
    # Both postgres (initialization) and the app need this mounted secret.
    # The host parent directory stays root-only (0700).
    os.chmod(secret_dir / 'db_app_password', 0o444)
    os.chmod(secret_dir / 'db_admin_password', 0o400)
    tls = ROOT / 'deploy' / 'tls'
    tls.mkdir(mode=0o700, exist_ok=True)
    cert, key = tls / 'fullchain.pem', tls / 'privkey.pem'
    if cert.exists() != key.exists():
        parser.error('Certificado incompleto: forneça fullchain.pem e privkey.pem juntos.')
    if not cert.exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '30',
                        '-subj', f'/CN={ip}', '-addext', f'subjectAltName=IP:{ip}'], check=True)
    os.chmod(key, 0o600)
    private_file(ROOT / '.env', f'MARKET_ORIGIN=https://{ip}\nBIND_IP={ip}\n'.encode())
    print(f'Arquivos preparados. URL: https://{ip}')
    print('Confira a impressão digital do certificado com os colegas:')
    subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-fingerprint', '-sha256'], check=True)


if __name__ == '__main__':
    main()
