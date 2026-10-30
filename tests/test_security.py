# -*- coding: utf-8 -*-
import unittest
import os
import sys
import io
import hashlib

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


class TestSecurityAuthorization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        import configparser

        config = configparser.ConfigParser()
        config_path = os.path.join(APP_DIR, "config", "app.ini")
        config.read(config_path)

        cls.conn = psycopg2.connect(
            host=config.get("database", "host"),
            port=config.get("database", "port"),
            dbname=config.get("database", "name"),
            user=config.get("database", "user"),
            password=config.get("database", "password"),
        )
        cls.conn.autocommit = True
        cur = cls.conn.cursor()

        # Clean up and insert test fixtures for Citizen A and Citizen B
        cur.execute("DELETE FROM applications WHERE application_no IN ('SEC_TEST_A', 'SEC_TEST_B')")
        cur.execute("DELETE FROM portal_users WHERE mobile IN ('9999900001', '9999900002')")

        # Citizen A (Mobile: 9999900001, DOB: 1950-01-01)
        cur.execute(
            """INSERT INTO applications 
               (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
               VALUES ('SEC_TEST_A', 'Citizen A', '9999900001', '1950-01-01', 'Village A', 'Sonari', '1111111111', 'SBIN0001111', 'PENDING', NOW())
               RETURNING id"""
        )
        cls.app_id_a = cur.fetchone()[0]

        # Citizen B (Mobile: 9999900002, DOB: 1952-02-02)
        cur.execute(
            """INSERT INTO applications 
               (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
               VALUES ('SEC_TEST_B', 'Citizen B', '9999900002', '1952-02-02', 'Village B', 'Namti', '2222222222', 'SBIN0002222', 'PENDING', NOW())
               RETURNING id"""
        )
        cls.app_id_b = cur.fetchone()[0]

        # Create portal user accounts
        pwd_hash_a = hashlib.sha256("01011950".encode("utf-8")).hexdigest()
        pwd_hash_b = hashlib.sha256("02021952".encode("utf-8")).hexdigest()
        cur.execute("INSERT INTO portal_users (mobile, password_hash) VALUES ('9999900001', %s)", (pwd_hash_a,))
        cur.execute("INSERT INTO portal_users (mobile, password_hash) VALUES ('9999900002', %s)", (pwd_hash_b,))
        cur.close()

    @classmethod
    def tearDownClass(cls):
        cur = cls.conn.cursor()
        cur.execute("DELETE FROM applications WHERE application_no IN ('SEC_TEST_A', 'SEC_TEST_B')")
        cur.execute("DELETE FROM portal_users WHERE mobile IN ('9999900001', '9999900002')")
        cur.close()
        cls.conn.close()

    def setUp(self):
        import app
        self.app = app.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_unauthenticated_access_to_application_blocked(self):
        """
        Situation 1: Unauthenticated visitor requesting a citizen's application.
        Expected: Blocked and redirected to status login page.
        """
        res = self.client.get(f"/application/{self.app_id_a}", follow_redirects=False)
        self.assertIn(res.status_code, [302, 403],
                      "Unauthenticated request must be blocked or redirected to login")
        if res.status_code == 302:
            self.assertIn("/status", res.headers.get("Location", ""))

    def test_citizen_a_cannot_view_citizen_b_application_idor(self):
        """
        Situation 2: Citizen A logged in requesting Citizen B's application record (IDOR).
        Expected: Strict 403 Forbidden.
        """
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["portal_mobile"] = "9999900001"  # Citizen A

        # Citizen A requests Citizen B's record
        res = self.client.get(f"/application/{self.app_id_b}")
        self.assertEqual(res.status_code, 403,
                         f"IDOR violation: Citizen A was able to view Citizen B's record (got {res.status_code})")

    def test_citizen_a_cannot_download_citizen_b_ack_pdf_idor(self):
        """
        Situation 3: Citizen A or unauthenticated visitor requesting Citizen B's acknowledgment PDF.
        Expected: Strict 403 Forbidden.
        """
        # Unauthenticated request
        res_unauth = self.client.get(f"/ack/{self.app_id_b}.pdf")
        self.assertEqual(res_unauth.status_code, 403,
                         "Unauthenticated visitor must not be able to download acknowledgment PDF")

        # Citizen A requests Citizen B's PDF
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["portal_mobile"] = "9999900001"  # Citizen A

        res_idor = self.client.get(f"/ack/{self.app_id_b}.pdf")
        self.assertEqual(res_idor.status_code, 403,
                         "Citizen A must not be able to download Citizen B's acknowledgment PDF")

    def test_citizen_cannot_perform_admin_action_privilege_escalation(self):
        """
        Situation 4: Ordinary logged-in citizen attempting an administrative action (approval/rejection).
        Expected: Strict 403 Forbidden.
        """
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["portal_mobile"] = "9999900001"  # Citizen A (not admin)

        res_approve = self.client.post(f"/admin/approve/{self.app_id_a}")
        self.assertEqual(res_approve.status_code, 403,
                         "Privilege escalation: Citizen was able to access /admin/approve")

        res_reject = self.client.post(f"/admin/reject/{self.app_id_a}")
        self.assertEqual(res_reject.status_code, 403,
                         "Privilege escalation: Citizen was able to access /admin/reject")

    def test_authorized_administrator_can_perform_approval(self):
        """
        Situation 5: Authorized administrator performing the approval action.
        Expected: Success (HTTP 302 redirect to admin dashboard, application status becomes APPROVED).
        """
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["admin"] = True

        res = self.client.post(f"/admin/approve/{self.app_id_b}", follow_redirects=False)
        self.assertIn(res.status_code, [200, 302], "Admin approval should succeed")

        cur = self.conn.cursor()
        cur.execute("SELECT status FROM applications WHERE id = %s", (self.app_id_b,))
        status = cur.fetchone()[0]
        cur.close()
        self.assertEqual(status, "APPROVED", "Application status must be updated to APPROVED by admin")

    def test_sql_injection_cannot_modify_records(self):
        """
        Situation 6: Malicious SQL injection payloads against password reset (/status/reset).
        Verify that injection attempts cannot modify records or reset other users' accounts.
        """
        cur = self.conn.cursor()
        cur.execute("SELECT password_hash FROM portal_users WHERE mobile = '9999900002'")
        original_hash_b = cur.fetchone()[0]

        # Attempt SQL injection to reset Citizen B's password via Citizen A input
        sqli_payload = "9999900001' OR mobile='9999900002"
        res = self.client.post("/status/reset", data={"mobile": sqli_payload}, follow_redirects=True)
        self.assertIn(res.status_code, [200, 302])

        # Verify Citizen B's password hash was NOT modified
        cur.execute("SELECT password_hash FROM portal_users WHERE mobile = '9999900002'")
        current_hash_b = cur.fetchone()[0]
        cur.close()

        self.assertEqual(original_hash_b, current_hash_b,
                         "SQL Injection security failure: Citizen B's password was modified by malicious payload!")

    def test_upload_security_magic_bytes_and_path_traversal(self):
        """
        Situation 7: File upload security hardening:
        - Spoofed magic bytes (e.g. PHP/shell code named image.jpg) must be rejected.
        - Directory traversal filenames (e.g. ../../etc/passwd) must be sanitized safely.
        - Oversized files (> 5MB) must be rejected.
        - Genuine JPEG and PDF files must be accepted.
        """
        with self.client.session_transaction() as sess:
            sess["verified_mobile"] = "9999900001"

        # 1. Spoofed magic bytes: shell script named fake.jpg
        fake_shell_content = b"<?php system($_GET['cmd']); ?>"
        data_spoof = {"document": (io.BytesIO(fake_shell_content), "fake.jpg")}
        res_spoof = self.client.post("/upload", data=data_spoof, content_type="multipart/form-data")
        html_spoof = res_spoof.get_data(as_text=True)
        self.assertIn("Invalid document content", html_spoof,
                      "Upload should reject files with spoofed extensions that do not match magic bytes")

        # 2. Oversized file (> 5MB)
        oversized_content = b"\xff\xd8\xff\xe0" + b"\x00" * (5 * 1024 * 1024 + 1024)
        data_over = {"document": (io.BytesIO(oversized_content), "large.jpg")}
        res_over = self.client.post("/upload", data=data_over, content_type="multipart/form-data")
        html_over = res_over.get_data(as_text=True)
        self.assertIn("too large", html_over.lower(), "Upload should reject files larger than 5MB")

        # 3. Path traversal attack: ../../../test.jpg
        legit_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 1024
        data_traversal = {"document": (io.BytesIO(legit_jpeg), "../../etc/traversal.jpg")}
        res_traversal = self.client.post("/upload", data=data_traversal, content_type="multipart/form-data",
                                         follow_redirects=False)
        self.assertEqual(res_traversal.status_code, 302, "Valid content with path traversal should be sanitized and saved safely")

        with self.client.session_transaction() as sess:
            doc_path = sess.get("doc_path", "")
            self.assertNotIn("..", doc_path, "Saved document path must not contain path traversal characters")


if __name__ == "__main__":
    unittest.main()
