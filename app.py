import os
import sqlite3
import hashlib
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, Response
import io
import csv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "high_fields_flats_super_secret_key"
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DATABASE = 'high_fields.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database and seeds it with default data if empty."""
    conn = get_db()
    cursor = conn.cursor()

    # Create tables
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

    # Check if empty and seed
    cursor.execute("SELECT COUNT(*) FROM admins")
    if cursor.fetchone()[0] == 0:
        # Seed default admin
        cursor.execute("INSERT INTO admins (email, password) VALUES (?, ?)",
                       ("admin@highfields.com", generate_password_hash("admin123")))

        # Seed partners
        cursor.execute("INSERT INTO partners (email, password, name, photo_url) VALUES (?, ?, ?, ?)",
                       ("partner1@highfields.com", generate_password_hash("partner123"), "Partner One", ""))
        cursor.execute("INSERT INTO partners (email, password, name, photo_url) VALUES (?, ?, ?, ?)",
                       ("partner2@highfields.com", generate_password_hash("partner223"), "Partner Two", ""))

        # Seed houses
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("123 High Fields St", 1200.0, 1))
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("456 Valley Rd", 1000.0, 1))
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("789 Hilltop Ln", 1500.0, 1))
        cursor.execute("INSERT INTO houses (address, fixed_rent, active) VALUES (?, ?, ?)",
                       ("101 Pine St", 800.0, 0)) # Inactive

        # Seed monthly_records
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

        conn = get_db()
        cursor = conn.cursor()

        # 1. Check Admin Logins
        cursor.execute("SELECT * FROM admins WHERE email = ?", (email,))
        admin = cursor.fetchone()
        if admin and check_password_hash(admin['password'], password):
            session['email'] = email
            session['role'] = 'admin'
            session['name'] = 'Administrator'
            session['photo_url'] = None
            conn.close()
            return redirect(url_for('admin_dashboard'))

        # 2. Check Partners
        cursor.execute("SELECT * FROM partners WHERE email = ?", (email,))
        partner = cursor.fetchone()
        if partner and check_password_hash(partner['password'], password):
            session['email'] = email
            session['role'] = 'partner'
            session['name'] = partner['name']
            session['photo_url'] = partner['photo_url']
            conn.close()
            return redirect(url_for('partner_dashboard'))

        conn.close()
        flash("Invalid email or password.", "error")
        return redirect(url_for('login_page'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Successfully logged out.", "success")
    return redirect(url_for('login_page'))

@app.route('/admin/dashboard')
@login_required('admin')
def admin_dashboard():
    conn = get_db()
    cursor = conn.cursor()

    # Get active houses and partners
    cursor.execute("SELECT * FROM houses WHERE active = 1")
    active_houses = [dict(row) for row in cursor.fetchall()]

    cursor.execute("SELECT * FROM partners")
    partners = [dict(row) for row in cursor.fetchall()]

    # Get filters
    house_filter = request.args.get('house_filter', '')
    partner_filter = request.args.get('partner_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    # Base query for records
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

    # Calculations for KPIs (All-time vs current month)
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

    conn.close()

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
                           current_owing=current_owing)

@app.route('/admin/add_record', methods=['POST'])
@login_required('admin')
def add_record():
    address = request.form.get('address')
    month_input = request.form.get('month') # expected YYYY-MM-DD
    rent_received = float(request.form.get('rent_received', 0.0))
    receiving_partner = request.form.get('receiving_partner')
    other_income = float(request.form.get('other_income', 0.0))
    other_expenses = float(request.form.get('other_expenses', 0.0))

    if not address or not month_input or not receiving_partner:
        flash("Please fill in all required fields.", "error")
        return redirect(url_for('admin_dashboard'))

    # Auto-calculations
    maintenance = round(rent_received * 0.2, 2)
    it_subscription = 20.0

    # Get fixed rent for the selected house
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT fixed_rent FROM houses WHERE address = ?", (address,))
    house = cursor.fetchone()
    fixed_rent = house['fixed_rent'] if house else 0.0

    rent_owing = max(0.0, fixed_rent - rent_received)
    profit = rent_received + other_income - maintenance - it_subscription - other_expenses

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    house_slug = "".join(c if c.isalnum() else "-" for c in address).strip("-").lower()
    month_slug = month_input[:7] # YYYY-MM
    record_id = f"REC-{month_slug}-{house_slug}-{datetime.now().strftime('%H%M')}"

    try:
        cursor.execute("""
        INSERT INTO monthly_records (record_id, address, month, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (record_id, address, month_input, rent_received, receiving_partner, maintenance, it_subscription, other_income, other_expenses, profit, rent_owing, now_str))
        conn.commit()
        flash("Record successfully added!", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error adding record: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))

@app.route('/partner/dashboard')
@login_required('partner')
def partner_dashboard():
    conn = get_db()
    cursor = conn.cursor()

    partner_name = session.get('name')

    # Get active houses for filters
    cursor.execute("SELECT * FROM houses WHERE active = 1")
    active_houses = [dict(row) for row in cursor.fetchall()]

    # Get filters
    house_filter = request.args.get('house_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    # Fetch records (all records to show overall financial summary)
    query = "SELECT * FROM monthly_records WHERE 1=1"
    params = []

    if house_filter:
        query += " AND address = ?"
        params.append(house_filter)
    if start_date:
        query += " AND month >= ?"
        params.append(start_date)
    if end_date:
        query += " AND month <= ?"
        params.append(end_date)

    query += " ORDER BY month DESC"
    cursor.execute(query, params)
    records = [dict(row) for row in cursor.fetchall()]

    # Financial summary calculations based on filtered records
    total_rent = sum(r['rent_received'] for r in records)
    total_profit = sum(r['profit'] for r in records)
    total_maint = sum(r['maintenance'] for r in records)
    total_it = sum(r['it_subscription'] for r in records)
    total_other_in = sum(r['other_income'] for r in records)
    total_other_ex = sum(r['other_expenses'] for r in records)

    # My Received Rent: Only profit where this partner is the receiving partner
    my_receipts = sum(r['profit'] for r in records if r['receiving_partner'] == partner_name)

    # Tab 2: Months where I received rent
    my_records = [r for r in records if r['receiving_partner'] == partner_name]

    # Current partner info for profile settings
    cursor.execute("SELECT * FROM partners WHERE email = ?", (session.get('email'),))
    partner_profile = dict(cursor.fetchone())

    conn.close()

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
                           partner_profile=partner_profile)

@app.route('/partner/update_profile', methods=['POST'])
@login_required('partner')
def update_profile():
    new_email = request.form.get('new_email', '').strip()
    current_pwd = request.form.get('current_pwd', '').strip()
    new_pwd = request.form.get('new_pwd', '').strip()
    confirm_pwd = request.form.get('confirm_pwd', '').strip()
    photo_file = request.files.get('photo')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM partners WHERE email = ?", (session.get('email'),))
    partner = cursor.fetchone()

    if not partner:
        conn.close()
        flash("Partner not found.", "error")
        return redirect(url_for('partner_dashboard'))

    # Verify current password
    if not check_password_hash(partner['password'], current_pwd):
        conn.close()
        flash("Current password is incorrect.", "error")
        return redirect(url_for('partner_dashboard'))

    # Validation for new password
    if new_pwd:
        if new_pwd != confirm_pwd:
            conn.close()
            flash("New passwords do not match.", "error")
            return redirect(url_for('partner_dashboard'))

    # Compile updates
    updates = []
    params = []

    if new_email and new_email != partner['email']:
        # Ensure new email is unique
        cursor.execute("SELECT COUNT(*) FROM partners WHERE email = ?", (new_email,))
        if cursor.fetchone()[0] > 0:
            conn.close()
            flash("Email is already taken.", "error")
            return redirect(url_for('partner_dashboard'))
        updates.append("email = ?")
        params.append(new_email)
        session['email'] = new_email

    if new_pwd:
        updates.append("password = ?")
        params.append(generate_password_hash(new_pwd))

    if photo_file and photo_file.filename != '':
        filename = secure_filename(photo_file.filename)
        # Append timestamp to avoid caching/collision issues
        unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        photo_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        photo_file.save(photo_path)

        photo_url = f"/static/uploads/{unique_filename}"
        updates.append("photo_url = ?")
        params.append(photo_url)
        session['photo_url'] = photo_url

    if updates:
        query = f"UPDATE partners SET {', '.join(updates)} WHERE email = ?"
        params.append(partner['email'])
        try:
            cursor.execute(query, params)
            conn.commit()
            flash("Profile updated successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error updating profile: {e}", "error")
    else:
        flash("No changes were made.", "info")

    conn.close()
    return redirect(url_for('partner_dashboard'))

@app.route('/download_csv')
@login_required()
def download_csv():
    conn = get_db()
    cursor = conn.cursor()

    house_filter = request.args.get('house_filter', '')
    partner_filter = request.args.get('partner_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = "SELECT * FROM monthly_records WHERE 1=1"
    params = []

    if house_filter:
        query += " AND address = ?"
        params.append(house_filter)
    if partner_filter:
        query += " AND receiving_partner = ?"
        params.append(partner_filter)
    # If the user is a partner, limit download to records they are allowed to see or overall filters
    if session.get('role') == 'partner':
        # If partner downloaded, we can download overall filtered data or only their records?
        # The streamlit app did:
        # csv = filtered.to_csv(index=False).encode('utf-8')
        # where filtered could be filtered by house or date range, so they can download that filtered set.
        pass

    if start_date:
        query += " AND month >= ?"
        params.append(start_date)
    if end_date:
        query += " AND month <= ?"
        params.append(end_date)

    query += " ORDER BY month DESC"
    cursor.execute(query, params)
    records = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    # Headers
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
