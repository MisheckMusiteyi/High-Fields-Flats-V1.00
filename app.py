import os
import sqlite3
import hashlib
import json
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, Response
import io
import csv
import pandas as pd
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# For Google Sheets Integration
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import tempfile

app = Flask(__name__)
app.secret_key = "high_fields_flats_super_secret_key"
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DATABASE = 'high_fields.db'

# ---------- CONFIG & SERVICE ACCOUNT SETUP ----------
def get_google_config():
    """Reads Google service account config from environment variables or secrets.json if present."""
    # Try environment variable first
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")

    # Try loading from secrets.json if env vars are not set
    if not sa_json or not sheet_id:
        try:
            if os.path.exists("secrets.json"):
                with open("secrets.json", "r") as f:
                    secrets = json.load(f)
                    sa_json = json.dumps(secrets.get("google", {}).get("service_account_json", {}))
                    sheet_id = secrets.get("google", {}).get("sheet_id", "")
        except Exception:
            pass

    return sa_json, sheet_id

def is_google_configured():
    sa_json, sheet_id = get_google_config()
    return bool(sa_json and sheet_id)

# ---------- HELPERS ----------
def to_float_clean(val):
    """Safely converts any value (including formatted currency strings like $1,200) to float."""
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    import re
    cleaned = re.sub(r"[^\d.\-]", "", str(val))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0

def check_password(stored_hash, raw_pwd):
    """Compares password against stored hash. Supports clear-text, legacy SHA256, and secure Werkzeug hashes."""
    if stored_hash is None or stored_hash == "":
        return False

    stored_str = str(stored_hash).strip()
    raw_pwd_str = str(raw_pwd).strip()

    # 1. Try clear-text match (if user edits Google Sheet directly with plain text/numbers)
    if stored_str == raw_pwd_str:
        return True

    # 2. Try Werkzeug secure hash match
    if any(stored_str.startswith(prefix) for prefix in ["pbkdf2:", "scrypt:", "bcrypt:", "argon2:"]):
        try:
            return check_password_hash(stored_str, raw_pwd_str)
        except Exception:
            pass

    # 3. Try legacy SHA256 hash match
    try:
        sha256_hash = hashlib.sha256(raw_pwd_str.encode()).hexdigest()
        if sha256_hash == stored_str:
            return True
    except Exception:
        pass

    return False

# ---------- DRIVER INTERFACE ----------
class SQLiteStorage:
    @staticmethod
    def get_db():
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        return conn

    def get_admins(self):
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins")
        admins = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return admins

    def get_partners(self):
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM partners")
        partners = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return partners

    def get_active_houses(self):
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM houses WHERE active = 1")
        houses = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return houses

    def get_house_fixed_rent(self, address):
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT fixed_rent FROM houses WHERE address = ?", (address,))
        row = cursor.fetchone()
        conn.close()
        return row['fixed_rent'] if row else 0.0

    def get_monthly_records(self, house_filter=None, partner_filter=None, start_date=None, end_date=None):
        conn = self.get_db()
        cursor = conn.cursor()
        query = "SELECT * FROM monthly_records WHERE 1=1"
        params = []
        if house_filter:
            query += " AND address = ?"
            params.append(house_filter)
        if partner_filter:
            query += " AND receiving_partner = ?"
            params.append(partner_filter)
        if start_date:
            query += " AND month >= ?"
            params.append(start_date)
        if end_date:
            query += " AND month <= ?"
            params.append(end_date)
        query += " ORDER BY month DESC"
        cursor.execute(query, params)
        records = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return records

    def add_monthly_record(self, record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp):
        conn = self.get_db()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO monthly_records (record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp))
        conn.commit()
        conn.close()

    def update_partner_profile(self, old_email, updates):
        conn = self.get_db()
        cursor = conn.cursor()
        set_clauses = []
        params = []
        for col, val in updates.items():
            set_clauses.append(f"{col} = ?")
            params.append(val)
        params.append(old_email)
        query = f"UPDATE partners SET {', '.join(set_clauses)} WHERE email = ?"
        cursor.execute(query, params)
        conn.commit()
        conn.close()


