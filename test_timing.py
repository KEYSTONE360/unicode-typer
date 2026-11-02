import unittest
from timing import parse_interval_ms, parse_delay_seconds

class TimingTests(unittest.TestCase):
    def test_fractional_milliseconds_are_preserved(self):
        for value in ('0.01', '0.1', '0.5', '12.5', '50.25', '4999.99'):
            self.assertEqual(parse_interval_ms(value), float(value))

    def test_boundaries(self):
        self.assertEqual(parse_interval_ms('5000'), 5000)
        self.assertEqual(parse_interval_ms(' 0.01 '), .01)
        self.assertEqual(parse_delay_seconds('1.25'), 1.25)

    def test_invalid_values_are_rejected(self):
        for value in ('', 'text', 'NaN', 'Infinity', '-Infinity', '0', '-1', '.009', '5000.01'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_interval_ms(value)
        for value in ('NaN', 'Infinity', '0.9', '60.1'):
            with self.assertRaises(ValueError):
                parse_delay_seconds(value)

if __name__ == '__main__':
    unittest.main()
