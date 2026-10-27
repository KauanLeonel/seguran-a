"""Cadastra usuários fictícios em lotes: por padrão, 10 a cada 30 segundos."""
import argparse
import math
from datetime import datetime, timezone
from functools import partial
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


def create_user(origin, cafile=None, context=None):
    handlers = [HTTPCookieProcessor(CookieJar())]
    if urlsplit(origin).scheme == 'https':
        handlers.append(HTTPSHandler(context=context or ssl.create_default_context(cafile=cafile)))
    client = build_opener(*handlers)
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


def retry_delay(error, interval):
    if isinstance(error, HTTPError):
        if error.code != 429 and not 500 <= error.code < 600:
            raise error
        try:
            delay = float(error.headers.get('Retry-After', interval))
        except (ValueError, TypeError, AttributeError):
            delay = interval
        return max(interval, delay) if math.isfinite(delay) else interval
    if isinstance(error, ssl.SSLError) or isinstance(getattr(error, 'reason', None), ssl.SSLError):
        raise error
    return interval


def run(origin, count=0, cafile=None, create=None, sleep=time.sleep,
        batch_size=10, interval=30):
    if count < 0 or batch_size < 1 or not math.isfinite(interval) or interval <= 0:
        raise ValueError('Quantidade não negativa, lote positivo e intervalo positivo finito são obrigatórios.')
    if create is None:
        # Load trusted CAs once, keeping a separate session for each account.
        context = ssl.create_default_context(cafile=cafile) if urlsplit(origin).scheme == 'https' else None
        create = partial(create_user, context=context)
    created = 0
    batch = 0
    while count == 0 or created < count:
        started = time.monotonic()
        batch += 1
        size = batch_size if count == 0 else min(batch_size, count - created)
        print(f'Lote {batch}: cadastrando {size} usuário(s).', flush=True)
        for _ in range(size):
            while True:
                try:
                    email = create(origin, cafile)
                    break
                except (HTTPError, URLError, TimeoutError, ConnectionError) as error:
                    delay = retry_delay(error, interval)
                    detail = f'HTTP {error.code}' if isinstance(error, HTTPError) else 'conexão indisponível'
                    print(f'Falha temporária ({detail}); nova tentativa em {delay:g}s.', flush=True)
                    if isinstance(error, HTTPError):
                        error.close()
                    sleep(delay)
            created += 1
            print(f'{datetime.now(timezone.utc).isoformat(timespec="seconds")} | Criado: {email} | Total: {created}', flush=True)
        print(f'Lote {batch} concluído. Total criado: {created}.', flush=True)
        if count == 0 or created < count:
            # Measure between batch starts; never launch overlapping batches.
            sleep(max(0, interval - (time.monotonic() - started)))
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000', help='Origem da loja, sem caminho')
    parser.add_argument('--count', type=int, default=0, help='Quantidade; 0 continua até Ctrl+C')
    parser.add_argument('--batch-size', type=int, default=10, help='Usuários por lote (padrão: 10)')
    parser.add_argument('--interval', type=float, default=30, help='Segundos entre inícios dos lotes (padrão: 30)')
    parser.add_argument('--ca', help='Certificado público confiável para HTTPS local')
    args = parser.parse_args()
    if args.batch_size < 1 or not math.isfinite(args.interval) or args.interval <= 0:
        parser.error('Informe lote positivo e intervalo positivo finito.')
    origin = args.url.rstrip('/')
    parsed = urlsplit(origin)
    if args.count < 0 or parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error('Informe origem HTTP/HTTPS válida e quantidade não negativa.')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Use HTTPS para acesso fora deste computador.')
    print(f'Primeiro lote agora; {args.batch_size} usuários a cada {args.interval:g} segundos. Ctrl+C encerra.', flush=True)
    try:
        run(origin, args.count, args.ca, batch_size=args.batch_size, interval=args.interval)
    except KeyboardInterrupt:
        print('\nGerador encerrado.')
    except HTTPError as error:
        parser.exit(1, f'Cadastro recusado: HTTP {error.code}. Confira se o servidor está atualizado e os limites de tentativas.\n')
    except (URLError, RuntimeError, OSError) as error:
        parser.exit(1, f'Não foi possível cadastrar: {error}\n')


if __name__ == '__main__':
    main()
