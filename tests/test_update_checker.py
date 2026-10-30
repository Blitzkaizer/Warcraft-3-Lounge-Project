import unittest
from src.core.update_checker import parse_semver

class TestUpdateChecker(unittest.TestCase):
    def test_parse_semver(self):
        self.assertEqual(parse_semver("v1.0.1"), (1, 0, 1))
        self.assertEqual(parse_semver("1.2.3"), (1, 2, 3))
        self.assertEqual(parse_semver("v2.0.0-beta"), (2, 0, 0))
        self.assertTrue(parse_semver("v1.0.2") > parse_semver("v1.0.1"))
        self.assertTrue(parse_semver("v2.0.0") > parse_semver("v1.9.9"))
        self.assertFalse(parse_semver("v1.0.0") > parse_semver("v1.0.1"))

if __name__ == '__main__':
    unittest.main()
