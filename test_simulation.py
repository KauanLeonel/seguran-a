"""Simulation integrity, access control, table rendering and scheduling."""
import unittest
from unittest.mock import Mock, patch

import app
import simulation
import simulate_market
import test_app


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_app.SecurityTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_batch_counts_encryption_stock_and_idempotence(self):
        result = simulation.create_batch('test-batch')
        self.assertEqual([len(result[k]) for k in ('users', 'listings', 'purchases')], [5, 5, 5])
        self.assertIsNone(simulation.create_batch('test-batch'))
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM listings').fetchone()[0], len(app.PRODUCTS) + 5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM purchases').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM simulation_batches').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM checkouts').fetchone()[0], 0)
            for uid in result['users']:
                user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
                self.assertNotIn('Cliente Fictício', user['name'])
                self.assertTrue(app.MARKET.name(user['name'], uid).startswith('Cliente Fictício'))
                self.assertTrue(user['password'].startswith('scrypt$131072$'))
                self.assertTrue(all(app.MARKET.profile(db, uid).values()))
            for order in db.execute('SELECT * FROM purchases').fetchall():
                self.assertNotEqual(order['buyer_id'], order['seller_id'])
                self.assertEqual(order['quantity'], 1)
                self.assertEqual(order['total_cents'], order['unit_cents'] + 1500)
                self.assertEqual(order['status'], 'paid')
                self.assertEqual(db.execute('SELECT stock FROM listings WHERE id=?', (order['listing_id'],)).fetchone()[0], 4)
                delivery = app.MARKET.vault.open(order['delivery'], f'order:{order["checkout_token"]}')
                self.assertIn('street', delivery)

    def test_failure_rolls_back_the_entire_batch(self):
        original = app.MARKET.create_purchase
        calls = 0
        def purchase(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError('injected failure')
            return original(*args)
        with patch.object(app.MARKET, 'create_purchase', side_effect=purchase), self.assertRaises(RuntimeError):
            simulation.create_batch('failed-batch')
        with app.connect() as db:
            for table in ('users', 'profiles', 'purchases', 'checkouts', 'simulation_batches',
                          'simulation_users', 'simulation_listings', 'simulation_purchases'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM listings').fetchone()[0], len(app.PRODUCTS))
        self.assertIsNotNone(simulation.create_batch('failed-batch'))

    def test_page_requires_login_and_shows_only_simulated_records(self):
        self.assertEqual(self.fixture.request('/simulation')[0], 303)
        cookie, _ = self.fixture.register('private-account@example.test')
        empty = self.fixture.request('/simulation', cookie=cookie)[2]
        self.assertIn('Nenhum lote gerado', empty)
        self.assertNotIn('private-account@example.test', empty)
        simulation.create_batch('html-batch')
        with app.connect() as db:
            listing_id = db.execute('SELECT listing_id FROM simulation_listings LIMIT 1').fetchone()[0]
            db.execute('UPDATE listings SET title=? WHERE id=?', ('<script>alert(1)</script>', listing_id))
        status, headers, page = self.fixture.request('/simulation', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn('http-equiv="refresh" content="10"', page)
        self.assertEqual(page.count('<table class="sql-table">'), 3)
        self.assertIn('html-batch', page)
        self.assertIn('SELECT id,', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>', page)
        for secret in ('private-account@example.test', '4242424242424242', 'scrypt$', '00000000000', 'Rua Exemplo'):
            self.assertNotIn(secret, page)
        for query in ('page=0', 'page=-1', 'page=1&page=2', 'sql=DROP', 'page=1%20OR%201=1'):
            self.assertEqual(self.fixture.request('/simulation?' + query, cookie=cookie)[0], 400, query)
        second = self.fixture.request('/simulation?page=2', cookie=cookie)[2]
        self.assertIn('Nenhum registro nesta página', second)


class ScheduleTests(unittest.TestCase):
    def test_immediate_then_every_ten_seconds_until_interrupted(self):
        create = Mock(return_value={'created': '2026-10-26T12:00:00-03:00'})
        sleep = Mock(side_effect=[None, KeyboardInterrupt])
        with patch('builtins.print'), patch('simulate_market.time.time', side_effect=[0, 10]), \
                patch('simulate_market.time.monotonic', side_effect=[0, 4, 10, 16]), self.assertRaises(KeyboardInterrupt):
            simulate_market.run(create=create, sleep=sleep)
        self.assertEqual([call.args[0] for call in create.call_args_list], ['sim-10-0', 'sim-10-1'])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [6, 4])

    def test_failed_batch_is_retried_next_interval(self):
        create = Mock(side_effect=[RuntimeError('private detail'), {'created': 'now'}])
        sleep = Mock(side_effect=[None, KeyboardInterrupt])
        with patch('builtins.print') as output, self.assertRaises(KeyboardInterrupt):
            simulate_market.run(create=create, sleep=sleep)
        self.assertEqual(create.call_count, 2)
        self.assertNotIn('private detail', str(output.call_args_list))

    def test_once_does_not_sleep_and_duplicate_is_not_counted(self):
        sleep = Mock()
        with patch('builtins.print'):
            self.assertEqual(simulate_market.run(once=True, create=Mock(return_value=None), sleep=sleep), 0)
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
