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


class TestIndicNameIntegrity(unittest.TestCase):
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

    def test_sanitize_preserves_indic_characters(self):
        """
        Verify that sanitize() does not truncate Indic names at 20 bytes.
        Assamese characters take 3 bytes in UTF-8. Slicing at 20 bytes severed
        names after 6 characters and corrupted multi-byte sequences.
        """
        import app

        assamese_name = "হিমাংশু শর্মা"  # 13 characters, ~39 UTF-8 bytes
        sanitized = app.sanitize(assamese_name)
        self.assertEqual(sanitized, assamese_name,
                         "sanitize() corrupted or truncated the Assamese name")

        # Test long Assamese name up to schema limit (100 chars)
        long_name = "শ্ৰীযুত লক্ষ্মীনাথ বেজবৰুৱা ডাঙৰীয়া - বিশিষ্ট অসমীয়া সাহিত্যিক"
        sanitized_long = app.sanitize(long_name)
        self.assertEqual(sanitized_long, long_name)

    def test_multi_script_pdf_generation_and_inspection(self):
        """
        Verify that acknowledgment PDF generation successfully renders and embeds
        Indic scripts (Assamese, Bengali, Devanagari) with complex conjuncts,
        combining marks, and mixed English/Indic scripts without producing '????'.
        Also verify that /ToUnicode cmap table and FreeSerif font are embedded in PDF.
        """
        import app

        test_scripts = [
            ("ASSAM_01", "হিমাংশু শর্মা"),                         # Assamese standard
            ("ASSAM_02", "লক্ষ্মীনাথ বেজবৰুৱা"),                 # Assamese conjuncts (ক্ষ্ম, বৰুৱা)
            ("BENG_01", "রবীন্দ্রনাথ ঠাকুর"),                      # Bengali conjuncts (ন্দ্ৰ)
            ("DEVA_01", "डॉ. राजेंद्र प्रसाद"),                  # Devanagari conjuncts & nukta (प्र, द्र)
            ("DEVA_02", "सुरेंद्र कुमार मिश्र"),                 # Devanagari conjuncts (श्र)
            ("MIXED_01", "Biren Sarma (বীৰেন শৰ্মা)"),             # Mixed English & Indic
        ]

        cur = self.conn.cursor()
        for app_no, name in test_scripts:
            cur.execute("DELETE FROM applications WHERE application_no = %s", (app_no,))
            cur.execute(
                """INSERT INTO applications 
                   (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
                   VALUES (%s, %s, '9876500055', '1950-01-01', 'Test Village', 'Sonari', '1122334455', 'SBIN0001234', 'PENDING', NOW())
                   RETURNING id""",
                (app_no, name)
            )
            app_id = cur.fetchone()[0]

            # Generate PDF
            pdf_bytes = app.generate_acknowledgment(cur, app_id)
            self.assertGreater(len(pdf_bytes), 1000, f"PDF generation failed for {name}")
            self.assertTrue(pdf_bytes.startswith(b"%PDF"), f"Invalid PDF header for {name}")

            # Verify that /ToUnicode map and FreeSerif font are embedded
            self.assertIn(b"/ToUnicode", pdf_bytes,
                          f"PDF for {name} does not contain /ToUnicode cmap table")
            self.assertIn(b"FreeSerif", pdf_bytes,
                          f"PDF for {name} does not embed FreeSerif font")

            # Cleanup
            cur.execute("DELETE FROM applications WHERE id = %s", (app_id,))

        cur.close()

    def test_database_utf8_storage_and_retrieval(self):
        """
        Verify that complex multi-byte Indic names are stored and retrieved from
        PostgreSQL applications table with 100% exact fidelity.
        """
        cur = self.conn.cursor()
        test_name = "লক্ষ্মীনাথ বেজবৰুৱা ও রবীন্দ্রনাথ ঠাকুর"
        cur.execute("DELETE FROM applications WHERE application_no = 'DB_UTF8_TEST'")
        cur.execute(
            """INSERT INTO applications 
               (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
               VALUES ('DB_UTF8_TEST', %s, '9876500077', '1952-01-01', 'V', 'Sonari', '1', 'SBIN0001', 'PENDING', NOW())
               RETURNING id""",
            (test_name,)
        )
        app_id = cur.fetchone()[0]

        cur.execute("SELECT applicant_name FROM applications WHERE id = %s", (app_id,))
        retrieved_name = cur.fetchone()[0]

        self.assertEqual(retrieved_name, test_name,
                         "Database failed to preserve exact UTF-8 Indic text")

        cur.execute("DELETE FROM applications WHERE id = %s", (app_id,))
        cur.close()


if __name__ == "__main__":
    unittest.main()
