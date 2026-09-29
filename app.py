"""Marketplace didático: somente dados sintéticos. Python 3.12+."""
import argparse
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import secrets
import sqlite3
import ssl
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from commerce import Commerce, profile_fields, validate_profile, PROFILE_FIELDS
from database import postgres_enabled, connect_postgres

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'market.db'
KEY_PATH = Path(os.environ.get('MARKET_KEY_FILE', ROOT / '.secrets' / 'market.key'))
MARKET = None
LOCK = threading.Lock()
HASH_SLOTS = threading.BoundedSemaphore(2)
ATTEMPTS = defaultdict(deque)
PRODUCTS = [(1, 'Fone Studio', 'Áudio para acompanhar seus estudos.', 12990, '🎧'),
            (2, 'Teclado Compact', 'Mais espaço para suas ideias.', 18990, '⌨️'),
            (3, 'Mochila Campus', 'Tudo o que você precisa, com você.', 15990, '🎒'),
            (4, 'Mouse Precision', 'Controle em cada detalhe.', 8990, '🖱️')]


@contextmanager
def connect():
    if postgres_enabled():
        with connect_postgres() as db:
            yield db
        return
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA secure_delete=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def password_hash(password, salt, n=131072):
    with HASH_SLOTS:
        digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=8, p=1, maxmem=256*1024*1024).hex()
    return f'scrypt${n}${digest}'


def verify_password(password, salt, stored):
    if stored.startswith('scrypt$131072$'):
        return hmac.compare_digest(password_hash(password, salt), stored)
    # Compatibility with the first release. Upgrade after successful login.
    return hmac.compare_digest(password_hash(password, salt, n=16384).split('$')[-1], stored)


