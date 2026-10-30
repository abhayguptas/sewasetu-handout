# -*- coding: utf-8 -*-
import unittest
import os
import sys
import io
from datetime import datetime

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


class TestCitizenJourneyAndCorrection(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        import app
        self.app = app.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_upload_accepts_jpg(self):
        """
        Verify that uploading a genuine JPG document under 5MB succeeds.
        """
        with self.client.session_transaction() as sess:
            sess["verified_mobile"] = "9876543210"

        # Valid JPEG header
        fake_jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 200000  # ~200 KB
        data = {"document": (io.BytesIO(fake_jpg), "age_proof.jpg")}
        res = self.client.post("/upload", data=data, content_type="multipart/form-data",
                               follow_redirects=False)

        self.assertEqual(res.status_code, 302)
        self.assertIn("/declaration", res.headers.get("Location", ""))

    def test_upload_accepts_pdf_above_100kb(self):
        """
        Verify that uploading a genuine PDF between 100KB and 5MB succeeds.
        """
        with self.client.session_transaction() as sess:
            sess["verified_mobile"] = "9876543211"

        # Valid PDF header
        fake_pdf = b"%PDF-1.4\n" + b"0" * 300000  # ~300 KB
        data = {"document": (io.BytesIO(fake_pdf), "aadhaar.pdf")}
        res = self.client.post("/upload", data=data, content_type="multipart/form-data",
                               follow_redirects=False)

        self.assertEqual(res.status_code, 302)
        self.assertIn("/declaration", res.headers.get("Location", ""))

    def test_upload_friendly_error_messages(self):
        """
        Verify that invalid uploads show descriptive error messages instead of ERR_VAL_47.
        """
        with self.client.session_transaction() as sess:
            sess["verified_mobile"] = "9876543212"

        data = {"document": (io.BytesIO(b"dummy text file"), "notes.txt")}
        res = self.client.post("/upload", data=data, content_type="multipart/form-data")
        html = res.get_data(as_text=True)

        self.assertNotIn("ERR_VAL_47", html)
        self.assertIn("Invalid format", html)

    def test_widow_submission_without_husband_employer(self):
        """
        Verify that a widowed applicant can submit without filling in a deceased
        husband's current employer.
        """
        cur = self.conn.cursor()
        cur.execute("DELETE FROM applications WHERE mobile = '9876500003'")
        cur.execute("DELETE FROM portal_users WHERE mobile = '9876500003'")
        cur.close()

        with self.client.session_transaction() as sess:
            sess["verified_mobile"] = "9876500003"
            sess["form_data"] = {
                "applicant_name": "Widow Beneficiary",
                "dob": "15/08/1955",
                "gender": "Female",
                "marital_status": "Widowed",
                "husband_name": "Late Husband Name",
                "husband_employer": "",  # Empty
                "village": "Namti Gaon",
                "block": "Namti",
                "bank_account": "1122334455",
                "ifsc": "SBIN0001234",
            }
            sess["doc_path"] = "/var/sewasetu/uploads/fake.pdf"

        # Submit declaration
        res = self.client.post("/declaration", data={"agree": "on"}, follow_redirects=True)
        html = res.get_data(as_text=True)

        self.assertNotIn("husband's employer", html.lower())
        self.assertIn("Application Submitted", html)

    def test_audited_pending_application_correction(self):
        """
        Verify that an applicant can correct their pending application:
        - Safe fields (village, bank_account, ifsc) are updated.
        - Changes are logged in application_edits audit table with old and new values.
        - Original submitted_at, mobile, dob, and status are strictly preserved.
        - Processed applications (APPROVED/REJECTED) cannot be edited.
        """
        cur = self.conn.cursor()
        now = datetime.now()
        cur.execute("DELETE FROM applications WHERE application_no = 'AUDIT_EDIT_01'")
        cur.execute("DELETE FROM portal_users WHERE mobile = '9876500099'")

        cur.execute(
            """INSERT INTO applications 
               (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
               VALUES ('AUDIT_EDIT_01', 'Original Name', '9876500099', '1952-05-10', 'Old Village', 'Sonari',
                       '1111222233', 'SBIN0001111', 'PENDING', %s)
               RETURNING id""",
            (now,)
        )
        app_id = cur.fetchone()[0]

        # Log in as applicant
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["portal_mobile"] = "9876500099"

        # Perform edit
        res = self.client.post(
            f"/application/{app_id}/edit",
            data={
                "applicant_name": "Corrected Name",
                "village": "New Ward 5",
                "block": "Namti",
                "bank_account": "9999888877",
                "ifsc": "SBIN0002222",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)

        # Verify applications table values
        cur.execute("SELECT applicant_name, village, bank_account, submitted_at, mobile, dob, status FROM applications WHERE id = %s", (app_id,))
        app_row = cur.fetchone()
        self.assertEqual(app_row[0], "Corrected Name")
        self.assertEqual(app_row[1], "New Ward 5")
        self.assertEqual(app_row[2], "9999888877")
        self.assertEqual(app_row[3], now, "Original submitted_at timestamp MUST be preserved")
        self.assertEqual(app_row[4], "9876500099", "Mobile number MUST NOT be modified")
        self.assertEqual(str(app_row[5]), "1952-05-10", "Date of birth MUST NOT be modified")
        self.assertEqual(app_row[6], "PENDING", "Status MUST remain PENDING")

        # Verify application_edits audit log
        cur.execute("SELECT field_name, old_value, new_value, edited_by FROM application_edits WHERE application_id = %s ORDER BY id ASC", (app_id,))
        edits = cur.fetchall()
        edit_fields = {e[0]: (e[1], e[2], e[3]) for e in edits}

        self.assertIn("applicant_name", edit_fields)
        self.assertEqual(edit_fields["applicant_name"], ("Original Name", "Corrected Name", "9876500099"))
        self.assertIn("village", edit_fields)
        self.assertEqual(edit_fields["village"], ("Old Village", "New Ward 5", "9876500099"))
        self.assertIn("bank_account", edit_fields)
        self.assertEqual(edit_fields["bank_account"], ("1111222233", "9999888877", "9876500099"))

        # Verify processed application cannot be edited
        cur.execute("UPDATE applications SET status = 'APPROVED' WHERE id = %s", (app_id,))
        res_approved = self.client.post(
            f"/application/{app_id}/edit",
            data={"applicant_name": "Tamper Name", "village": "V", "block": "B", "bank_account": "1", "ifsc": "SBIN0001"},
            follow_redirects=True,
        )
        html = res_approved.get_data(as_text=True)
        self.assertIn("already been processed and cannot be edited", html)

        # Cleanup
        cur.execute("DELETE FROM application_edits WHERE application_id = %s", (app_id,))
        cur.execute("DELETE FROM applications WHERE id = %s", (app_id,))
        cur.close()


if __name__ == "__main__":
    unittest.main()
