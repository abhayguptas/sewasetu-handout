# -*- coding: utf-8 -*-
import unittest
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(current_dir, "..", "app.py")):
    APP_DIR = os.path.abspath(os.path.join(current_dir, ".."))
elif os.path.exists(os.path.join(current_dir, "..", "app", "app.py")):
    APP_DIR = os.path.abspath(os.path.join(current_dir, "..", "app"))
else:
    raise RuntimeError("Cannot locate app directory")

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import werkzeug
if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"


class TestCitizenExperience(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_mobile_viewport_meta_tag(self):
        """
        Verify that base template contains viewport meta tag for mobile responsiveness.
        """
        res = self.client.get("/")
        html = res.get_data(as_text=True)
        self.assertIn('<meta name="viewport"', html)

    def test_informational_footer_routes_resolve(self):
        """
        Verify that /contact, /grievance, and /rti resolve with HTTP 200
        rather than broken 404s (which caused 24,000+ 404s in logs).
        """
        for endpoint in ["/contact", "/grievance", "/rti"]:
            res = self.client.get(endpoint)
            self.assertEqual(res.status_code, 200, f"Endpoint {endpoint} should return 200 OK")
            html = res.get_data(as_text=True)
            self.assertIn("Sewa Setu", html)

    def test_guideline_download_link_friendly_handling(self):
        """
        Verify that /downloads/oap-guidelines-2023.pdf redirects gracefully to /about
        rather than returning a broken 404.
        """
        res = self.client.get("/downloads/oap-guidelines-2023.pdf")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/about", res.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main()
