"""Generator timing and recovery, without waiting or writing to the real database."""
from email.message import Message
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import generate_users


class GeneratorTests(unittest.TestCase):
    def test_partial_final_batch_and_time_spent_registering(self):
        create, sleep = Mock(return_value='test@example.test'), Mock()
        with patch('builtins.print'), patch('generate_users.time.monotonic', side_effect=[0, 4, 30, 36, 60]):
            self.assertEqual(generate_users.run('http://localhost:8000', count=25,
                             create=create, sleep=sleep), 25)
        self.assertEqual(create.call_count, 25)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [26, 24])

    def test_slow_batches_do_not_accumulate_a_backlog(self):
        sleep = Mock()
        with patch('builtins.print'), patch('generate_users.time.monotonic', side_effect=[0, 40, 40]):
            generate_users.run('http://localhost:8000', count=11,
                               create=Mock(return_value='test@example.test'), sleep=sleep)
        sleep.assert_called_once_with(0)

    def test_unlimited_mode_runs_until_interrupted(self):
        create, sleep = Mock(return_value='test@example.test'), Mock(side_effect=[None, KeyboardInterrupt])
        with patch('builtins.print'), self.assertRaises(KeyboardInterrupt):
            generate_users.run('http://localhost:8000', create=create, sleep=sleep)
        self.assertEqual(create.call_count, 20)
        self.assertEqual(sleep.call_count, 2)

    def test_temporary_errors_retry_without_counting_failures(self):
        headers = Message()
        headers['Retry-After'] = '90'
        errors = [HTTPError('http://localhost:8000', 429, 'limit', headers, None),
                  HTTPError('http://localhost:8000', 503, 'unavailable', {}, None),
                  URLError('offline'), TimeoutError()]
        create = Mock(side_effect=[*errors, 'ok@example.test'])
        sleep = Mock()
        with patch('builtins.print'):
            self.assertEqual(generate_users.run('http://localhost:8000', count=1,
                             create=create, sleep=sleep), 1)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [90, 30, 30, 30])

    def test_bad_certificate_and_permanent_http_error_stop(self):
        errors = [URLError(ssl.SSLCertVerificationError('invalid certificate')),
                  HTTPError('http://localhost:8000', 403, 'forbidden', {}, None)]
        for error in errors:
            with self.subTest(error=error):
                sleep = Mock()
                with patch('builtins.print'), self.assertRaises(type(error)):
                    generate_users.run('http://localhost:8000', count=1,
                                       create=Mock(side_effect=error), sleep=sleep)
                sleep.assert_not_called()

    def test_invalid_schedule_is_rejected_before_any_request(self):
        for options in ({'count': -1}, {'batch_size': 0}, {'interval': 0},
                        {'interval': float('nan')}, {'interval': float('inf')}):
            with self.subTest(options=options):
                create = Mock()
                with self.assertRaises(ValueError):
                    generate_users.run('http://localhost:8000', create=create, **options)
                create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
