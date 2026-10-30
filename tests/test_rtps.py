# -*- coding: utf-8 -*-
import unittest
import os
import sys
import subprocess
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(current_dir, "..", "scripts")):
    APP_DIR = os.path.abspath(os.path.join(current_dir, ".."))
elif os.path.exists(os.path.join(current_dir, "..", "app", "scripts")):
    APP_DIR = os.path.abspath(os.path.join(current_dir, "..", "app"))
else:
    raise RuntimeError("Cannot locate app directory")


class TestDeemedApproval(unittest.TestCase):
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

    def test_deemed_approval_config_load(self):
        """
        Verify that deemed_approval script references config/app.ini.
        In the unfixed state, it tries to read config/settings.ini which was renamed
        to config/app.ini around mid-March 2026, causing a FileNotFoundError.
        """
        script_path = os.path.join(APP_DIR, "scripts", "deemed_approval.py")
        with open(script_path, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertNotIn("settings.ini", code,
                         "deemed_approval.py still references obsolete config/settings.ini")
        self.assertIn("app.ini", code,
                      "deemed_approval.py must reference config/app.ini")

    def test_sla_boundary_and_audit_execution(self):
        """
        Verify strict 15-day statutory SLA boundary condition:
        - Application submitted 14 days, 23 hours ago must remain PENDING (within SLA).
        - Application submitted 15 days, 1 hour ago must be approved (overdue).
        - Already REJECTED application submitted 30 days ago must remain REJECTED.
        - Audit trail: decided_by must be 'RTPS-AUTO' and decided_at must be set.
        """
        cur = self.conn.cursor()
        now = datetime.now()

        # Clean up any leftover test data
        cur.execute("DELETE FROM applications WHERE application_no IN ('SLA_UNDER_15', 'SLA_OVER_15', 'SLA_REJECTED')")

        # 1. Under SLA (14 days, 23 hours old)
        cur.execute(
            """INSERT INTO applications 
               (application_no, applicant_name, mobile, dob, gender, marital_status,
                village, block, bank_account, ifsc, status, submitted_at)
               VALUES ('SLA_UNDER_15', 'Citizen Within SLA', '9999900014', '1955-01-01', 'Male', 'Married',
                       'Test Village', 'Sonari', '1111222233', 'SBIN0001234', 'PENDING', %s)""",
            (now - timedelta(days=14, hours=23),)
        )

        # 2. Over SLA (15 days, 1 hour old)
        cur.execute(
            """INSERT INTO applications 
               (application_no, applicant_name, mobile, dob, gender, marital_status,
                village, block, bank_account, ifsc, status, submitted_at)
               VALUES ('SLA_OVER_15', 'Citizen Overdue', '9999900015', '1950-01-01', 'Male', 'Married',
                       'Test Village', 'Sonari', '2222333344', 'SBIN0001234', 'PENDING', %s)""",
            (now - timedelta(days=15, hours=1),)
        )

        # 3. Non-pending: Already REJECTED (30 days old)
        cur.execute(
            """INSERT INTO applications 
               (application_no, applicant_name, mobile, dob, gender, marital_status,
                village, block, bank_account, ifsc, status, submitted_at, decided_at, decided_by)
               VALUES ('SLA_REJECTED', 'Citizen Rejected', '9999900030', '1952-01-01', 'Male', 'Married',
                       'Test Village', 'Sonari', '3333444455', 'SBIN0001234', 'REJECTED', %s, %s, 'ADMIN-MANUAL')""",
            (now - timedelta(days=30), now - timedelta(days=20))
        )

        # Execute deemed_approval script
        script_path = os.path.join(APP_DIR, "scripts", "deemed_approval.py")
        res = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")

        # Check results
        cur.execute("SELECT status, decided_by, decided_at FROM applications WHERE application_no = 'SLA_UNDER_15'")
        under_row = cur.fetchone()
        self.assertEqual(under_row[0], "PENDING", "Application under 15 days must remain PENDING")

        cur.execute("SELECT status, decided_by, decided_at FROM applications WHERE application_no = 'SLA_OVER_15'")
        over_row = cur.fetchone()
        self.assertEqual(over_row[0], "DEEMED_APPROVED", "Application over 15 days must be marked DEEMED_APPROVED")
        self.assertEqual(over_row[1], "RTPS-AUTO", "Audit decided_by must be recorded as 'RTPS-AUTO'")
        self.assertIsNotNone(over_row[2], "Audit decided_at timestamp must be set")

        cur.execute("SELECT status, decided_by FROM applications WHERE application_no = 'SLA_REJECTED'")
        rej_row = cur.fetchone()
        self.assertEqual(rej_row[0], "REJECTED", "Non-pending REJECTED applications must NOT be modified")
        self.assertEqual(rej_row[1], "ADMIN-MANUAL")

        # Cleanup
        cur.execute("DELETE FROM applications WHERE application_no IN ('SLA_UNDER_15', 'SLA_OVER_15', 'SLA_REJECTED')")
        cur.close()

    def test_idempotency_of_deemed_approval(self):
        """
        Verify that running deemed_approval multiple times is idempotent:
        A second execution immediately following the first must find 0 pending overdue
        applications and perform 0 duplicate approvals.
        """
        script_path = os.path.join(APP_DIR, "scripts", "deemed_approval.py")
        res1 = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        self.assertEqual(res1.returncode, 0)

        # Run immediately again
        res2 = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        self.assertEqual(res2.returncode, 0)
        self.assertIn("0 applications approved", res2.stdout,
                      "Second execution must be idempotent and find 0 applications to approve")


if __name__ == "__main__":
    unittest.main()
