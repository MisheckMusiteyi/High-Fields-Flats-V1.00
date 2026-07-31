# -*- coding: utf-8 -*-
"""high_fields.py

Streamlit rental tracker for High Fields Flats.
"""

import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import hashlib
from datetime import datetime, date
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import tempfile
import os

# ---------- PAGE CONFIG & STYLE ----------
st.set_page_config(page_title="High Fields Flats – Rental Tracker", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Georgia:ital,wght@0,400;0,700;1,400;1,700&display=swap');
    html, body, [class*="css"]  {
        font-family: 'Georgia', serif;
    }
    :root {
        --navy: #0B1F3B;
        --orange: #F15A24;
        --light-bg: #F5F7FA;
        --card-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .stApp {
        background-color: var(--light-bg);
    }
    .main-header {
        color: var(--navy);
        font-size: 2.2rem;
        font-weight: bold;
        border-bottom: 3px solid var(--orange);
        padding-bottom: 0.5rem;
        margin-bottom: 1.5rem;
    }
    .kpi-card {
        background: white;
        border-radius: 12px;
        padding: 1.2rem;
        box-shadow: var(--card-shadow);
        border-left: 6px solid var(--orange);
        margin-bottom: 1rem;
    }
    .kpi-card h3 {
        color: var(--navy);
        font-size: 1rem;
        margin-bottom: 0.3rem;
    }
    .kpi-card .value {
        font-size: 2rem;
        font-weight: bold;
        color: var(--navy);
    }
</style>
""", unsafe_allow_html=True)

# ---------- GOOGLE API SETUP ----------
def get_gspread_client():
    json_str = st.secrets["google"]["service_account_json"]
    service_account_info = json.loads(json_str)
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    return gspread.authorize(creds)

def get_drive_service():
    json_str = st.secrets["google"]["service_account_json"]
    service_account_info = json.loads(json_str)
    from google.oauth2 import service_account
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=credentials)

def load_sheet(tab_name):
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["google"]["sheet_id"])
    worksheet = sh.worksheet(tab_name)
    data = worksheet.get_all_records()
    return pd.DataFrame(data) if data else pd.DataFrame()

def append_row(tab_name, row_dict):
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["google"]["sheet_id"])
    worksheet = sh.worksheet(tab_name)
    headers = worksheet.row_values(1)
    row = [row_dict.get(col, "") for col in headers]
    worksheet.append_row(row)

def update_row(tab_name, row_index, updates):
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["google"]["sheet_id"])
    worksheet = sh.worksheet(tab_name)
    for col, value in updates.items():
        worksheet.update_cell(row_index, col, value)

# ---------- AUTHENTICATION ----------
def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def authenticate(email, password):
    """Returns (role, name, photo_url) or None"""
    # 1. Check Admin Logins sheet (no underscore)
    admins = load_sheet("Admin Logins")
    if not admins.empty:
        admin_row = admins[admins["Email"].astype(str) == email]
        if not admin_row.empty:
            stored_hash = str(admin_row.iloc[0]["Password"])
            if hash_password(password) == stored_hash:
                return "admin", "Administrator", None

    # 2. Check Partners sheet
    partners = load_sheet("Partners")
    if not partners.empty:
        partner_row = partners[partners["Email"].astype(str) == email]
        if not partner_row.empty:
            stored_hash = str(partner_row.iloc[0]["Password"])
            if hash_password(password) == stored_hash:
                photo = partner_row.iloc[0].get("Photo_URL", "")
                return "partner", partner_row.iloc[0]["Name"], photo if pd.notna(photo) else None

    return None

# ---------- SESSION STATE ----------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.role = None
    st.session_state.name = None
    st.session_state.photo_url = None
    st.session_state.email = None

# ---------- LOGIN PAGE ----------
if not st.session_state.logged_in:
    st.markdown("<h1 class='main-header'>High Fields Flats – Rental Tracker</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        with st.form("login_form"):
            st.markdown("<h3 style='color: var(--navy);'>Login</h3>", unsafe_allow_html=True)
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", type="primary")
            if submitted:
                result = authenticate(email, password)
                if result:
                    st.session_state.logged_in = True
                    st.session_state.role = result[0]
                    st.session_state.name = result[1]
                    st.session_state.photo_url = result[2]
                    st.session_state.email = email
                    st.rerun()
                else:
                    st.error("Invalid email or password.")
    st.stop()

def logout():
    st.session_state.logged_in = False
    st.rerun()

# ---------- SIDEBAR ----------
with st.sidebar:
    st.markdown(f"<h2 style='color:white;'>{st.secrets['app']['title']}</h2>", unsafe_allow_html=True)
    if st.session_state.photo_url:
        st.image(st.session_state.photo_url, width=100)
    st.markdown(f"**{st.session_state.name}**  \n*{st.session_state.role.capitalize()}*")
    st.button("Logout", on_click=logout, key="logout")
    st.markdown("---")

def get_active_houses():
    houses = load_sheet("Houses")
    if not houses.empty:
        # Accept "Yes" (case-insensitive) or boolean True
        active_col = houses["Active"].astype(str).str.strip().str.lower()
        active = houses[active_col.isin(["true", "yes"])]
        return active["House_Number"].tolist()
    return []

# ---------- ADMIN DASHBOARD ----------
if st.session_state.role == "admin":
    st.markdown("<h1 class='main-header'>Admin Dashboard</h1>", unsafe_allow_html=True)
    tab1, tab2, tab3 = st.tabs(["Overview & KPIs", "Add Monthly Record", "Data Table & Download"])

    records = load_sheet("MonthlyRecords")
    houses_df = load_sheet("Houses")
    partners_df = load_sheet("Partners")

    with tab2:
        st.subheader("Record New Month")
        with st.form("add_record", clear_on_submit=True):
            colA, colB = st.columns(2)
            with colA:
                house = st.selectbox("House", get_active_houses())
                month_input = st.date_input("Month (select 1st day)", value=date.today().replace(day=1))
            with colB:
                rent_received = st.number_input("Rent Received ($)", min_value=0.0, value=0.0, step=10.0)
                receiving_partner = st.selectbox("Receiving Partner", partners_df["Name"].tolist() if not partners_df.empty else [])
                other_income = st.number_input("Other Income ($)", min_value=0.0, value=0.0, step=10.0)
                other_expenses = st.number_input("Other Expenses ($)", min_value=0.0, value=0.0, step=10.0)

            maintenance = round(rent_received * 0.2, 2)
            it_sub = 20.0

            # Note: because these live inside the same st.form as rent_received,
            # Streamlit won't recompute them live as the user types - they only
            # reflect the current rent_received value on the initial render/rerun.
            colC, colD = st.columns(2)
            with colC:
                maintenance_input = st.number_input("Maintenance (auto 20%)", value=maintenance, step=1.0)
            with colD:
                it_input = st.number_input("IT Subscription (auto $20)", value=it_sub, step=1.0)

            profit = rent_received + other_income - maintenance_input - it_input - other_expenses
            fixed_rent = 0
            if not houses_df.empty:
                # Compare as strings in case Sheets returns House_Number with
                # inconsistent types (e.g. int vs str) between sheets.
                house_row = houses_df[houses_df["House_Number"].astype(str) == str(house)]
                if not house_row.empty:
                    fixed_rent = house_row.iloc[0]["Fixed_Rent"]
            rent_owing = max(0, fixed_rent - rent_received)

            st.markdown(f"**Calculated Profit:** ${profit:,.2f}  |  **Rent Owing:** ${rent_owing:,.2f}")
            submitted = st.form_submit_button("Save Record", type="primary")
            if submitted:
                if not house or not receiving_partner:
                    st.error("Please select house and partner.")
                else:
                    now = datetime.now()
                    rec_id = f"REC-{month_input.strftime('%Y-%m')}-{house}-{now.strftime('%H%M')}"
                    new_row = {
                        "Record_ID": rec_id,
                        "House_Number": house,
                        "Month": month_input.strftime("%Y-%m-%d"),
                        "Rent_Received": rent_received,
                        "Receiving_Partner": receiving_partner,
                        "Maintenance": maintenance_input,
                        "IT_Subscription": it_input,
                        "Other_Income": other_income,
                        "Other_Expenses": other_expenses,
                        "Profit": profit,
                        "Rent_Owing": rent_owing,
                        "Timestamp": now.strftime("%Y-%m-%d %H:%M")
                    }
                    try:
                        append_row("MonthlyRecords", new_row)
                        st.success("Record saved!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")

    with tab1:
        st.subheader("Key Performance Indicators")
        if records.empty:
            st.info("No records yet.")
        else:
            records["Month"] = pd.to_datetime(records["Month"])
            total_alltime_rent = records["Rent_Received"].sum()
            total_profit_all = records["Profit"].sum()
            total_owing_all = records["Rent_Owing"].sum()
            current_month_start = date.today().replace(day=1)
            current_records = records[records["Month"].dt.date == current_month_start]
            current_rent = current_records["Rent_Received"].sum() if not current_records.empty else 0
            current_profit = current_records["Profit"].sum() if not current_records.empty else 0
            current_owing = current_records["Rent_Owing"].sum() if not current_records.empty else 0
            maint_total = records["Maintenance"].sum()
            it_total = records["IT_Subscription"].sum()

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.markdown(f"<div class='kpi-card'><h3>Total Rent (All Time)</h3><div class='value'>${total_alltime_rent:,.2f}</div></div>", unsafe_allow_html=True)
            with col2:
                st.markdown(f"<div class='kpi-card'><h3>Current Month Rent</h3><div class='value'>${current_rent:,.2f}</div></div>", unsafe_allow_html=True)
            with col3:
                st.markdown(f"<div class='kpi-card'><h3>Total Profit (All Time)</h3><div class='value'>${total_profit_all:,.2f}</div></div>", unsafe_allow_html=True)
            with col4:
                st.markdown(f"<div class='kpi-card'><h3>Current Month Profit</h3><div class='value'>${current_profit:,.2f}</div></div>", unsafe_allow_html=True)

            col5, col6, col7, col8 = st.columns(4)
            with col5:
                st.markdown(f"<div class='kpi-card'><h3>Total Rent Owing</h3><div class='value'>${total_owing_all:,.2f}</div></div>", unsafe_allow_html=True)
            with col6:
                st.markdown(f"<div class='kpi-card'><h3>Maintenance (All Time)</h3><div class='value'>${maint_total:,.2f}</div></div>", unsafe_allow_html=True)
            with col7:
                st.markdown(f"<div class='kpi-card'><h3>IT Subscriptions (All Time)</h3><div class='value'>${it_total:,.2f}</div></div>", unsafe_allow_html=True)
            with col8:
                st.markdown(f"<div class='kpi-card'><h3>Other Income / Expenses</h3><div class='value'>+${records['Other_Income'].sum():,.2f} / -${records['Other_Expenses'].sum():,.2f}</div></div>", unsafe_allow_html=True)

    with tab3:
        st.subheader("All Records")
        if not records.empty:
            house_filter = st.multiselect("Filter by House", options=records["House_Number"].unique())
            partner_filter = st.multiselect("Filter by Receiving Partner", options=records["Receiving_Partner"].unique())
            date_range = st.date_input("Date Range", value=[])
            filtered = records.copy()
            if house_filter:
                filtered = filtered[filtered["House_Number"].isin(house_filter)]
            if partner_filter:
                filtered = filtered[filtered["Receiving_Partner"].isin(partner_filter)]
            if len(date_range) == 2:
                start, end = date_range
                filtered = filtered[(filtered["Month"].dt.date >= start) & (filtered["Month"].dt.date <= end)]
            st.dataframe(filtered, use_container_width=True)
            csv = filtered.to_csv(index=False).encode('utf-8')
            st.download_button("Download CSV", csv, "monthly_records.csv", "text/csv", key='download-csv')

# ---------- PARTNER DASHBOARD ----------
elif st.session_state.role == "partner":
    st.markdown(f"<h1 class='main-header'>Welcome, {st.session_state.name}</h1>", unsafe_allow_html=True)
    records = load_sheet("MonthlyRecords")
    if not records.empty:
        records["Month"] = pd.to_datetime(records["Month"])

    with st.sidebar.expander("Profile Settings"):
        st.subheader("Update Profile")
        with st.form("profile_form"):
            new_email = st.text_input("New Email (leave blank to keep)", value="")
            current_pwd = st.text_input("Current Password", type="password")
            new_pwd = st.text_input("New Password (leave blank to keep)", type="password")
            confirm_pwd = st.text_input("Confirm New Password", type="password")
            uploaded_file = st.file_uploader("Profile Photo", type=["png","jpg","jpeg"])
            submit_profile = st.form_submit_button("Save Changes")
            if submit_profile:
                partners_df = load_sheet("Partners")
                my_row_idx = partners_df.index[partners_df["Email"].astype(str) == st.session_state.email].tolist()
                if not my_row_idx:
                    st.error("User not found.")
                else:
                    my_row = partners_df.iloc[my_row_idx[0]]
                    if hash_password(current_pwd) != str(my_row["Password"]):
                        st.error("Current password is incorrect.")
                    elif new_pwd and new_pwd != confirm_pwd:
                        st.error("Passwords don't match.")
                    else:
                        updates = {}
                        row_num = my_row_idx[0] + 2
                        if new_email and new_email != my_row["Email"]:
                            updates["Email"] = new_email
                            st.session_state.email = new_email
                        if new_pwd:
                            updates["Password"] = hash_password(new_pwd)
                        if uploaded_file:
                            drive_service = get_drive_service()
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                                tmp.write(uploaded_file.getvalue())
                                tmp_path = tmp.name
                            try:
                                file_metadata = {"name": f"photo_{st.session_state.email}.jpg"}
                                media = MediaFileUpload(tmp_path, mimetype="image/jpeg")
                                file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
                                drive_service.permissions().create(fileId=file["id"], body={"type": "anyone", "role": "reader"}).execute()
                                photo_url = f"https://drive.google.com/uc?export=view&id={file['id']}"
                                updates["Photo_URL"] = photo_url
                                st.session_state.photo_url = photo_url
                            finally:
                                os.unlink(tmp_path)
                        if updates:
                            for col_name, value in updates.items():
                                col_idx = partners_df.columns.get_loc(col_name) + 1
                                update_row("Partners", row_num, {col_idx: value})
                            st.success("Profile updated. Refresh to see changes.")
                            st.rerun()

    tab1, tab2 = st.tabs(["My Financials", "My Rent Receipts"])
    with tab1:
        st.subheader("Financial Summary")
        if records.empty:
            st.info("No data yet.")
        else:
            col_filter1, col_filter2 = st.columns(2)
            with col_filter1:
                house_choices = get_active_houses()
                selected_house = st.selectbox("Select House", ["All"] + house_choices)
            with col_filter2:
                if not records.empty:
                    min_date = records["Month"].min().date()
                    max_date = records["Month"].max().date()
                    date_range = st.date_input("Date range", [min_date, max_date])
                else:
                    date_range = []

            filtered = records.copy()
            if selected_house != "All":
                filtered = filtered[filtered["House_Number"].astype(str) == str(selected_house)]
            if len(date_range) == 2:
                start, end = date_range
                filtered = filtered[(filtered["Month"].dt.date >= start) & (filtered["Month"].dt.date <= end)]

            total_rent = filtered["Rent_Received"].sum()
            total_maint = filtered["Maintenance"].sum()
            total_it = filtered["IT_Subscription"].sum()
            total_other_in = filtered["Other_Income"].sum()
            total_other_ex = filtered["Other_Expenses"].sum()
            total_profit = filtered["Profit"].sum()
            my_receipts = filtered[filtered["Receiving_Partner"] == st.session_state.name]["Profit"].sum()

            colA, colB, colC = st.columns(3)
            with colA:
                st.markdown(f"<div class='kpi-card'><h3>Total Rent Received</h3><div class='value'>${total_rent:,.2f}</div></div>", unsafe_allow_html=True)
            with colB:
                st.markdown(f"<div class='kpi-card'><h3>Total Profit</h3><div class='value'>${total_profit:,.2f}</div></div>", unsafe_allow_html=True)
            with colC:
                st.markdown(f"<div class='kpi-card'><h3>My Received Rent</h3><div class='value'>${my_receipts:,.2f}</div></div>", unsafe_allow_html=True)

            colD, colE, colF = st.columns(3)
            with colD:
                st.markdown(f"<div class='kpi-card'><h3>Maintenance Costs</h3><div class='value'>${total_maint:,.2f}</div></div>", unsafe_allow_html=True)
            with colE:
                st.markdown(f"<div class='kpi-card'><h3>IT Subscriptions</h3><div class='value'>${total_it:,.2f}</div></div>", unsafe_allow_html=True)
            with colF:
                st.markdown(f"<div class='kpi-card'><h3>Other Income / Expenses</h3><div class='value'>+${total_other_in:,.2f} / -${total_other_ex:,.2f}</div></div>", unsafe_allow_html=True)

            st.dataframe(filtered, use_container_width=True)
            csv = filtered.to_csv(index=False).encode('utf-8')
            st.download_button("Download My Data", csv, "my_data.csv", "text/csv")

    with tab2:
        st.subheader("Months Where I Received Rent")
        if not records.empty:
            my_records = records[records["Receiving_Partner"] == st.session_state.name]
            if not my_records.empty:
                st.dataframe(my_records[["Month", "House_Number", "Profit"]], use_container_width=True)
                total_my_profit = my_records["Profit"].sum()
                st.markdown(f"**Total I've Received: ${total_my_profit:,.2f}**")
            else:
                st.info("No rent receipts assigned to you yet.")
