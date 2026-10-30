# -*- coding: utf-8 -*-
import unittest
import os
import sys
import time

current_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(current_dir, "..", "app.py")):
    APP_DIR = os.path.abspath(os.path.join(current_dir, ".."))
elif os.path.exists(os.path.join(current_dir, "..", "app", "app.py")):
    APP_DIR = os.path.abspath(os.path.join(current_dir, "..", "app"))
else:
    raise RuntimeError("Cannot locate app directory")

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


class TestSubmissionPerformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        import configparser

        config = configparser.ConfigParser()
        config.read(os.path.join(APP_DIR, "config", "app.ini"))

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

    def test_no_artificial_loop_in_pdf_generation(self):
        """
        Verify that generate_acknowledgment does not contain the artificial
        2000-iteration vector border loop that pegged CPU and caused Gunicorn
        worker timeouts during month-end traffic rushes.
        """
        app_path = os.path.join(APP_DIR, "app.py")
        with open(app_path, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertNotIn("range(0, 2000)", code,
                         "Artificial 2000-iteration PDF border loop found in app.py")

    def test_pdf_generation_latency(self):
        """
        Verify that acknowledgment PDF generation takes under 500ms.
        With the artificial 2000-iteration loop, it took > 2,500ms per request, starving workers.
        """
        import app
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM applications LIMIT 1")
        app_id = cur.fetchone()[0]

        start_time = time.time()
        pdf_bytes = app.generate_acknowledgment(cur, app_id)
        duration = time.time() - start_time
        cur.close()

        self.assertGreater(len(pdf_bytes), 1000)
        self.assertLess(duration, 0.5,
                        f"PDF generation took {duration:.3f}s; expected < 0.5s (down from > 2.5s with loop)")

    def test_database_indexes_on_applications(self):
        """
        Verify that targeted database indexes exist on applications(mobile) and
        applications(status, submitted_at) to avoid full sequential scans of 100,000 rows.
        """
        cur = self.conn.cursor()
        cur.execute(
            """SELECT indexname FROM pg_indexes 
               WHERE tablename = 'applications'"""
        )
        indexes = [row[0] for row in cur.fetchall()]
        cur.close()

        self.assertIn("idx_applications_mobile", indexes,
                      "Index on applications(mobile) is missing")
        self.assertIn("idx_applications_status_submitted", indexes,
                      "Composite index on applications(status, submitted_at) is missing")

    def test_query_execution_plan_uses_index(self):
        """
        Verify via EXPLAIN ANALYZE that querying applications by mobile uses
        an Index Scan on idx_applications_mobile rather than a 100,000-row Seq Scan.
        """
        cur = self.conn.cursor()
        cur.execute("EXPLAIN SELECT id FROM applications WHERE mobile = '9876543210'")
        plan_lines = [r[0] for r in cur.fetchall()]
        plan_text = " ".join(plan_lines)
        cur.close()

        self.assertTrue("Index Scan" in plan_text or "Bitmap Index Scan" in plan_text,
                        f"Expected Index Scan in plan, got:\n{plan_text}")


if __name__ == "__main__":
    unittest.main()