def init():
    global MARKET
    if not postgres_enabled():
        DB.parent.mkdir(exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
          email TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER,
          csrf TEXT NOT NULL, expires REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
          product_id INTEGER NOT NULL, cents INTEGER NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, created TEXT DEFAULT CURRENT_TIMESTAMP,
          ip TEXT NOT NULL, kind TEXT NOT NULL);
        ''')
    MARKET = Commerce(connect, KEY_PATH, PRODUCTS)


def event(ip, kind):
    with connect() as db:
        db.execute('INSERT INTO events(ip,kind) VALUES(?,?)', (ip, kind))
        db.execute('DELETE FROM events WHERE id <= (SELECT COALESCE(MAX(id),0)-1000 FROM events)')


def limited(key, maximum, period):
    now = time.monotonic()
    with LOCK:
        # Bound memory even when many different accounts are submitted.
        if len(ATTEMPTS) > 4096:
            for old in list(ATTEMPTS):
                if not ATTEMPTS[old] or now - ATTEMPTS[old][-1] > 900:
                    del ATTEMPTS[old]
            if len(ATTEMPTS) > 4096 and key not in ATTEMPTS:
                return True
        q = ATTEMPTS[key]
        while q and q[0] <= now - period:
            q.popleft()
        if len(q) >= maximum:
            return True
        q.append(now)
        return False


def esc(value):
    return html.escape(str(value), quote=True)


def money(cents):
    return f'R$ {cents / 100:.2f}'.replace('.', ',')


class Handler(BaseHTTPRequestHandler):
    server_version = 'CampusMarket'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *args):
        pass  # Never log passwords, cookies, or request bodies.

    def reply(self, status, body='', content_type='text/html; charset=utf-8', location=None, render_error=True):
        if render_error and status >= 400 and content_type == 'text/html; charset=utf-8':
            content = (f'<section class="panel"><small>ERRO {status}</small>'
                       f'<h1>Não foi possível concluir</h1><p role="alert">{esc(body)}</p>'
                       '<p><a class="button" href="/account">Minha conta</a></p>'
                       '<a href="/">Voltar à loja →</a></section>')
            body = self.page('Não foi possível concluir', content, {'user_id': None})
        payload = body.encode()
        self.send_response(status)
        for k, v in {
            'Content-Type': content_type, 'Content-Length': str(len(payload)),
            'Content-Security-Policy': "default-src 'none'; connect-src 'self'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            # Preserve Origin on same-origin HTML form POSTs.
            'Referrer-Policy': 'same-origin', 'Cache-Control': 'no-store',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
            'Connection': 'close',
        }.items():
            self.send_header(k, v)
        if self.server.secure:
            self.send_header('Strict-Transport-Security', 'max-age=86400')
        if getattr(self, 'new_cookie', None):
            self.send_header('Set-Cookie', f'campus_session={self.new_cookie}; HttpOnly; SameSite=Strict; Path=/' + ('; Secure' if self.server.secure else ''))
        if location:
            self.send_header('Location', location)
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def guard(self):
        self.new_cookie = None
        if self.headers.get_all('Host') != [self.server.authority]:
            self.reply(400, 'Host inválido.')
            return False
        if limited(('request', self.client_address[0]), 120, 60):
            self.reply(429, 'Limite temporário. Aguarde um minuto.')
            return False
        return True

    def session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
            raw = cookies['campus_session'].value if 'campus_session' in cookies else ''
        except Exception:
            raw = ''
        token = hashlib.sha256(raw.encode()).hexdigest()
        with connect() as db:
            db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
            row = db.execute('SELECT * FROM sessions WHERE token=?', (token,)).fetchone()
            if row:
                return row
        return self.create_session(None)

    def create_session(self, user_id, db=None):
        raw = secrets.token_urlsafe(32)
        token = hashlib.sha256(raw.encode()).hexdigest()
        csrf = secrets.token_urlsafe(32)
        if db is not None:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token, user_id, csrf, time.time() + 1800))
        else:
            with connect() as connection:
                connection.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token, user_id, csrf, time.time() + 1800))
        self.new_cookie = raw
        return {'token': token, 'user_id': user_id, 'csrf': csrf}

    def form(self, action, fields, label, session):
        return f'<form method="post" action="{action}"><input type="hidden" name="csrf" value="{esc(session["csrf"])}">{fields}<button>{label}</button></form>'

    def page(self, title, content, session):
        nav = '<a href="/login">Entrar</a><a class="pill" href="/register">Criar conta</a>'
        if session['user_id']:
            nav = '<a href="/sales">Minhas vendas</a><a href="/account">Minha conta</a>' + self.form('/logout', '', 'Sair', session)
        return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)} · Campus Market</title><link rel="stylesheet" href="/style.css"></head>
        <body><header><a class="brand" href="/">◈ Campus<span>Market</span></a><nav>{nav}</nav></header>
        <main>{content}</main><footer>Campus Market <span>Essenciais para acompanhar o seu dia.</span></footer></body></html>'''

    def auth_page(self, session, message='', values=None, status=200):
        values = values or {}
        register = self.path == '/register'
        title = 'Crie sua conta' if register else 'Bem-vindo de volta'
        fields = (f'<label>Nome<input name="name" maxlength="60" required autocomplete="name" value="{esc(values.get("name", ""))}"></label>' if register else '')
        fields += (f'<label>E-mail<input type="email" name="email" maxlength="120" required autocomplete="username" placeholder="voce@exemplo.com" value="{esc(values.get("email", ""))}"></label>'
                   '<label>Senha<input type="password" name="password" minlength="12" maxlength="128" required autocomplete="' + ('new-password' if register else 'current-password') + '"></label>')
        if register:
            fields += '<h2>Dados pessoais e entrega</h2>' + profile_fields(values)
        alert = f'<div class="form-error" role="alert">{esc(message)}</div>' if message else ''
        link = '<a href="/login">Já tem conta? Entrar →</a>' if register else '<a href="/register">Ainda não tem conta? Cadastre-se →</a>'
        content = f'<section class="panel"><small>SEU ESPAÇO NO CAMPUS</small><h1>{title}</h1><p>Acompanhe seus pedidos e encontre seus próximos favoritos.</p>{alert}' + self.form(self.path, fields, 'Criar minha conta' if register else 'Entrar', session) + f'<p class="hint">Sua senha deve ter de 12 a 128 caracteres.</p>{link}</section>'
        self.reply(status, self.page(title, content, session), render_error=False)

    def do_GET(self):
        if not self.guard():
            return
        if self.path == '/.well-known/appspecific/com.chrome.devtools.json':
            # No automatic workspace configured; do not create a session or attack event.
            return self.reply(404, '{}', 'application/json; charset=utf-8')
        if self.path == '/style.css':
            return self.reply(200, (ROOT / 'style.css').read_text(encoding='utf-8'), 'text/css; charset=utf-8')
        s = self.session()
        if MARKET.get(self, s):
            return
        if self.path == '/':
            cards = ''
            for product in MARKET.catalog():
                buy = f'<a class="button" href="/checkout?id={product["id"]}">Comprar agora ↗</a>'
                cards += f'<article><div class="product">{esc(product["icon"])}</div><div class="details"><small>ESSENCIAIS DO CAMPUS</small><h3>{esc(product["title"])}</h3><p>{esc(product["description"])}</p><strong>{money(product["cents"])}</strong>{buy}</div></article>'
            content = '<section class="hero"><div><div class="eyebrow">NOVA COLEÇÃO / ESSENCIAIS</div><h1>Seu próximo essencial.<br><em>Mais do seu jeito.</em></h1><p>Tecnologia, conforto e praticidade.<br>Descubra escolhas que combinam com a sua rotina.</p><a class="button" href="#catalog">Explorar produtos ↓</a></div><div class="seal">◈<small>CAMPUS<br>COLLECTION</small></div></section><div class="section-title" id="catalog"><h2>Escolhas para o dia a dia</h2><span>Pequenos detalhes. Novas possibilidades.</span></div><section class="grid">' + cards + '</section><aside>Encontre seus favoritos e acompanhe todos os pedidos na sua conta.</aside>'
        elif self.path in ('/login', '/register'):
            if s['user_id']:
                return self.reply(303, location='/')
            return self.auth_page(s)
        elif self.path == '/account':
            if not s['user_id']:
                return self.reply(303, location='/login')
            with connect() as db:
                user = db.execute('SELECT name,email FROM users WHERE id=?', (s['user_id'],)).fetchone()
                orders = db.execute('SELECT * FROM purchases WHERE buyer_id=? ORDER BY id DESC LIMIT 100', (s['user_id'],)).fetchall()
                legacy = db.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 100', (s['user_id'],)).fetchall()
                profile = MARKET.profile(db, s['user_id'])
            old = ''.join(f'<li>Pedido anterior #{o["id"]} · {money(o["cents"])} · Sem entrega registrada</li>' for o in legacy)
            summary = f'CPF: ***.***.***-{esc(profile["cpf"][-2:])}<br>Cartão: •••• {esc(profile["card"][-4:])}' if all(profile.values()) else 'Complete seus dados pessoais para comprar.'
            content = f'<section class="panel wide"><small>MINHA CONTA</small><h1>Olá, {esc(MARKET.name(user["name"], s["user_id"]))}.</h1><p>{esc(user["email"])}</p><div class="mock"><b>Seu perfil</b><p>{summary}</p><a href="/profile">Dados pessoais e endereço →</a></div><h2>Seus pedidos</h2>{MARKET.order_list(orders)}<ul>{old}</ul><a href="/">Voltar ao catálogo →</a></section>'
        else:
            event(self.client_address[0], 'rota_inexistente')
            return self.reply(404, 'Página não encontrada.')
        self.reply(200, self.page('Marketplace', content, s))

    def do_POST(self):
        if not self.guard():
            return
        if self.headers.get_all('Origin') != [self.server.origin]:
            event(self.client_address[0], 'origem_recusada')
            return self.reply(403, 'Não foi possível confirmar a origem do formulário. Abra o cadastro novamente pelo endereço da loja.')
        lengths = self.headers.get_all('Content-Length') or []
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit():
            return self.reply(400, 'Requisição inválida.')
        size = int(lengths[0])
        if size > 4096:
            return self.reply(413, 'Requisição muito grande.')
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
            return self.reply(415, 'Formato inválido.')
        try:
            form = parse_qs(self.rfile.read(size).decode('utf-8'), max_num_fields=18, keep_blank_values=True)
            if any(len(v) != 1 for v in form.values()):
                raise ValueError()
        except (ValueError, UnicodeError):
            return self.reply(400, 'Formulário inválido.')
        get = lambda key: form.get(key, [''])[0]
        safe_values = {key: get(key) for key in ['name', 'email', *(f[0] for f in PROFILE_FIELDS), 'expiry']}
        s = self.session()
        if not hmac.compare_digest(get('csrf').encode(), s['csrf'].encode()):
            event(self.client_address[0], 'csrf_recusado')
            if self.path in ('/register', '/login'):
                return self.auth_page(s, 'Formulário expirado ou inválido. Confira os dados e envie novamente.', safe_values, 403)
            return self.reply(403, 'Formulário expirado ou inválido. Recarregue a página.')
        if self.path in ('/login', '/register'):
            email, password = get('email').strip().lower(), get('password')
            if limited(('auth', self.client_address[0]), 10, 300):
                event(self.client_address[0], 'limite_autenticacao')
                return self.reply(429, 'Muitas tentativas. Aguarde cinco minutos.')
            if not (3 <= len(email) <= 120 and '@' in email and 12 <= len(password) <= 128):
                return self.auth_page(s, 'Informe um e-mail válido e uma senha de 12 a 128 caracteres.', safe_values, 400)
            profile_data = None
            if self.path == '/register':
                try:
                    profile_data = validate_profile(get)
                except ValueError as error:
                    return self.auth_page(s, str(error), safe_values, 400)
            with connect() as db:
                if self.path == '/register':
                    name = get('name').strip()
                    if not 1 <= len(name) <= 60:
                        return self.auth_page(s, 'Informe seu nome (até 60 caracteres).', {'name': name, 'email': email}, 400)
                    salt = secrets.token_bytes(16)
                    try:
                        cur = db.execute('INSERT INTO users(name,email,salt,password) VALUES(?,?,?,?)', (name, email, salt, password_hash(password, salt)))
                        uid = cur.lastrowid
                        db.execute('UPDATE users SET name=? WHERE id=?', (MARKET.vault.seal(name, f'name:{uid}'), uid))
                        MARKET.create_profile(db, uid, profile_data)
                    except sqlite3.IntegrityError:
                        return self.auth_page(s, 'Não foi possível usar este e-mail. Se você já se cadastrou, entre na sua conta ou use outro e-mail.', safe_values, 400)
                else:
                    user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
                    valid = verify_password(password, user['salt'], user['password']) if user else hmac.compare_digest(password_hash(password, b'0'*16), '0'*128)
                    if not valid:
                        # Same response and expensive hash for nonexistent accounts.
                        self.reply(401, 'E-mail ou senha inválidos.')
                        return
                    uid = user['id']
                    if not user['password'].startswith('scrypt$131072$'):
                        db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(password, user['salt']), uid))
                db.execute('DELETE FROM sessions WHERE token=?', (s['token'],))
                self.create_session(uid, db)
            return self.reply(303, location='/')
        if not s['user_id']:
            return self.reply(401, 'Entre na sua conta.')
        if self.path in ('/profile', '/sell', '/buy', '/delivery'):
            if limited(('commerce', s['user_id']), 20, 60):
                return self.reply(429, 'Muitas operações. Aguarde um minuto.')
            def verify(user, password):
                if limited(('profile-password', s['user_id']), 5, 300) or not 12 <= len(password) <= 128:
                    return False
                return verify_password(password, user['salt'], user['password'])
            if MARKET.post(self, s, get, verify):
                return
        if self.path == '/logout':
            with connect() as db:
                db.execute('DELETE FROM sessions WHERE token=?', (s['token'],))
            self.create_session(None)
            return self.reply(303, location='/')
        self.reply(404, 'Página não encontrada.')


