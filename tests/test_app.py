import os
import tempfile
import unittest
import sqlite3
import io
from datetime import datetime, date
from app import app, get_db, init_db, DATABASE
from werkzeug.security import check_password_hash

class HighFieldsTrackerTestCase(unittest.TestCase):

    def setUp(self):
        # Configure app for testing
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test_secret_key'

        # Create a temporary database file
        self.db_fd, self.temp_db_path = tempfile.mkstemp()
        app.config['DATABASE'] = self.temp_db_path

        # Override DATABASE global in app.py
        import app as app_module
        self.original_database = app_module.DATABASE
        app_module.DATABASE = self.temp_db_path

        self.client = app.test_client()

        # Initialize and seed temporary database
        init_db()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.temp_db_path):
            os.unlink(self.temp_db_path)

        # Restore original database path in app module
        import app as app_module
        app_module.DATABASE = self.original_database

    def test_database_initialization(self):
        """Test that database is successfully initialized and seeded with default data."""
        conn = sqlite3.connect(self.temp_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check admin user
        cursor.execute("SELECT * FROM admins WHERE email = ?", ("admin@highfields.com",))
        admin = cursor.fetchone()
        self.assertIsNotNone(admin)

        # Check partners
        cursor.execute("SELECT * FROM partners ORDER BY name")
        partners = cursor.fetchall()
        self.assertEqual(len(partners), 2)
        self.assertEqual(partners[0]['name'], "Partner One")
        self.assertEqual(partners[1]['name'], "Partner Two")

        # Check active houses
        cursor.execute("SELECT * FROM houses WHERE active = 1")
        active_houses = cursor.fetchall()
        self.assertEqual(len(active_houses), 3)

        # Check seeded records
        cursor.execute("SELECT * FROM monthly_records")
        records = cursor.fetchall()
        self.assertEqual(len(records), 3)

        conn.close()

    def test_login_and_logout(self):
        """Test successful and unsuccessful authentication flows."""
        # 1. Test Admin Login
        response = self.client.post('/login', data={
            'email': 'admin@highfields.com',
            'password': 'admin123'
        }, follow_redirects=True)
        self.assertIn(b'ADMIN DASHBOARD', response.data)

        # 2. Test Logout
        response = self.client.get('/logout', follow_redirects=True)
        self.assertIn(b'LOGIN', response.data)

        # 3. Test Partner Login
        response = self.client.post('/login', data={
            'email': 'partner1@highfields.com',
            'password': 'partner123'
        }, follow_redirects=True)
        self.assertIn(b'WELCOME, PARTNER ONE', response.data)

        # 4. Test Invalid Login
        response = self.client.post('/login', data={
            'email': 'admin@highfields.com',
            'password': 'wrong_password'
        }, follow_redirects=True)
        self.assertIn(b'Invalid email or password.', response.data)

    def test_unauthorized_dashboard_access(self):
        """Test that unauthenticated users are redirected to login."""
        response = self.client.get('/admin/dashboard', follow_redirects=True)
        self.assertIn(b'LOGIN', response.data)

        response = self.client.get('/partner/dashboard', follow_redirects=True)
        self.assertIn(b'LOGIN', response.data)

    def login_helper(self, email, password):
        return self.client.post('/login', data={
            'email': email,
            'password': password
        }, follow_redirects=True)

    def test_admin_add_record_calculations(self):
        """Test record addition by admin and its associated calculations."""
        # Log in as admin first
        self.login_helper('admin@highfields.com', 'admin123')

        # Add a record for '123 High Fields St' (Fixed rent is 1200)
        # Rent received: 1000 (meaning rent owing should be 200)
        # Other income: 50.00
        # Other expenses: 30.00
        # Maintenance: 1000 * 20% = 200
        # IT sub: 20
        # Expected profit: 1000 + 50 - 200 - 20 - 30 = 800

        response = self.client.post('/admin/add_record', data={
            'address': '123 High Fields St',
            'month': '2024-08-01',
            'rent_received': '1000.00',
            'receiving_partner': 'Partner One',
            'other_income': '50.00',
            'other_expenses': '30.00'
        }, follow_redirects=True)

        self.assertIn(b'Record successfully added!', response.data)

        # Query database to assert values
        conn = sqlite3.connect(self.temp_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monthly_records WHERE month = '2024-08-01'")
        record = cursor.fetchone()

        self.assertIsNotNone(record)
        self.assertEqual(record['address'], '123 High Fields St')
        self.assertEqual(record['rent_received'], 1000.00)
        self.assertEqual(record['receiving_partner'], 'Partner One')
        self.assertEqual(record['maintenance'], 200.00)
        self.assertEqual(record['it_subscription'], 20.00)
        self.assertEqual(record['other_income'], 50.00)
        self.assertEqual(record['other_expenses'], 30.00)
        self.assertEqual(record['profit'], 800.00)
        self.assertEqual(record['rent_owing'], 200.00)

        conn.close()

    def test_partner_profile_update(self):
        """Test that partners can update their profile emails, passwords, and photos."""
        # Log in as partner
        self.login_helper('partner1@highfields.com', 'partner123')

        # Update email and password
        response = self.client.post('/partner/update_profile', data={
            'new_email': 'updated_partner1@highfields.com',
            'current_pwd': 'partner123',
            'new_pwd': 'newpassword123',
            'confirm_pwd': 'newpassword123',
            'photo': (io.BytesIO(b"dummy_image_data"), 'test_avatar.png')
        }, follow_redirects=True)

        self.assertIn(b'Profile updated successfully!', response.data)

        # Verify in database
        conn = sqlite3.connect(self.temp_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Old email should be updated
        cursor.execute("SELECT * FROM partners WHERE email = ?", ('partner1@highfields.com',))
        self.assertIsNone(cursor.fetchone())

        # New email should exist
        cursor.execute("SELECT * FROM partners WHERE email = ?", ('updated_partner1@highfields.com',))
        updated_partner = cursor.fetchone()
        self.assertIsNotNone(updated_partner)
        self.assertEqual(updated_partner['name'], 'Partner One')
        self.assertTrue(check_password_hash(updated_partner['password'], 'newpassword123'))
        self.assertTrue(updated_partner['photo_url'].startswith('/static/uploads/'))

        conn.close()

    def test_csv_export(self):
        """Test downloading records as a CSV."""
        # Log in as admin
        self.login_helper('admin@highfields.com', 'admin123')

        # Request CSV
        response = self.client.get('/download_csv?house_filter=123+High+Fields+St')
        self.assertEqual(response.mimetype, 'text/csv')
        self.assertIn('attachment; filename=monthly_records.csv', response.headers.get('Content-Disposition', ''))

        # Validate content structure
        csv_data = response.data.decode('utf-8')
        lines = csv_data.splitlines()
        self.assertGreater(len(lines), 1) # Header + at least 1 record

        # Header assertions
        headers = lines[0].split(',')
        self.assertEqual(headers[0], 'Record_ID')
        self.assertEqual(headers[1], 'Address')
        self.assertEqual(headers[2], 'Month')

        # Row assertion (contains the matching address and not the others)
        self.assertIn('123 High Fields St', lines[1])
        for line in lines[2:]:
            self.assertNotIn('456 Valley Rd', line)

if __name__ == '__main__':
    unittest.main()
