import unittest

from store_advanced import safe_web_url, advanced_bp, assistant_bp


class AdvancedStoreHelpersTest(unittest.TestCase):
    def test_safe_web_url_accepts_http_and_https(self):
        self.assertEqual(safe_web_url("https://example.com/image.png"), "https://example.com/image.png")
        self.assertEqual(safe_web_url("http://example.com/path"), "http://example.com/path")

    def test_safe_web_url_rejects_script_and_relative_urls(self):
        self.assertEqual(safe_web_url("javascript:alert(1)"), "")
        self.assertEqual(safe_web_url("/relative/path"), "")
        self.assertEqual(safe_web_url(""), "")

    def test_blueprints_have_expected_prefixes(self):
        self.assertEqual(advanced_bp.url_prefix, "/admin/advanced")
        self.assertEqual(assistant_bp.url_prefix, "/store")


if __name__ == "__main__":
    unittest.main()