class Server(ThreadingHTTPServer):
    daemon_threads = True
    slots = threading.BoundedSemaphore(24)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--origin', help='Origem exata acessada no navegador, sem barra final')
    parser.add_argument('--cert')
    parser.add_argument('--key')
    parser.add_argument('--events', action='store_true', help='Exibir últimos eventos no terminal local')
    args = parser.parse_args()
    init()
    if args.events:
        with connect() as db:
            for row in db.execute('SELECT created,ip,kind FROM events ORDER BY id DESC LIMIT 50'):
                print(json.dumps(dict(row), ensure_ascii=False))
        return
    secure = bool(args.cert and args.key)
    if bool(args.cert) != bool(args.key):
        parser.error('Informe --cert e --key juntos.')
    if args.host not in ('127.0.0.1', 'localhost', '::1') and (not secure or not args.origin):
        parser.error('Para rede local, informe --cert, --key e --origin https://IP:PORTA.')
    origin = args.origin or f'{"https" if secure else "http"}://localhost:{args.port}'
    from urllib.parse import urlsplit
    parsed = urlsplit(origin)
    if parsed.scheme != ('https' if secure else 'http') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error('--origin deve ser uma origem válida sem caminho ou barra final.')
    server = Server((args.host, args.port), Handler)
    server.origin, server.authority, server.secure = origin, parsed.netloc, secure
    if secure:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    print(f'Campus Market: {origin}\nSomente dados fictícios. Ctrl+C para encerrar.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
