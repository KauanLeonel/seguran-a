"""Create synthetic users through the ordinary registration flow every 60 seconds."""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import secrets
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPSHandler, Request


class CsrfParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'input' and fields.get('name') == 'csrf':
            self.token = fields.get('value')


def synthetic_user():
    suffix = secrets.token_hex(8)
    # Deliberately invalid identifiers; no real person's CPF/phone is generated.
    return {'name': 'Cliente Fictício ' + suffix[:6], 'email': f'cliente.{suffix}@example.test',
            'password': secrets.token_urlsafe(24), 'cpf': '00000000000', 'phone': '11000000000',
            'street': 'Rua Exemplo', 'number': str(secrets.randbelow(999)+1),
            'district': 'Bairro Modelo', 'city': 'Cidade Exemplo', 'state': 'SP', 'zip': '00000000',
            'card': '4242424242424242', 'expiry': f'12/{datetime.now().year+3}'}


def create_user(origin, cafile=None):
    context = ssl.create_default_context(cafile=cafile)
    client = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=context))
    with client.open(origin + '/register', timeout=20) as response:
        parser = CsrfParser()
        parser.feed(response.read().decode('utf-8'))
    if not parser.token:
        raise RuntimeError('Formulário de cadastro não encontrado.')
    user = synthetic_user()
    request = Request(origin + '/register', data=urlencode({**user, 'csrf': parser.token}).encode(),
                      headers={'Origin': origin, 'Content-Type': 'application/x-www-form-urlencoded'})
    with client.open(request, timeout=30) as response:
        page = response.read().decode('utf-8')
        if response.status != 200 or response.geturl() != origin + '/' or 'Minha conta' not in page:
            raise RuntimeError('O cadastro não retornou uma sessão autenticada.')
    return user['email']


def run(origin, count=0, cafile=None, create=None, sleep=time.sleep):
    create = create or create_user
    created = 0
    while count == 0 or created < count:
        started = time.monotonic()
        email = create(origin, cafile)
        created += 1
        print(f'{datetime.now(timezone.utc).isoformat(timespec="seconds")} | Criado: {email}', flush=True)
        if count == 0 or created < count:
            sleep(max(0, 60 - (time.monotonic() - started)))
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000', help='Origem da loja, sem caminho')
    parser.add_argument('--count', type=int, default=0, help='Quantidade; 0 continua até Ctrl+C')
    parser.add_argument('--ca', help='Certificado público confiável para HTTPS local')
    args = parser.parse_args()
    origin = args.url.rstrip('/')
    parsed = urlsplit(origin)
    if args.count < 0 or parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error('Informe origem HTTP/HTTPS válida e quantidade não negativa.')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Use HTTPS para acesso fora deste computador.')
    print('Primeiro cadastro agora; próximos a cada 60 segundos. Ctrl+C encerra.', flush=True)
    try:
        run(origin, args.count, args.ca)
    except KeyboardInterrupt:
        print('\nGerador encerrado.')
    except HTTPError as error:
        parser.exit(1, f'Cadastro recusado: HTTP {error.code}. Confira se o servidor está atualizado e os limites de tentativas.\n')
    except (URLError, RuntimeError, OSError) as error:
        parser.exit(1, f'Não foi possível cadastrar: {error}\n')


if __name__ == '__main__':
    main()
