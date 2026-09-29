"""Authenticated encryption for personal data; key is separate from the database."""
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Vault:
    def __init__(self, path, allow_create):
        path = Path(path)
        if not path.exists():
            if not allow_create:
                raise RuntimeError('Chave ausente. Restaure a chave original; não gere outra para este banco.')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as file:
                file.write(AESGCM.generate_key(bit_length=256))
        key = path.read_bytes()
        if len(key) != 32:
            raise RuntimeError('Chave de criptografia inválida.')
        self.cipher = AESGCM(key)

    def seal(self, value, context):
        nonce = os.urandom(12)
        data = json.dumps(value, ensure_ascii=False).encode()
        return 'v1:' + base64.urlsafe_b64encode(nonce + self.cipher.encrypt(nonce, data, context.encode())).decode()

    def open(self, value, context):
        data = base64.urlsafe_b64decode(value[3:])
        return json.loads(self.cipher.decrypt(data[:12], data[12:], context.encode()))
