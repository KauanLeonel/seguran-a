import http.client
import re
import uuid
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode
import app

PROFILE = {'cpf': '00000000000', 'phone': '11000000000', 'street': 'Rua Exemplo',
           'number': '123', 'district': 'Bairro Modelo', 'city': 'Cidade Exemplo',
           'state': 'SP', 'zip': '00000000', 'card': '4242424242424242', 'expiry': '12/2099'}


class SecurityTests(unittest.TestCase):
    def setUp(cls):
        cls.temp = app.ROOT / ('test-data-' + uuid.uuid4().hex)
        cls.temp.mkdir()
        app.DB = cls.temp / 'test.db'
        app.KEY_PATH = cls.temp / 'test.key'
        app.ATTEMPTS.clear()
        app.init()
        cls.server = app.Server(('127.0.0.1', 0), app.Handler)
        cls.port = cls.server.server_port
        cls.server.authority = f'localhost:{cls.port}'
        cls.server.origin = f'http://localhost:{cls.port}'
        cls.server.secure = False
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    def tearDown(cls):
        cls.server.shutdown()
        cls.server.server_close()
        app.DB.unlink()
        app.KEY_PATH.unlink()
        cls.temp.rmdir()

    def request(self, path, fields=None, cookie='', origin=None, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        headers = {'Host': host or self.server.authority, 'Cookie': cookie}
        if fields is not None:
            headers.update({'Content-Type': 'application/x-www-form-urlencoded',
                            'Origin': origin or self.server.origin})
        connection.request('POST' if fields is not None else 'GET', path,
                           urlencode(fields) if fields is not None else None, headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read().decode()
        connection.close()
        return result

    def csrf(self, page):
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def test_security_and_purchase_flow(self):
        status, headers, page = self.request('/register')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIn("connect-src 'self';", headers['Content-Security-Policy'])
        with app.connect() as db:
            events_before = db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            sessions_before = db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        dev_status, dev_headers, dev_body = self.request('/.well-known/appspecific/com.chrome.devtools.json')
        self.assertEqual(dev_status, 404)
        self.assertEqual(dev_body, '{}')
        self.assertNotIn('Set-Cookie', dev_headers)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], events_before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], sessions_before)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        csrf = self.csrf(page)
        fields = {**PROFILE, 'name': '<script>alert(1)</script>', 'email': 'one@example.test',
                  'password': 'long-test-password', 'csrf': csrf}
        self.assertEqual(self.request('/register', fields, cookie, 'https://evil.test')[0], 403)
        self.assertEqual(self.request('/register', fields, cookie, 'null')[0], 403)
        status, _, error_page = self.request('/register', {**fields, 'csrf': 'wrong'}, cookie)
        self.assertEqual(status, 403)
        self.assertIn('role="alert"', error_page)
        self.assertIn('Formulário expirado ou inválido.', error_page)
        self.assertIn('action="/register"', error_page)
        self.assertIn('value="one@example.test"', error_page)
        self.assertNotIn('long-test-password', error_page)
        status, headers, _ = self.request('/register', fields, cookie)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        authenticated = headers['Set-Cookie'].split(';')[0]
        status, _, home = self.request('/', cookie=authenticated)
        self.assertEqual(status, 200)
        self.assertIn('Minha conta', home)
        self.assertNotIn('Uma compra simulada', home)
        self.assertNotEqual(authenticated, cookie)
        self.assertEqual(self.request('/account', cookie=cookie)[0], 303)
        _, _, page = self.request('/account', cookie=authenticated)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)
        csrf = self.csrf(page)
        _, _, checkout_page = self.request('/checkout?id=1', cookie=authenticated)
        checkout = re.search(r'name="checkout" value="([^"]+)"', checkout_page).group(1)
        self.assertEqual(self.request('/buy', {'csrf': csrf, 'checkout': checkout, 'quantity': '1', 'shipping': 'standard', 'cents': '1', 'user_id': '999'}, authenticated)[0], 303)
        with app.connect() as db:
            order = db.execute('SELECT * FROM purchases').fetchone()
            self.assertEqual(order['total_cents'], 14490)
            self.assertEqual(order['buyer_id'], 1)
            self.assertNotEqual(db.execute('SELECT password FROM users').fetchone()[0], fields['password'])
        _, headers, page = self.request('/register')
        second = headers['Set-Cookie'].split(';')[0]
        status, _, duplicate = self.request('/register', {**fields, 'csrf': self.csrf(page)}, second)
        self.assertEqual(status, 400)
        self.assertIn('Se você já se cadastrou', duplicate)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
        _, headers, _ = self.request('/register', {**fields, 'email': 'two@example.test', 'csrf': self.csrf(page)}, second)
        second = headers['Set-Cookie'].split(';')[0]
        self.assertNotIn('Pedido #1', self.request('/account', cookie=second)[2])
        self.assertEqual(self.request('/logout', {'csrf': csrf}, authenticated)[0], 303)
        self.assertEqual(self.request('/account', cookie=authenticated)[0], 303)
        _, login_headers, login_page = self.request('/login')
        status, login_headers, _ = self.request('/login', {'email': 'one@example.test', 'password': 'long-test-password', 'csrf': self.csrf(login_page)}, login_headers['Set-Cookie'].split(';')[0])
        self.assertEqual(status, 303)
        self.assertEqual(login_headers['Location'], '/')
        self.assertIn('Minha conta', self.request('/', cookie=login_headers['Set-Cookie'].split(';')[0])[2])
        self.assertEqual(self.request('/data/market.db')[0], 404)
        self.assertEqual(self.request('/', host='evil.test')[0], 400)
        _, headers, page = self.request('/login')
        cookie = headers['Set-Cookie'].split(';')[0]
        fields = {'csrf': self.csrf(page), 'email': "' OR 1=1 --@example.test", 'password': 'incorrect-password'}
        self.assertEqual(self.request('/login', fields, cookie)[0], 401)
        for _ in range(10):
            status = self.request('/login', fields, cookie)[0]
        self.assertEqual(status, 429)

    def register(self, email):
        _, headers, page = self.request('/register')
        status, headers, _ = self.request('/register', {**PROFILE, 'csrf': self.csrf(page), 'name': 'Cliente Fictício', 'email': email, 'password': 'long-test-password'}, headers['Set-Cookie'].split(';')[0])
        self.assertEqual(status, 303)
        cookie = headers['Set-Cookie'].split(';')[0]
        _, _, page = self.request('/account', cookie=cookie)
        return cookie, self.csrf(page)

    def test_marketplace_delivery_authorization_and_encryption(self):
        seller, sc = self.register('seller@example.test')
        buyer, bc = self.register('buyer@example.test')
        outsider, oc = self.register('outsider@example.test')
        status, _, _ = self.request('/sell', {'csrf': sc, 'title': '<b>Notebook</b>', 'description': 'Notebook para estudar', 'price': '200,50', 'stock': '2', 'seller_id': '2'}, seller)
        self.assertEqual(status, 303)
        with app.connect() as db:
            product = db.execute('SELECT * FROM listings WHERE seller_id=1').fetchone()
            pid = product['id']
            raw_profile = db.execute('SELECT data FROM profiles WHERE user_id=2').fetchone()[0]
            self.assertNotIn('4242424242424242', raw_profile)
            self.assertNotIn('Rua Exemplo', raw_profile)
            self.assertNotIn('Cliente', db.execute('SELECT name FROM users WHERE id=2').fetchone()[0])
            self.assertEqual(app.MARKET.profile(db, 2)['card'], '4242424242424242')
        self.assertIn('&lt;b&gt;Notebook&lt;/b&gt;', self.request('/')[2])
        self.assertEqual(self.request(f'/checkout?id={pid}', cookie=seller)[0], 400)
        _, _, page = self.request(f'/checkout?id={pid}', cookie=buyer)
        token = re.search(r'name="checkout" value="([^"]+)"', page).group(1)
        fields = {'csrf': bc, 'checkout': token, 'quantity': '2', 'shipping': 'express', 'total_cents': '1'}
        self.assertEqual(self.request('/buy', {**fields, 'csrf': oc}, outsider)[0], 400)
        status, headers, _ = self.request('/buy', fields, buyer)
        self.assertEqual(status, 303)
        order_url = headers['Location']
        self.assertEqual(self.request('/buy', fields, buyer)[1]['Location'], order_url)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT stock FROM listings WHERE id=?', (pid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM purchases').fetchone()[0], 1)
            order = db.execute('SELECT * FROM purchases').fetchone()
            self.assertEqual(order['total_cents'], 43100)
            self.assertNotIn('Rua Exemplo', order['delivery'])
            oid = order['id']
        self.assertEqual(self.request(order_url, cookie=outsider)[0], 404)
        seller_page = self.request(order_url, cookie=seller)[2]
        self.assertIn('Rua Exemplo', seller_page)
        self.assertNotIn('4242', seller_page)
        self.assertNotIn('CPF', seller_page)
        self.assertEqual(self.request('/delivery', {'csrf': bc, 'order': oid, 'action': 'receive'}, buyer)[0], 409)
        self.assertEqual(self.request('/delivery', {'csrf': oc, 'order': oid, 'action': 'ship'}, outsider)[0], 409)
        self.assertEqual(self.request('/delivery', {'csrf': sc, 'order': oid, 'action': 'ship'}, seller)[0], 303)
        self.assertEqual(self.request('/delivery', {'csrf': bc, 'order': oid, 'action': 'receive'}, buyer)[0], 303)
        self.assertIn('Entregue', self.request(order_url, cookie=buyer)[2])

    def test_profile_reauthentication_validation_and_vault_binding(self):
        from cryptography.exceptions import InvalidTag
        cookie, csrf = self.register('profile@example.test')
        with app.connect() as db:
            profile = app.MARKET.profile(db, 1)
            sealed = db.execute('SELECT data FROM profiles WHERE user_id=1').fetchone()[0]
        with self.assertRaises(InvalidTag):
            app.MARKET.vault.open(sealed, 'profile:2')
        fields = {**profile, 'name': 'Nome Atualizado', 'password': 'wrong-password', 'csrf': csrf}
        self.assertEqual(self.request('/profile', fields, cookie)[0], 400)
        fields['password'] = 'long-test-password'
        self.assertEqual(self.request('/profile', {**fields, 'card': '4111111111111111'}, cookie)[0], 400)
        self.assertEqual(self.request('/profile', {**fields, 'state': 'XX'}, cookie)[0], 400)
        self.assertEqual(self.request('/profile', {**fields, 'csrf': 'wrong'}, cookie)[0], 403)
        self.assertEqual(self.request('/profile', fields, cookie)[0], 303)
        self.assertIn('Nome Atualizado', self.request('/account', cookie=cookie)[2])
        self.assertNotIn('4242424242424242', self.request('/account', cookie=cookie)[2])

    def test_atomic_last_item_and_expired_checkout(self):
        import concurrent.futures
        import time
        buyer, csrf = self.register('stock@example.test')
        with app.connect() as db:
            db.execute('UPDATE listings SET stock=1 WHERE id=1')
        tokens = []
        for _ in range(3):
            page = self.request('/checkout?id=1', cookie=buyer)[2]
            tokens.append(re.search(r'name="checkout" value="([^"]+)"', page).group(1))
        with app.connect() as db:
            db.execute('UPDATE checkouts SET expires=? WHERE token=?', (time.time()-1, tokens[2]))
        def purchase(token):
            return self.request('/buy', {'csrf': csrf, 'checkout': token, 'quantity': '1', 'shipping': 'standard'}, buyer)[0]
        self.assertEqual(purchase(tokens[2]), 400)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(purchase, tokens[:2]))
        self.assertEqual(sorted(results), [303, 400])
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT stock FROM listings WHERE id=1').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM purchases').fetchone()[0], 1)

    def test_legacy_migration_and_missing_key_fail_closed(self):
        import sqlite3
        original_db, original_key, original_market = app.DB, app.KEY_PATH, app.MARKET
        legacy_db, legacy_key = self.temp / 'legacy.db', self.temp / 'legacy.key'
        try:
            app.DB, app.KEY_PATH = legacy_db, legacy_key
            db = sqlite3.connect(legacy_db)
            db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, password TEXT NOT NULL)')
            old_hash = app.password_hash('long-test-password', b'0'*16, n=16384).split('$')[-1]
            db.execute('INSERT INTO users VALUES(1,?,?,?,?)', ('Nome Antigo', 'old@example.test', b'0'*16, old_hash))
            db.commit()
            db.close()
            app.init()
            app.init()  # Migration is idempotent.
            with app.connect() as db:
                user = db.execute('SELECT * FROM users WHERE id=1').fetchone()
                self.assertEqual(app.MARKET.name(user['name'], 1), 'Nome Antigo')
                self.assertNotIn('Nome Antigo', user['name'])
                self.assertTrue(app.verify_password('long-test-password', user['salt'], user['password']))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM profiles').fetchone()[0], 1)
            _, headers, page = self.request('/login')
            status, _, _ = self.request('/login', {'csrf': self.csrf(page), 'email': 'old@example.test', 'password': 'long-test-password'}, headers['Set-Cookie'].split(';')[0])
            self.assertEqual(status, 303)
            with app.connect() as db:
                self.assertTrue(db.execute('SELECT password FROM users WHERE id=1').fetchone()[0].startswith('scrypt$131072$'))
            key_bytes = legacy_key.read_bytes()
            legacy_key.unlink()
            with self.assertRaises(RuntimeError):
                app.init()
            self.assertFalse(legacy_key.exists())
            legacy_key.write_bytes(key_bytes)
        finally:
            app.DB, app.KEY_PATH, app.MARKET = original_db, original_key, original_market
            legacy_db.unlink(missing_ok=True)
            legacy_key.unlink(missing_ok=True)

    def test_manual_registration_requires_all_fields(self):
        from html.parser import HTMLParser
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input':
                    self.values[attrs.get('name')] = attrs.get('value', '')
        _, headers, page = self.request('/register')
        inputs = Inputs()
        inputs.feed(page)
        for key in ['name', 'email', 'password', *PROFILE]:
            self.assertEqual(inputs.values[key], '')
        fields = {'csrf': self.csrf(page), 'name': 'Manual', 'email': 'manual@example.test', 'password': 'long-test-password'}
        cookie = headers['Set-Cookie'].split(';')[0]
        for missing in ('cpf', 'phone', 'street', 'card', 'expiry'):
            status, _, error = self.request('/register', {**PROFILE, **fields, missing: ''}, cookie)
            self.assertEqual(status, 400)
            self.assertNotIn('value="4242424242424242"', error)
            self.assertNotIn('value="long-test-password"', error)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
        self.assertEqual(self.request('/register', {**PROFILE, **fields}, cookie)[0], 303)
        with app.connect() as db:
            self.assertEqual(app.MARKET.profile(db, 1), PROFILE)

    def test_cleanup_previous_defaults_and_incomplete_checkout(self):
        cookie, csrf = self.register('old-default@example.test')
        self.register('edited@example.test')
        with app.connect() as db:
            old = {**PROFILE, 'number': '1'}
            db.execute('UPDATE profiles SET data=? WHERE user_id=1', (app.MARKET.vault.seal(old, 'profile:1'),))
            db.execute('DELETE FROM commerce_migrations WHERE version=2')
        app.init()
        with app.connect() as db:
            self.assertFalse(any(app.MARKET.profile(db, 1).values()))
            self.assertEqual(app.MARKET.profile(db, 2), PROFILE)
        self.assertIn('Complete seus dados', self.request('/account', cookie=cookie)[2])
        self.assertEqual(self.request('/checkout?id=1', cookie=cookie)[1]['Location'], '/profile')
        self.assertEqual(self.request('/profile', {**PROFILE, 'name': 'Manual', 'password': 'long-test-password', 'csrf': csrf}, cookie)[0], 303)
        app.init()
        with app.connect() as db:
            self.assertEqual(app.MARKET.profile(db, 1), PROFILE)

    def test_generator_creates_users_through_http_every_minute(self):
        import generate_users
        from unittest.mock import patch
        sleeps = []
        with patch('builtins.print'):
            self.assertEqual(generate_users.run(self.server.origin, count=2, sleep=sleeps.append), 2)
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(0 < sleeps[0] <= 60)
        with app.connect() as db:
            users = db.execute('SELECT id,email FROM users').fetchall()
            self.assertEqual(len(users), 2)
            self.assertNotEqual(users[0]['email'], users[1]['email'])
            for user in users:
                self.assertTrue(all(app.MARKET.profile(db, user['id']).values()))


if __name__ == '__main__':
    unittest.main()
