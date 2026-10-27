"""Transport and optional real-PostgreSQL integration tests.

RUN_POSTGRES_TESTS=1 enables tests using PGHOST/PGPORT/PGUSER/PGPASSWORD.
They use isolated random schemas and never modify public tables.
"""
from io import BytesIO
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

import app
import test_app
PROFILE = test_app.PROFILE
from wsgi import create_application


class WsgiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_app.SecurityTests()
        self.fixture.setUp()
        self.application = create_application('https://market.example.test', initialize=False)

    def tearDown(self):
        self.fixture.tearDown()

    def request(self, path, fields=None, cookie='', remote='127.0.0.1', real='192.0.2.10', host='market.example.test', origin='https://market.example.test'):
        parsed = urlsplit(path)
        body = urlencode(fields).encode() if fields is not None else b''
        environ = {'REQUEST_METHOD': 'POST' if fields is not None else 'GET', 'PATH_INFO': parsed.path,
                   'QUERY_STRING': parsed.query, 'HTTP_HOST': host, 'HTTP_COOKIE': cookie,
                   'REMOTE_ADDR': remote, 'HTTP_X_REAL_IP': real, 'wsgi.input': BytesIO(body)}
        if fields is not None:
            environ.update(CONTENT_TYPE='application/x-www-form-urlencoded', CONTENT_LENGTH=str(len(body)), HTTP_ORIGIN=origin)
        captured = {}
        def start(status, headers):
            captured.update(status=int(status.split()[0]), headers=dict(headers))
        result = b''.join(self.application(environ, start)).decode()
        return captured['status'], captured['headers'], result

    def test_registration_and_secure_cookie_behind_proxy(self):
        status, headers, page = self.request('/register')
        self.assertEqual(status, 200)
        self.assertIn('; Secure', headers['Set-Cookie'])
        self.assertNotIn('Connection', headers)
        fields = {**PROFILE, 'name': 'Usuário WSGI', 'email': 'wsgi@example.test', 'password': 'long-test-password', 'csrf': self.fixture.csrf(page)}
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('/register', fields, cookie, origin='https://evil.test')[0], 403)
        status, headers, _ = self.request('/register', fields, cookie)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('Minha conta', self.request('/', cookie=cookie)[2])
        self.assertEqual(self.request('/', host='evil.test')[0], 400)

    def test_untrusted_forwarded_ip_is_ignored(self):
        with patch('app.limited', return_value=False) as limited:
            self.request('/style.css', remote='198.51.100.5', real='192.0.2.200')
            self.assertEqual(limited.call_args.args[0], ('request', '198.51.100.5'))
        with patch('app.limited', return_value=False) as limited:
            self.request('/style.css', remote='127.0.0.1', real='192.0.2.200')
            self.assertEqual(limited.call_args.args[0], ('request', '192.0.2.200'))


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'PostgreSQL integration not configured')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from uuid import uuid4
        self.fixture = test_app.SecurityTests()
        self.fixture.setUp()
        self.schema = 'test_' + uuid4().hex
        with psycopg.connect(autocommit=True) as db:
            db.execute(psycopg.sql.SQL('CREATE SCHEMA {}').format(psycopg.sql.Identifier(self.schema)))
        self.environment = patch.dict(os.environ, {'MARKET_DATABASE': 'postgres', 'MARKET_PG_SCHEMA': self.schema})
        self.environment.start()
        app.init()

    def tearDown(self):
        import psycopg
        with psycopg.connect(autocommit=True) as db:
            db.execute(psycopg.sql.SQL('DROP SCHEMA {} CASCADE').format(psycopg.sql.Identifier(self.schema)))
        self.environment.stop()
        self.fixture.tearDown()

    def test_marketplace(self):
        self.fixture.test_marketplace_delivery_authorization_and_encryption()

    def test_concurrent_stock(self):
        self.fixture.test_atomic_last_item_and_expired_checkout()

    def test_duplicate_registration_and_sessions(self):
        self.fixture.test_security_and_purchase_flow()

    def test_sqlite_import_and_refusal_to_overwrite(self):
        from migrate_database import migrate
        import simulation
        # Populate the isolated SQLite source, then return to the empty PG target.
        with patch.dict(os.environ, {'MARKET_DATABASE': 'sqlite'}):
            app.init()
            cookie, csrf = self.fixture.register('import@example.test')
            simulation.create_batch('imported-simulation')
        counts = migrate(app.DB)
        self.assertEqual(counts['users'], 6)
        self.assertEqual(counts['simulation_users'], 5)
        self.assertEqual(counts['simulation_listings'], 5)
        self.assertEqual(counts['simulation_purchases'], 5)
        with app.connect() as db:
            user = db.execute('SELECT * FROM users ORDER BY id').fetchone()
            self.assertEqual(app.MARKET.name(user['name'], user['id']), 'Cliente Fictício')
            self.assertEqual(app.MARKET.profile(db, user['id']), PROFILE)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        with self.assertRaises(RuntimeError):
            migrate(app.DB)
        self.fixture.register('after-import@example.test')
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 7)
        self.assertIn('imported-simulation', simulation.render(app.connect, app.MARKET, 1))

    def test_simulation_batch_and_snapshot(self):
        import simulation
        result = simulation.create_batch('postgres-batch')
        self.assertEqual(len(result['purchases']), 5)
        self.assertIsNone(simulation.create_batch('postgres-batch'))
        page = simulation.render(app.connect, app.MARKET, 1)
        self.assertIn('postgres-batch', page)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM simulation_users').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM purchases WHERE buyer_id=seller_id').fetchone()[0], 0)
        with patch.object(app.MARKET, 'create_purchase', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                simulation.create_batch('failed-postgres-batch')
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM simulation_users').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM purchases').fetchone()[0], 5)
            self.assertFalse(db.execute('SELECT 1 FROM simulation_batches WHERE batch_id=?', ('failed-postgres-batch',)).fetchone())


if __name__ == '__main__':
    unittest.main()
