import os
import tempfile
import unittest
import sqlite3
import io
import json
from datetime import datetime, date
from unittest.mock import patch, MagicMock
from app import app, init_db, DATABASE, get_storage, is_google_configured, SQLiteStorage, GoogleSheetsStorage, parse_month_period, format_month_period
from werkzeug.security import check_password_hash, generate_password_hash

class HighFieldsTrackerTestCase(unittest.TestCase):

    def setUp(self):
        # Configure app for testing
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test_secret_key'

        # Create a temporary database file for SQLite tests
        self.db_fd, self.temp_db_path = tempfile.mkstemp()
        app.config['DATABASE'] = self.temp_db_path

        # Override DATABASE global in app.py
        import app as app_module
        self.original_database = app_module.DATABASE
        app_module.DATABASE = self.temp_db_path

        self.client = app.test_client()

        # Initialize and seed temporary SQLite database
        init_db()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.temp_db_path):
            os.unlink(self.temp_db_path)

        # Restore original database path in app module
        import app as app_module
        app_module.DATABASE = self.original_database

    def test_database_initialization(self):
        """Test that SQLite database is successfully initialized and seeded with default data."""
        conn = sqlite3.connect(self.temp_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check admin user
        cursor.execute("SELECT * FROM admins WHERE email = ?", ("admin",))
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

    def test_login_and_logout_sqlite(self):
        """Test successful and unsuccessful authentication flows in SQLite mode."""
        # 1. Test Admin Login
        response = self.client.post('/login', data={
            'email': 'admin',
            'password': 'admin123'
        }, follow_redirects=True)
        self.assertIn(b'ADMIN DASHBOARD', response.data)

        # 2. Test Logout
        response = self.client.get('/logout', follow_redirects=True)
        self.assertIn(b'LOGIN', response.data)

        # 3. Test Partner Login
        response = self.client.post('/login', data={
            'email': 'partner1',
            'password': 'partner123'
        }, follow_redirects=True)
        self.assertIn(b'WELCOME, PARTNER ONE', response.data)

        # 4. Test Invalid Login
        response = self.client.post('/login', data={
            'email': 'admin',
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

    def test_admin_add_record_calculations_sqlite(self):
        """Test record addition by admin and its associated calculations in SQLite mode."""
        self.login_helper('admin', 'admin123')

        response = self.client.post('/admin/add_record', data={
            'address': '123 High Fields St',
            'month': '2024-08-01',
            'rent_received': '1000.00',
            'receiving_partner': 'Partner One',
            'other_income': '50.00',
            'other_expenses': '30.00'
        }, follow_redirects=True)

        self.assertIn(b'Record successfully added!', response.data)

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

    def test_partner_profile_update_sqlite(self):
        """Test that partners can update their profile emails, passwords, and photos in SQLite mode."""
        self.login_helper('partner1', 'partner123')

        response = self.client.post('/partner/update_profile', data={
            'new_email': 'updated_partner1',
            'current_pwd': 'partner123',
            'new_pwd': 'newpassword123',
            'confirm_pwd': 'newpassword123',
            'photo': (io.BytesIO(b"dummy_image_data"), 'test_avatar.png')
        }, follow_redirects=True)

        self.assertIn(b'Profile updated successfully!', response.data)

        conn = sqlite3.connect(self.temp_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM partners WHERE email = ?", ('partner1',))
        self.assertIsNone(cursor.fetchone())

        cursor.execute("SELECT * FROM partners WHERE email = ?", ('updated_partner1',))
        updated_partner = cursor.fetchone()
        self.assertIsNotNone(updated_partner)
        self.assertEqual(updated_partner['name'], 'Partner One')
        self.assertTrue(check_password_hash(updated_partner['password'], 'newpassword123'))
        self.assertTrue(updated_partner['photo_url'].startswith('/static/uploads/'))

        conn.close()

    def test_csv_export_sqlite(self):
        """Test downloading records as a CSV in SQLite mode."""
        self.login_helper('admin', 'admin123')

        response = self.client.get('/download_csv?house_filter=123+High+Fields+St')
        self.assertEqual(response.mimetype, 'text/csv')
        self.assertIn('attachment; filename=monthly_records.csv', response.headers.get('Content-Disposition', ''))

        csv_data = response.data.decode('utf-8')
        lines = csv_data.splitlines()
        self.assertGreater(len(lines), 1)

        headers = lines[0].split(',')
        self.assertEqual(headers[0], 'Record_ID')
        self.assertEqual(headers[1], 'Address')
        self.assertEqual(headers[2], 'Month')

        self.assertIn('123 High Fields St', lines[1])

    def test_parse_month_period(self):
        """Test reading the overview month-range filter from query params."""
        self.assertEqual(parse_month_period({}), ('', ''))
        self.assertEqual(parse_month_period({'from_month': '1', 'from_year': '2026', 'to_month': '3', 'to_year': '2027'}),
                         ('2026-01', '2027-03'))
        # Reversed range is swapped
        self.assertEqual(parse_month_period({'from_month': '3', 'from_year': '2027', 'to_month': '1', 'to_year': '2026'}),
                         ('2026-01', '2027-03'))
        # Incomplete or invalid values are ignored
        self.assertEqual(parse_month_period({'from_month': '13', 'from_year': '2026', 'to_year': '2027'}), ('', ''))
        self.assertEqual(format_month_period('2026-01', '2027-03'), 'Jan 2026 – Mar 2027')
        self.assertEqual(format_month_period('', ''), 'All time')

    def test_admin_overview_houses_and_period_filter_sqlite(self):
        """Test the overview Houses table and the All Time month-range filter in SQLite mode."""
        self.login_helper('admin', 'admin123')

        response = self.client.get('/admin/dashboard')
        # All houses are listed, including the inactive one
        self.assertIn(b'101 Pine St', response.data)
        self.assertIn(b'Inactive', response.data)
        # Seeded records are from 2024, so active houses have nothing recorded this month
        self.assertIn(b'Not recorded', response.data)
        # Unfiltered all-time rent: 1200 + 900 + 1500
        self.assertIn(b'$3,600.00', response.data)

        response = self.client.get('/admin/dashboard?from_month=7&from_year=2024&to_month=7&to_year=2024')
        self.assertIn('Jul 2024 – Jul 2024'.encode('utf-8'), response.data)
        # Only the July 2024 record (1500) counts towards the total now
        self.assertNotIn(b'$3,600.00', response.data)

    def test_admin_this_month_expenses_and_net_profit_sqlite(self):
        """Test that This Month shows maintenance, IT subscription and net profit for the current month."""
        self.login_helper('admin', 'admin123')
        this_month = date.today().replace(day=1).strftime('%Y-%m-%d')

        self.client.post('/admin/add_record', data={
            'address': '456 Valley Rd',
            'month': this_month,
            'rent_received': '1000.00',
            'receiving_partner': 'Partner Two',
            'other_income': '0.00',
            'other_expenses': '0.00'
        }, follow_redirects=True)

        response = self.client.get('/admin/dashboard')
        self.assertIn(b'Net Profit', response.data)
        # 1000 rent - 200 maintenance (20%) - 20 IT = 780 net profit
        self.assertIn(b'$1,000.00 rent', response.data)
        self.assertIn(b'$200.00 maintenance', response.data)
        self.assertIn(b'$20.00 IT', response.data)
        self.assertIn(b'$780.00', response.data)

    def test_partner_this_month_sqlite(self):
        """Test that the partner dashboard shows the This Month section, including their own received rent."""
        self.login_helper('admin', 'admin123')
        this_month = date.today().replace(day=1).strftime('%Y-%m-%d')
        self.client.post('/admin/add_record', data={
            'address': '456 Valley Rd',
            'month': this_month,
            'rent_received': '1000.00',
            'receiving_partner': 'Partner One',
            'other_income': '0.00',
            'other_expenses': '0.00'
        }, follow_redirects=True)
        self.client.get('/logout')

        self.login_helper('partner1', 'partner123')
        response = self.client.get('/partner/dashboard')
        self.assertIn(b'This Month', response.data)
        self.assertIn(b'My Received Rent', response.data)
        # 1000 rent - 200 maintenance - 20 IT = 780, received by Partner One
        self.assertIn(b'$200.00 maintenance', response.data)
        self.assertIn(b'$780.00', response.data)

        # Filters on the All Time section don't change This Month
        response = self.client.get('/partner/dashboard?house_filter=123+High+Fields+St')
        self.assertIn(b'$200.00 maintenance', response.data)

    # ---------- GOOGLE SHEETS MODE TESTS (MOCKED) ----------

    @patch('app.is_google_configured', return_value=True)
    @patch('app.GoogleSheetsStorage')
    def test_google_sheets_login_and_operations(self, mock_sheets_storage_class, mock_is_configured):
        """Test login, dashboard views, and calculations when configured to use Google Sheets."""
        mock_storage = MagicMock()
        mock_sheets_storage_class.return_value = mock_storage

        mock_storage.get_admins.return_value = [
            {'email': 'sheet_admin', 'password': generate_password_hash('sheet_admin123')}
        ]
        mock_storage.get_partners.return_value = [
            {'email': 'sheet_partner1', 'password': generate_password_hash('sheet_partner123'), 'name': 'Sheet Partner One', 'photo_url': ''}
        ]
        mock_storage.get_active_houses.return_value = [
            {'address': 'Sheet House 1', 'fixed_rent': 1000.0, 'active': 1}
        ]
        mock_storage.get_house_fixed_rent.return_value = 1000.0

        mock_storage.get_monthly_records.return_value = [
            {
                'record_id': 'REC-2024-06-sheet-house-1',
                'address': 'Sheet House 1',
                'month': '2024-06-01',
                'rent_received': 1000.0,
                'receiving_partner': 'Sheet Partner One',
                'maintenance': 200.0,
                'it_subscription': 20.0,
                'other_income': 0.0,
                'other_expenses': 0.0,
                'profit': 780.0,
                'rent_owing': 0.0,
                'timestamp': '2024-06-01 12:00'
            }
        ]

        # 1. Test Admin Login using mocked Google Sheets config
        response = self.client.post('/login', data={
            'email': 'sheet_admin',
            'password': 'sheet_admin123'
        }, follow_redirects=True)
        self.assertIn(b'ADMIN DASHBOARD', response.data)
        mock_storage.get_admins.assert_called_once()

        # 2. Test Admin Dashboard rendering
        response = self.client.get('/admin/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Sheet House 1', response.data)
        self.assertIn(b'Sheet Partner One', response.data)

        # 3. Test Add Record Calculations in Google Sheets Mode
        response = self.client.post('/admin/add_record', data={
            'address': 'Sheet House 1',
            'month': '2024-08-01',
            'rent_received': '800.00',
            'receiving_partner': 'Sheet Partner One',
            'other_income': '10.00',
            'other_expenses': '5.00'
        }, follow_redirects=True)

        self.assertIn(b'Record successfully added!', response.data)

        mock_storage.add_monthly_record.assert_called_once()
        args, kwargs = mock_storage.add_monthly_record.call_args
        self.assertEqual(args[1], 'Sheet House 1')
        self.assertEqual(args[2], '2024-08-01')
        self.assertEqual(args[3], 800.0) # rent_received
        self.assertEqual(args[4], 'Sheet Partner One') # receiving_partner
        self.assertEqual(args[5], 160.0) # maintenance
        self.assertEqual(args[6], 20.0) # it_subscription
        self.assertEqual(args[7], 10.0) # other_income
        self.assertEqual(args[8], 5.0) # other_expenses
        self.assertEqual(args[9], 625.0) # profit
        self.assertEqual(args[10], 200.0) # rent_owing

    @patch('app.is_google_configured', return_value=True)
    @patch('app.GoogleSheetsStorage')
    def test_partner_profile_update_google_sheets(self, mock_sheets_storage_class, mock_is_configured):
        """Test partner profile photo upload in Google Sheets mode."""
        mock_storage = MagicMock()
        mock_sheets_storage_class.return_value = mock_storage
        mock_storage.get_partners.return_value = [
            {'email': 'sheet_partner1', 'password': generate_password_hash('sheet_partner123'), 'name': 'Sheet Partner One', 'photo_url': ''}
        ]

        # Login as partner
        self.login_helper('sheet_partner1', 'sheet_partner123')

        response = self.client.post('/partner/update_profile', data={
            'current_pwd': 'sheet_partner123',
            'photo': (io.BytesIO(b"dummy_photo_data"), 'avatar.png')
        }, follow_redirects=True)

        self.assertIn(b'Profile updated successfully!', response.data)
        mock_storage.update_partner_profile.assert_called_once()

if __name__ == '__main__':
    unittest.main()