class GoogleSheetsStorage:
    def __init__(self):
        self.sa_json, self.sheet_id = get_google_config()
        self.credentials_dict = json.loads(self.sa_json)
        self.scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    def get_gspread_client(self):
        creds = ServiceAccountCredentials.from_json_keyfile_dict(self.credentials_dict, self.scope)
        return gspread.authorize(creds)

    def load_sheet(self, tab_name):
        gc = self.get_gspread_client()
        sh = gc.open_by_key(self.sheet_id)
        worksheet = sh.worksheet(tab_name)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data) if data else pd.DataFrame()
        if not df.empty:
            df.columns = df.columns.astype(str).str.strip()
        return df

    def get_admins(self):
        df = self.load_sheet("Admin Logins")
        if df.empty:
            return []
        records = []
        for _, row in df.iterrows():
            records.append({
                'email': str(row.get('Email', '')).strip(),
                'password': str(row.get('Password', '')).strip()
            })
        return records

    def get_partners(self):
        df = self.load_sheet("Partners")
        if df.empty:
            return []
        records = []
        for _, row in df.iterrows():
            records.append({
                'email': str(row.get('Email', '')).strip(),
                'password': str(row.get('Password', '')).strip(),
                'name': str(row.get('Name', '')).strip(),
                'photo_url': str(row.get('Photo_URL', '')).strip() if pd.notna(row.get('Photo_URL')) else ""
            })
        return records

    def get_active_houses(self):
        df = self.load_sheet("Houses")
        if df.empty:
            return []
        records = []
        for _, row in df.iterrows():
            active_val = str(row.get('Active', '')).strip().lower()
            if active_val in ['true', 'yes', '1']:
                fixed_rent = to_float_clean(row.get('Fixed_Rent', 0.0))
                records.append({
                    'address': str(row.get('Address', '')).strip(),
                    'fixed_rent': fixed_rent,
                    'active': 1
                })
        return records

    def get_house_fixed_rent(self, address):
        houses = self.get_active_houses()
        for h in houses:
            if h['address'] == address:
                return h['fixed_rent']
        # Check all houses including inactive just in case
        df = self.load_sheet("Houses")
        if not df.empty:
            match = df[df["Address"].astype(str).str.strip() == address]
            if not match.empty:
                return to_float_clean(match.iloc[0]["Fixed_Rent"])
        return 0.0

    def get_monthly_records(self, house_filter=None, partner_filter=None, start_date=None, end_date=None):
        df = self.load_sheet("MonthlyRecords")
        if df.empty:
            return []

        records = []
        for _, row in df.iterrows():
            rec = {
                'record_id': str(row.get('Record_ID', '')).strip(),
                'address': str(row.get('Address', '')).strip(),
                'month': str(row.get('Month', '')).strip(),
                'rent_received': to_float_clean(row.get('Rent_Received', 0.0)),
                'receiving_partner': str(row.get('Receiving_Partner', '')).strip(),
                'maintenance': to_float_clean(row.get('Maintenance', 0.0)),
                'it_subscription': to_float_clean(row.get('IT_Subscription', 0.0)),
                'other_income': to_float_clean(row.get('Other_Income', 0.0)),
                'other_expenses': to_float_clean(row.get('Other_Expenses', 0.0)),
                'profit': to_float_clean(row.get('Profit', 0.0)),
                'rent_owing': to_float_clean(row.get('Rent_Owing', 0.0)),
                'timestamp': str(row.get('Timestamp', '')).strip()
            }
            records.append(rec)

        if house_filter:
            records = [r for r in records if r['address'] == house_filter]
        if partner_filter:
            records = [r for r in records if r['receiving_partner'] == partner_filter]
        if start_date:
            records = [r for r in records if r['month'] >= start_date]
        if end_date:
            records = [r for r in records if r['month'] <= end_date]

        records.sort(key=lambda r: r['month'], reverse=True)
        return records

    def add_monthly_record(self, record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp):
        gc = self.get_gspread_client()
        sh = gc.open_by_key(self.sheet_id)
        worksheet = sh.worksheet("MonthlyRecords")
        headers = [h.strip() for h in worksheet.row_values(1)]

        row_dict = {
            "Record_ID": record_id,
            "Address": address,
            "Month": month,
            "Rent_Received": rent_received,
            "Receiving_Partner": receiving_partner,
            "Maintenance": maintenance,
            "IT_Subscription": it_subscription,
            "Other_Income": other_income,
            "Other_Expenses": other_expenses,
            "Profit": profit,
            "Rent_Owing": rent_owing,
            "Timestamp": timestamp
        }

        row_to_append = [row_dict.get(col, "") for col in headers]
        worksheet.append_row(row_to_append, value_input_option="RAW")

    def get_drive_service(self):
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(
            self.credentials_dict,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=credentials)

    def update_partner_profile(self, old_email, updates):
        gc = self.get_gspread_client()
        sh = gc.open_by_key(self.sheet_id)
        worksheet = sh.worksheet("Partners")

        df = self.load_sheet("Partners")
        match_idx = df.index[df["Email"].astype(str).str.strip() == old_email].tolist()
        if not match_idx:
            raise Exception("User not found in Google Sheets")

        row_num = match_idx[0] + 2
        headers = [h.strip() for h in worksheet.row_values(1)]

        col_mapping = {
            'email': 'Email',
            'password': 'Password',
            'photo_url': 'Photo_URL'
        }

        for sql_col, val in updates.items():
            sheet_col_name = col_mapping.get(sql_col)
            if sheet_col_name and sheet_col_name in headers:
                col_idx = headers.index(sheet_col_name) + 1
                worksheet.update_cell(row_num, col_idx, val)


def get_storage():
    """Returns the configured storage driver (Google Sheets if configured, fallback to SQLite)."""
    if is_google_configured():
        return GoogleSheetsStorage()
    return SQLiteStorage()

# ---------- SEEDING SQLITE FOR LOCAL FALLBACK ----------
def init_db():
    """Initializes the SQLite database and seeds it with default data if empty."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        email TEXT PRIMARY KEY,
        password TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS partners (
        email TEXT PRIMARY KEY,
        password TEXT,
        name TEXT,
        photo_url TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS houses (
        address TEXT PRIMARY KEY,
        fixed_rent REAL,
        active INTEGER
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS monthly_records (
        record_id TEXT PRIMARY KEY,
        address TEXT,
        month TEXT,
        rent_received REAL,
        receiving_partner TEXT,
        maintenance REAL,
        it_subscription REAL,
        other_income REAL,
        other_expenses REAL,
        profit REAL,
        rent_owing REAL,
        timestamp TEXT
    )
    """)
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM admins")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO admins (email, password) VALUES (?, ?)",
                       ("admin@highfields.com", generate_password_hash("admin123")))
        cursor.execute("INSERT INTO partners (email, password, name, photo_url) VALUES (?, ?, ?, ?)",
                       ("partner1@highfields.com", generate_password_hash("partner123"), "Partner One", ""))
        cursor.execute("INSERT INTO partners (email, password, name, photo_url) VALUES (?, ?, ?, ?)",
                       ("partner2@highfields.com", generate_password_hash("partner223"), "Partner Two", ""))
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("123 High Fields St", 1200.0, 1))
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("456 Valley Rd", 1000.0, 1))
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("789 Hilltop Ln", 1500.0, 1))
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("101 Pine St", 800.0, 0))

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute("""
        INSERT INTO monthly_records (record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("REC-2024-06-123-high-fields-st-1200", "123 High Fields St", "2024-06-01", 1200.0, "Partner One", 240.0, 20.0, 0.0, 0.0, 940.0, 0.0, now_str))
        cursor.execute("""
        INSERT INTO monthly_records (record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("REC-2024-06-456-valley-rd-1200", "456 Valley Rd", "2024-06-01", 900.0, "Partner Two", 180.0, 20.0, 0.0, 50.0, 650.0, 100.0, now_str))
        cursor.execute("""
        INSERT INTO monthly_records (record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("REC-2024-07-789-hilltop-ln-1200", "789 Hilltop Ln", "2024-07-01", 1500.0, "Partner One", 300.0, 20.0, 100.0, 0.0, 1280.0, 0.0, now_str))
        conn.commit()
    conn.close()

# ---------- MIDDLEWARE / DECORATORS ----------
def login_required(role=None):
    def wrapper(f):
        def decorated_function(*args, **kwargs):
            if 'email' not in session:
                flash("Please log in first.", "error")
                return redirect(url_for('login_page'))
            if role and session.get('role') != role:
                flash("Unauthorized access.", "error")
                return redirect(url_for('login_page'))
            return f(*args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    return wrapper

# ---------- ROUTES ----------

@app.route('/')
def index():
    if 'email' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        elif session.get('role') == 'partner':
            return redirect(url_for('partner_dashboard'))
    return redirect(url_for('login_page'))

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        storage = get_storage()

        # 1. Check Admins
        admins = storage.get_admins()
        admin = next((a for a in admins if str(a['email']).strip().lower() == email.lower()), None)
        if admin and check_password(admin['password'], password):
            session['email'] = email
            session['role'] = 'admin'
            session['name'] = 'Administrator'
            session['photo_url'] = None
            return redirect(url_for('admin_dashboard'))

        # 2. Check Partners
        partners = storage.get_partners()
        partner = next((p for p in partners if str(p['email']).strip().lower() == email.lower()), None)
        if partner and check_password(partner['password'], password):
            session['email'] = email
            session['role'] = 'partner'
            session['name'] = partner['name']
            session['photo_url'] = partner['photo_url']
            return redirect(url_for('partner_dashboard'))

        flash("Invalid email or password.", "error")
        return redirect(url_for('login_page'))

    active_backend = "Google Sheets" if is_google_configured() else "Local SQLite Database"
    return render_template('login.html', active_backend=active_backend)

@app.route('/logout')
def logout():
    session.clear()
    flash("Successfully logged out.", "success")
    return redirect(url_for('login_page'))

@app.route('/admin/dashboard')
@login_required('admin')
def admin_dashboard():
    storage = get_storage()

    active_houses = storage.get_active_houses()
    partners = storage.get_partners()

    house_filter = request.args.get('house_filter', '')
    partner_filter = request.args.get('partner_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    records = storage.get_monthly_records(house_filter, partner_filter, start_date, end_date)

    total_alltime_rent = sum(r['rent_received'] for r in records)
    total_profit_all = sum(r['profit'] for r in records)
    total_owing_all = sum(r['rent_owing'] for r in records)
    maint_total = sum(r['maintenance'] for r in records)
    it_total = sum(r['it_subscription'] for r in records)
    other_inc_total = sum(r['other_income'] for r in records)
    other_exp_total = sum(r['other_expenses'] for r in records)

    current_month_str = date.today().replace(day=1).strftime("%Y-%m-%d")
    current_records = [r for r in records if r['month'] == current_month_str]
    current_rent = sum(r['rent_received'] for r in current_records)
    current_profit = sum(r['profit'] for r in current_records)
    current_owing = sum(r['rent_owing'] for r in current_records)

    return render_template('admin_dashboard.html',
                           active_houses=active_houses,
                           partners=partners,
                           records=records,
                           house_filter=house_filter,
                           partner_filter=partner_filter,
                           start_date=start_date,
                           end_date=end_date,
                           total_alltime_rent=total_alltime_rent,
                           total_profit_all=total_profit_all,
                           total_owing_all=total_owing_all,
                           maint_total=maint_total,
                           it_total=it_total,
                           other_inc_total=other_inc_total,
                           other_exp_total=other_exp_total,
                           current_rent=current_rent,
                           current_profit=current_profit,
                           current_owing=current_owing,
                           active_backend="Google Sheets" if is_google_configured() else "Local SQLite")

@app.route('/admin/add_record', methods=['POST'])
@login_required('admin')
def add_record():
    address = request.form.get('address')
    month_input = request.form.get('month')
    rent_received = float(request.form.get('rent_received', 0.0))
    receiving_partner = request.form.get('receiving_partner')
    other_income = float(request.form.get('other_income', 0.0))
    other_expenses = float(request.form.get('other_expenses', 0.0))

    if not address or not month_input or not receiving_partner:
        flash("Please fill in all required fields.", "error")
        return redirect(url_for('admin_dashboard'))

    storage = get_storage()

    maintenance = round(rent_received * 0.2, 2)
    it_subscription = 20.0

    fixed_rent = storage.get_house_fixed_rent(address)

    rent_owing = max(0.0, fixed_rent - rent_received)
    profit = rent_received + other_income - maintenance - it_subscription - other_expenses

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    house_slug = "".join(c if c.isalnum() else "-" for c in address).strip("-").lower()
    month_slug = month_input[:7]
    record_id = f"REC-{month_slug}-{house_slug}-{datetime.now().strftime('%H%M')}"

    try:
        storage.add_monthly_record(record_id, address, month_input, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, now_str)
        flash("Record successfully added!", "success")
    except Exception as e:
        flash(f"Error adding record: {e}", "error")

    return redirect(url_for('admin_dashboard'))

@app.route('/partner/dashboard')
@login_required('partner')
def partner_dashboard():
    storage = get_storage()

    partner_name = session.get('name')
    active_houses = storage.get_active_houses()

    house_filter = request.args.get('house_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    records = storage.get_monthly_records(house_filter, None, start_date, end_date)

    total_rent = sum(r['rent_received'] for r in records)
    total_profit = sum(r['profit'] for r in records)
    total_maint = sum(r['maintenance'] for r in records)
    total_it = sum(r['it_subscription'] for r in records)
    total_other_in = sum(r['other_income'] for r in records)
    total_other_ex = sum(r['other_expenses'] for r in records)

    my_receipts = sum(r['profit'] for r in records if r['receiving_partner'] == partner_name)
    my_records = [r for r in records if r['receiving_partner'] == partner_name]

    partners = storage.get_partners()
    partner_profile = next((p for p in partners if str(p['email']).strip().lower() == session.get('email').lower()), None)

    return render_template('partner_dashboard.html',
                           active_houses=active_houses,
                           records=records,
                           my_records=my_records,
                           house_filter=house_filter,
                           start_date=start_date,
                           end_date=end_date,
                           total_rent=total_rent,
                           total_profit=total_profit,
                           total_maint=total_maint,
                           total_it=total_it,
                           total_other_in=total_other_in,
                           total_other_ex=total_other_ex,
                           my_receipts=my_receipts,
                           partner_profile=partner_profile,
                           active_backend="Google Sheets" if is_google_configured() else "Local SQLite")

@app.route('/partner/update_profile', methods=['POST'])
@login_required('partner')
def update_profile():
    new_email = request.form.get('new_email', '').strip()
    current_pwd = request.form.get('current_pwd', '').strip()
    new_pwd = request.form.get('new_pwd', '').strip()
    confirm_pwd = request.form.get('confirm_pwd', '').strip()
    photo_file = request.files.get('photo')

    storage = get_storage()
    partners = storage.get_partners()
    partner = next((p for p in partners if str(p['email']).strip().lower() == session.get('email').lower()), None)

    if not partner:
        flash("Partner not found.", "error")
        return redirect(url_for('partner_dashboard'))

    if not check_password(partner['password'], current_pwd):
        flash("Current password is incorrect.", "error")
        return redirect(url_for('partner_dashboard'))

    if new_pwd:
        if new_pwd != confirm_pwd:
            flash("New passwords do not match.", "error")
            return redirect(url_for('partner_dashboard'))

    updates = {}

    if new_email and new_email != partner['email']:
        if any(str(p['email']).strip().lower() == new_email.lower() for p in partners):
            flash("Email is already taken.", "error")
            return redirect(url_for('partner_dashboard'))
        updates['email'] = new_email
        session['email'] = new_email

    if new_pwd:
        updates['password'] = generate_password_hash(new_pwd)

    if photo_file and photo_file.filename != '':
        filename = secure_filename(photo_file.filename)
        unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"

        if isinstance(storage, GoogleSheetsStorage):
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp:
                photo_file.save(tmp.name)
                tmp_path = tmp.name
            try:
                drive_service = storage.get_drive_service()
                file_metadata = {"name": f"photo_{session.get('email')}_{filename}"}
                media = MediaFileUpload(tmp_path, mimetype="image/jpeg" if filename.lower().endswith(('.jpg', '.jpeg')) else "image/png")
                file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
                drive_service.permissions().create(fileId=file["id"], body={"type": "anyone", "role": "reader"}).execute()
                photo_url = f"https://drive.google.com/uc?export=view&id={file['id']}"
                updates['photo_url'] = photo_url
                session['photo_url'] = photo_url
            except Exception as e:
                flash(f"Failed to upload photo to Google Drive: {e}", "error")
            finally:
                os.unlink(tmp_path)
        else:
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            photo_file.save(photo_path)
            photo_url = f"/static/uploads/{unique_filename}"
            updates['photo_url'] = photo_url
            session['photo_url'] = photo_url

    if updates:
        try:
            storage.update_partner_profile(partner['email'], updates)
            flash("Profile updated successfully!", "success")
        except Exception as e:
            flash(f"Error updating profile: {e}", "error")
    else:
        flash("No changes were made.", "info")

    return redirect(url_for('partner_dashboard'))

@app.route('/download_csv')
@login_required()
def download_csv():
    storage = get_storage()

    house_filter = request.args.get('house_filter', '')
    partner_filter = request.args.get('partner_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    records = storage.get_monthly_records(house_filter, partner_filter, start_date, end_date)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Record_ID", "Address", "Month", "Rent_Received", "Receiving_Partner",
                     "Maintenance", "IT_Subscription", "Other_Income", "Other_Expenses",
                     "Profit", "Rent_Owing", "Timestamp"])

    for r in records:
        writer.writerow([
            r['record_id'], r['address'], r['month'], r['rent_received'], r['receiving_partner'],
            r['maintenance'], r['it_subscription'], r['other_income'], r['other_expenses'],
            r['profit'], r['rent_owing'], r['timestamp']
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=monthly_records.csv"}
    )

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
