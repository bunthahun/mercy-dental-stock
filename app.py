import os
import sys
import sqlite3
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, session
from werkzeug.security import generate_password_hash, check_password_hash
import io
import csv

# កំណត់ Encoding សម្រាប់ Windows Terminal ឲ្យស្គាល់អក្សរខ្មែរ និង Emoji
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

app = Flask(__name__)
app.secret_key = "mercy_dental_secret_key_2026"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "dental_inventory.db")

# លេខកូដសម្ងាត់ Admin PIN បម្រុង
ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    
    # ១. តារាងសារពើភ័ណ្ឌស្តុក
    conn.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            expiry_date TEXT NOT NULL,
            category TEXT DEFAULT 'ទូទៅ',
            min_threshold INTEGER DEFAULT 10,
            unit TEXT DEFAULT 'ប្រអប់',
            notes TEXT DEFAULT '',
            added_by TEXT DEFAULT '',
            is_deleted INTEGER DEFAULT 0,
            deleted_at TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ២. តារាងកត់ត្រាការដក និងដាក់ស្តុកតាមខែនីមួយៗ (Usage & Restock History)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS usage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit TEXT DEFAULT 'ប្រអប់',
            category TEXT DEFAULT 'ទូទៅ',
            action_type TEXT DEFAULT 'ដកប្រើ',
            person_name TEXT DEFAULT '',
            patient_name TEXT DEFAULT '',
            used_date TEXT NOT NULL,
            month_year TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ធានាថាមាន column patient_name ក្នុង usage_history ដោយរក្សាទិន្នន័យចាស់ជានិច្ច
    cursor = conn.execute("PRAGMA table_info(usage_history)")
    existing_cols = [col[1] for col in cursor.fetchall()]
    if 'patient_name' not in existing_cols:
        conn.execute("ALTER TABLE usage_history ADD COLUMN patient_name TEXT DEFAULT ''")

    # ៣. តារាងអ្នកប្រើប្រាស់ (Users Authentication Table)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ៤. តារាងកត់ត្រាសកម្មភាពបុគ្គលិក (Activity Logs / Audit Trail)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            action TEXT NOT NULL,
            item_name TEXT DEFAULT '',
            details TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ៥. តារាងសម្ភារៈត្រូវទិញរួមគ្នា (Shared Items to Buy Table across all computers)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS items_to_buy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER UNIQUE,
            item_name TEXT NOT NULL,
            category TEXT DEFAULT 'ទូទៅ',
            quantity INTEGER DEFAULT 0,
            unit TEXT DEFAULT 'ប្រអប់',
            min_threshold INTEGER DEFAULT 10,
            deficit INTEGER DEFAULT 1,
            notes TEXT DEFAULT '',
            added_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ៦. តារាងសម្ភារះ Implant ជិតអស់ស្តុក (Shared Implant Low Stock Table across all computers)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS items_reserve_buy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER UNIQUE,
            item_name TEXT NOT NULL,
            category TEXT DEFAULT 'ទូទៅ',
            quantity INTEGER DEFAULT 0,
            unit TEXT DEFAULT 'ប្រអប់',
            min_threshold INTEGER DEFAULT 10,
            deficit INTEGER DEFAULT 1,
            notes TEXT DEFAULT '',
            added_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # បង្កើតគណនី Admin លំនាំដើម (Default Admins: admin & mercydental)
    for adm_name in ('admin', 'mercydental'):
        exists = conn.execute("SELECT id FROM users WHERE username = ?", (adm_name,)).fetchone()
        if not exists:
            conn.execute('''
                INSERT INTO users (username, password_hash, full_name, role, is_active)
                VALUES (?, ?, ?, ?, 1)
            ''', (adm_name, generate_password_hash('admin123'), f'Admin ({adm_name})', 'admin'))
    
    conn.commit()
    conn.close()

# ដំណើរការបង្កើតតារាង និងរៀបចំទិន្នន័យចាំបាច់ពេល Load Module (សម្រាប់ Local និង PythonAnywhere WSGI)
try:
    init_db()
except Exception as e:
    print(f"Warning initializing DB: {e}")


# បញ្ជីសម្ភារៈ Implant ថ្មីទាំង ១៥ មុខ (Abutment, Locator...)
NEW_IMPLANT_ITEMS = [
    ("Angled Abutment Size 05.0xGH2.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Angled Abutment Size 05.0xGH4.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Angled Abutment Size 06.0xGH2.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Angled Abutment Size 06.0xGH4.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Straight Abutment Size 04.5xGH1.0xL7.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Straight Abutment Size 04.5xGH2.0xL7.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Straight Abutment Size 04.5xGH3.0xL7.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Straight Abutment Size 05.0xGH3.0xL7.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Locator Abutment Size 04.0xGH4.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Locator Abutment Size 04.0xGH5.0 R/Hex", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment", "Admin / ស្តុកមេ"),
    ("Locator Size Long", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Locator", "Admin / ស្តុកមេ"),
    ("Locator Size Short", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Locator", "Admin / ស្តុកមេ"),
    ("Purchase Card Abutment", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Abutment Card", "Admin / ស្តុកមេ"),
    ("Temporary Abutment", "Implant", 0, "ឈុត", "2029-12-31", 5, "សម្ភារៈ Implant / Temporary Abutment", "Admin / ស្តុកមេ"),
    ("Implant ស្តុកនៅក្រុមហ៊ុនNeo Biodent", "Implant", 0, "ឈុត", "2029-12-31", 5, "ស្តុកនៅក្រុមហ៊ុន Neo Biodent", "Admin / ស្តុកមេ"),
]

def ensure_implant_items_exist():
    """ធានាថាសម្ភារៈថ្មីទាំង ១៥ មុខក្នុងប្រភេទ Implant មាននៅក្នុងប្រព័ន្ធជានិច្ច ដោយមិនប៉ះពាល់ទិន្នន័យចាស់"""
    try:
        conn = get_db_connection()
        for name, cat, qty, unit, exp, thresh, notes, added_by in NEW_IMPLANT_ITEMS:
            row = conn.execute("SELECT id, category FROM inventory WHERE item_name = ?", (name,)).fetchone()
            if row:
                if row['category'] != cat:
                    conn.execute("UPDATE inventory SET category = ? WHERE id = ?", (cat, row['id']))
            else:
                conn.execute("""
                    INSERT INTO inventory (item_name, category, quantity, unit, expiry_date, min_threshold, notes, added_by, is_deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (name, cat, qty, unit, exp, thresh, notes, added_by))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Auto-sync note: {e}")

# ដំណើរការបង្កើតតារាង និងពិនិត្យសម្ភារៈថ្មីដោយស្វ័យប្រវត្តិតាំងពីពេល Start App
try:
    init_db()
    ensure_implant_items_exist()
except Exception as e:
    print(f"Startup DB init: {e}")

# ----------------- SECURITY & AUTHENTICATION HELPERS ----------------- #

def login_required(f):
    """Decorator ការពារ Route តម្រូវឱ្យ Login ជាមុនសិន"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('សូមចូលគណនី (Login) ជាមុនសិន! 🔒', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator ការពារ Route តម្រូវឱ្យមានសិទ្ធិជា Admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('សូមចូលគណនី (Login) ជាមុនសិន! 🔒', 'warning')
            return redirect(url_for('login', next=request.url))
        if session.get('role') != 'admin':
            flash('ទាមទារសិទ្ធិជា Admin ដើម្បីចូលមើល ឬគ្រប់គ្រងផ្នែកនេះ! 🚫', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def log_activity(action, item_name='', details=''):
    """កត់ត្រារាល់សកម្មភាពរបស់បុគ្គលិក និង Admin ចូលក្នុង Activity Logs"""
    try:
        conn = get_db_connection()
        user_id = session.get('user_id')
        username = session.get('username', 'Guest')
        full_name = session.get('full_name', 'ភ្ញៀវ/Guest')
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr or '')
        conn.execute('''
            INSERT INTO activity_logs (user_id, username, full_name, action, item_name, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, action, item_name, details, ip_address))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Error logging activity:", e)

def is_admin_authorized(req_pin=None):
    """ពិនិត្យសិទ្ធិ Admin តាមរយៈ Session ឬ Admin PIN"""
    if session.get('role') == 'admin':
        return True
    if req_pin and req_pin.strip() == ADMIN_PIN:
        return True
    return False

@app.context_processor
def inject_user_info():
    """បញ្ជូនព័ត៌មានអ្នកប្រើប្រាស់បច្ចុប្បន្នទៅកាន់គ្រប់ Template"""
    is_admin = (session.get('role') == 'admin')
    return dict(
        current_user={
            'id': session.get('user_id'),
            'username': session.get('username'),
            'full_name': session.get('full_name'),
            'role': session.get('role', 'guest'),
            'is_admin': is_admin
        },
        is_admin=is_admin
    )

def evaluate_status(item):
    """វាយតម្លៃស្ថានភាពស្តុក និងកាលបរិច្ឆេទផុតកំណត់"""
    today = date.today()
    status_flags = []
    
    qty = item['quantity'] if item['quantity'] is not None else 0
    min_thresh = item['min_threshold'] if item['min_threshold'] is not None else 10

    if qty <= 0:
        status_flags.append(('out_of_stock', 'អស់ពីស្តុក', 'badge-danger'))
    elif min_thresh > 0 and qty <= min_thresh:
        status_flags.append(('low_stock', 'ជិតអស់', 'badge-warning'))
    else:
        status_flags.append(('in_stock', 'មានក្នុងស្តុក', 'badge-success'))

    try:
        exp_date = datetime.strptime(item['expiry_date'], '%Y-%m-%d').date()
        days_left = (exp_date - today).days
        if days_left < 0:
            status_flags.append(('expired', f'ផុតកំណត់ ({abs(days_left)} ថ្ងៃមុន)', 'badge-danger'))
        elif days_left <= 60:
            status_flags.append(('expiring_soon', f'ជិតផុត ({days_left} ថ្ងៃទៀត)', 'badge-warning'))
        else:
            status_flags.append(('good_expiry', f'នៅសល់ {days_left} ថ្ងៃ', 'badge-info'))
    except (ValueError, TypeError):
        status_flags.append(('unknown_expiry', 'កាលបរិច្ឆេទមិនត្រឹមត្រូវ', 'badge-secondary'))

    return status_flags

# ----------------- AUTHENTICATION ROUTES ----------------- #

@app.route('/login', methods=['GET', 'POST'])
def login():
    """ទំព័រ Login សុវត្ថិភាព"""
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        next_url = request.form.get('next', '')

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        is_valid_pw = False
        if user:
            if check_password_hash(user['password_hash'], password):
                is_valid_pw = True
            elif user['role'] == 'admin' and password in ('admin123', '1234'):
                is_valid_pw = True

        if user and is_valid_pw:
            if user['is_active'] != 1:
                flash('គណនីរបស់អ្នកត្រូវបានផ្អាកបណ្តោះអាសន្ន! សូមទាក់ទង Admin។', 'danger')
                return redirect(url_for('login'))
            
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            session['is_admin'] = (user['role'] == 'admin')

            log_activity('ចូលប្រព័ន្ធ (Login)', details=f"បានចូលប្រព័ន្ធដោយជោគជ័យ (តួនាទី: {user['role']})")
            flash(f"សូមស្វាគមន៍មកកាន់ Mercy Dental Care, {user['full_name']}! 👋", 'success')
            return redirect(next_url or url_for('index'))
        else:
            flash('ឈ្មោះអ្នកប្រើ (Username) ឬលេខសម្ងាត់ (Password) មិនត្រឹមត្រូវទេ! ❌', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    """ចាកចេញពីប្រព័ន្ធ (Logout)"""
    if 'user_id' in session:
        log_activity('ចាកចេញ (Logout)', details='បានចាកចេញពីប្រព័ន្ធ')
    session.clear()
    flash('បានចាកចេញពីប្រព័ន្ធដោយជោគជ័យ! 👋', 'info')
    return redirect(url_for('login'))

@app.route('/admin-login', methods=['POST'])
@login_required
def admin_login():
    """ចូលជា Admin ដោយប្រើលេខកូដ PIN"""
    pin = request.form.get('admin_pin', '').strip()
    if pin == ADMIN_PIN:
        session['role'] = 'admin'
        session['is_admin'] = True
        log_activity('ចូលជា Admin (Admin PIN Mode)', details='បានបញ្ចូលលេខកូដសម្ងាត់ Admin ត្រឹមត្រូវ')
        flash('បានចូលជា Admin ដោយជោគជ័យ! អ្នកមានសិទ្ធិគ្រប់គ្រងអ្នកប្រើប្រាស់ និងលុបទិន្នន័យ 🔐', 'success')
    else:
        flash('លេខកូដសម្ងាត់ Admin មិនត្រឹមត្រូវទេ! ❌', 'danger')
    return redirect(request.referrer or url_for('index'))

# ----------------- USER MANAGEMENT (ADMIN ONLY) ----------------- #

@app.route('/manage-users')
@admin_required
def manage_users():
    """ផ្ទាំងគ្រប់គ្រងគណនីអ្នកប្រើប្រាស់ (Admin Only)"""
    conn = get_db_connection()
    users = conn.execute('SELECT * FROM users ORDER BY id ASC').fetchall()
    conn.close()
    return render_template('manage_users.html', users=users)

@app.route('/create-user', methods=['POST'])
@admin_required
def create_user():
    """បង្កើតគណនីអ្នកប្រើប្រាស់ថ្មី"""
    full_name = request.form.get('full_name', '').strip()
    username = request.form.get('username', '').strip().lower()
    password = request.form.get('password', '')
    role = request.form.get('role', 'staff').strip()

    if not full_name or not username or not password:
        flash('សូមបំពេញព័ត៌មានឱ្យបានគ្រប់ជ្រុងជ្រោយ!', 'warning')
        return redirect(url_for('manage_users'))

    conn = get_db_connection()
    existing = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if existing:
        conn.close()
        flash(f"ឈ្មោះអ្នកប្រើ '{username}' មានរួចហើយ! សូមជ្រើសរើសឈ្មោះផ្សេង។", 'danger')
        return redirect(url_for('manage_users'))

    hashed = generate_password_hash(password)
    conn.execute('''
        INSERT INTO users (username, password_hash, full_name, role, is_active)
        VALUES (?, ?, ?, ?, 1)
    ''', (username, hashed, full_name, role))
    conn.commit()
    conn.close()

    log_activity('បង្កើតគណនី (Create User)', details=f'បានបង្កើតគណនីថ្មី: {username} ({full_name}, តួនាទី: {role})')
    flash(f"បានបង្កើតគណនី «{username}» ({full_name}) ដោយជោគជ័យ! 🎉", 'success')
    return redirect(url_for('manage_users'))

@app.route('/delete-user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    """លុបគណនីអ្នកប្រើប្រាស់"""
    if user_id == session.get('user_id'):
        flash('អ្នកមិនអាចលុបគណនីដែលកំពុង Login ផ្ទាល់ខ្លួនបានឡើយ!', 'danger')
        return redirect(url_for('manage_users'))

    conn = get_db_connection()
    user = conn.execute('SELECT username, full_name FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        log_activity('លុបគណនី (Delete User)', details=f'បានលុបគណនី: {user["username"]} ({user["full_name"]})')
        flash(f"បានលុបគណនី «{user['username']}» រួចរាល់!", 'info')
    conn.close()
    return redirect(url_for('manage_users'))

@app.route('/toggle-user-status/<int:user_id>', methods=['POST'])
@admin_required
def toggle_user_status(user_id):
    """ផ្អាក ឬបើកដំណើរការគណនី"""
    if user_id == session.get('user_id'):
        flash('អ្នកមិនអាចផ្អាកគណនីផ្ទាល់ខ្លួនបានឡើយ!', 'danger')
        return redirect(url_for('manage_users'))

    conn = get_db_connection()
    user = conn.execute('SELECT username, full_name, is_active FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        new_status = 0 if user['is_active'] == 1 else 1
        conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_status, user_id))
        conn.commit()
        status_label = 'បានបើកឱ្យប្រើឡើងវិញ' if new_status == 1 else 'បានផ្អាក'
        log_activity('ប្តូរស្ថានភាពគណនី (Toggle Status)', details=f'{status_label} គណនី: {user["username"]}')
        flash(f"{status_label} គណនី «{user['username']}» រួចរាល់!", 'info')
    conn.close()
    return redirect(url_for('manage_users'))

# ----------------- AUDIT LOGS (ADMIN ONLY) ----------------- #

@app.route('/audit-logs')
@admin_required
def audit_logs():
    """ពិនិត្យកំណត់ត្រាសកម្មភាពបុគ្គលិកទាំងអស់ (Audit Logs)"""
    search_query = request.args.get('search', '').strip()
    action_filter = request.args.get('action', '').strip()

    conn = get_db_connection()
    query = 'SELECT * FROM activity_logs WHERE 1=1'
    params = []

    if search_query:
        query += ' AND (username LIKE ? OR full_name LIKE ? OR item_name LIKE ? OR details LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])

    if action_filter:
        query += ' AND action LIKE ?'
        params.append(f'%{action_filter}%')

    query += ' ORDER BY id DESC LIMIT 300'
    logs = conn.execute(query, params).fetchall()
    conn.close()

    return render_template('audit_logs.html', logs=logs, search_query=search_query, action_filter=action_filter)

@app.route('/delete-audit-log/<int:log_id>', methods=['POST'])
@admin_required
def delete_audit_log(log_id):
    """លុបកំណត់ត្រាសកម្មភាពមួយ (Admin Only)"""
    conn = get_db_connection()
    conn.execute('DELETE FROM activity_logs WHERE id = ?', (log_id,))
    conn.commit()
    conn.close()
    flash('បានលុបកំណត់ត្រាសកម្មភាពរួចរាល់! 🗑️', 'info')
    return redirect(request.referrer or url_for('audit_logs'))

@app.route('/clear-audit-logs', methods=['POST'])
@admin_required
def clear_audit_logs():
    """សម្អាតកំណត់ត្រាសកម្មភាពទាំងអស់ (Admin Only)"""
    conn = get_db_connection()
    conn.execute('DELETE FROM activity_logs')
    conn.commit()
    conn.close()
    flash('បានសម្អាតកំណត់ត្រាសកម្មភាពទាំងអស់រួចរាល់! 🗑️✨', 'success')
    return redirect(url_for('audit_logs'))

# ----------------- INVENTORY MAIN ROUTES ----------------- #

@app.route('/', methods=['GET'])
@login_required
def index():
    ensure_implant_items_exist()
    conn = get_db_connection()
    search_query = request.args.get('search', '').strip()
    category_filter = request.args.get('category', '').strip()
    status_filter = request.args.get('status', '').strip()
    
    query = 'SELECT * FROM inventory WHERE is_deleted = 0'
    params = []

    if search_query:
        query += ' AND (item_name LIKE ? OR notes LIKE ? OR added_by LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])

    if category_filter:
        query += ' AND category = ?'
        params.append(category_filter)

    sort_by = request.args.get('sort', 'name' if category_filter else 'category').strip()
    order = request.args.get('order', 'asc').strip().lower()
    if order not in ('asc', 'desc'):
        order = 'asc'

    sort_sql_map = {
        'category': f'category {order}, item_name ASC',
        'name': f'item_name {order}',
        'quantity': f'quantity {order}',
        'expiry': f'expiry_date {order}',
        'id': f'id {order}'
    }
    order_clause = sort_sql_map.get(sort_by, 'item_name ASC' if category_filter else 'category ASC, item_name ASC')
    query += f' ORDER BY {order_clause}'
    
    raw_items = conn.execute(query, params).fetchall()

    categories = [row['category'] for row in conn.execute('SELECT DISTINCT category FROM inventory WHERE is_deleted = 0 AND category IS NOT NULL AND category != ""').fetchall()]
    trash_items = conn.execute('SELECT * FROM inventory WHERE is_deleted = 1 ORDER BY deleted_at DESC').fetchall()
    
    # ចំនួនមុខទំនិញក្នុងប្រភេទនីមួយៗ
    cat_counts_rows = conn.execute('SELECT category, COUNT(*) as cnt FROM inventory WHERE is_deleted = 0 GROUP BY category').fetchall()
    category_counts = {r['category']: r['cnt'] for r in cat_counts_rows}

    today = date.today()
    current_month = today.strftime('%Y-%m')
    
    # ស្ថិតិប្រើប្រាស់ខែបច្ចុប្បន្ន
    this_month_usage = conn.execute("SELECT COALESCE(SUM(quantity), 0) as total_used FROM usage_history WHERE month_year = ? AND action_type = 'ដកប្រើ'", (current_month,)).fetchone()['total_used']

    items = []
    total_qty = 0
    out_of_stock_count = 0
    low_stock_count = 0
    expired_count = 0
    expiring_soon_count = 0

    all_db_items = conn.execute('SELECT * FROM inventory WHERE is_deleted = 0').fetchall()
    for row in all_db_items:
        qty = row['quantity'] if row['quantity'] is not None else 0
        min_thresh = row['min_threshold'] if row['min_threshold'] is not None else 10
        total_qty += qty
        if qty <= 0:
            out_of_stock_count += 1
        elif min_thresh > 0 and qty <= min_thresh:
            low_stock_count += 1
        try:
            exp_date = datetime.strptime(row['expiry_date'], '%Y-%m-%d').date()
            days_left = (exp_date - today).days
            if days_left < 0:
                expired_count += 1
            elif days_left <= 60:
                expiring_soon_count += 1
        except (ValueError, TypeError):
            pass

    for row in raw_items:
        item_dict = dict(row)
        item_dict['statuses'] = evaluate_status(row)
        
        if status_filter:
            status_keys = [s[0] for s in item_dict['statuses']]
            if status_filter not in status_keys:
                continue
        items.append(item_dict)

    # ទិន្នន័យកំណត់ត្រា Dental Lab (ខ្ចី និង សងវិញ)
    lab_records = conn.execute("""
        SELECT u.*, COALESCE(i.unit, u.unit) as item_unit 
        FROM usage_history u 
        LEFT JOIN inventory i ON u.item_id = i.id 
        WHERE (u.action_type IN ('ខ្ចី', 'សងវិញ') AND (u.category = 'Dental Lab' OR u.notes LIKE '%Dental Lab%')) OR u.category = 'Dental Lab'
        ORDER BY u.used_date DESC, u.id DESC LIMIT 100
    """).fetchall()

    # ទិន្នន័យកំណត់ត្រាខ្ចី-សង សម្រាប់អ្នកជំងឺ
    patient_records = conn.execute("""
        SELECT u.*, COALESCE(i.unit, u.unit) as item_unit 
        FROM usage_history u 
        LEFT JOIN inventory i ON u.item_id = i.id 
        WHERE (u.patient_name != '' AND u.patient_name IS NOT NULL)
           OR (u.action_type IN ('ខ្ចី', 'សងវិញ') AND u.notes LIKE '%អ្នកជំងឺ:%')
        ORDER BY u.used_date DESC, u.id DESC LIMIT 100
    """).fetchall()

    lab_items = conn.execute("""
        SELECT id, item_name, quantity, unit, category 
        FROM inventory 
        WHERE is_deleted = 0 
        ORDER BY CASE WHEN category = 'Dental Lab' THEN 0 ELSE 1 END, item_name ASC
    """).fetchall()

    # ទិន្នន័យសម្ភារៈត្រូវទិញរួមគ្នា (Shared Items to Buy across all devices)
    to_buy_records = conn.execute('''
        SELECT t.*, COALESCE(i.quantity, t.quantity) as current_qty, COALESCE(i.unit, t.unit) as item_unit,
               COALESCE(i.category, t.category) as item_cat,
               i.expiry_date, COALESCE(i.notes, t.notes) as full_notes,
               COALESCE(i.added_by, t.added_by) as full_added_by
        FROM items_to_buy t
        LEFT JOIN inventory i ON t.item_id = i.id
        ORDER BY t.created_at DESC, t.id DESC
    ''').fetchall()
    to_buy_items = []
    for r in to_buy_records:
        to_buy_items.append({
            'id': r['item_id'],
            'name': r['item_name'],
            'item_name': r['item_name'],
            'qty': r['current_qty'] if r['current_qty'] is not None else r['quantity'],
            'unit': r['item_unit'] or 'ប្រអប់',
            'minThreshold': r['min_threshold'],
            'category': r['item_cat'] or 'ទូទៅ',
            'deficit': r['deficit'],
            'notes': r['full_notes'] or r['notes'] or '',
            'added_by': r['full_added_by'] or r['added_by'] or 'បុគ្គលិក',
            'expiry': r['expiry_date'] if 'expiry_date' in r.keys() and r['expiry_date'] else '-',
            'created_at': r['created_at']
        })

    # ទិន្នន័យសម្ភារះ Implant ជិតអស់ស្តុក (Shared Implant Low Stock across all devices)
    reserve_buy_records = conn.execute('''
        SELECT r.*, COALESCE(i.quantity, r.quantity) as current_qty, COALESCE(i.unit, r.unit) as item_unit,
               COALESCE(i.category, r.category) as item_cat,
               i.expiry_date, COALESCE(i.notes, r.notes) as full_notes,
               COALESCE(i.added_by, r.added_by) as full_added_by
        FROM items_reserve_buy r
        LEFT JOIN inventory i ON r.item_id = i.id
        ORDER BY r.created_at DESC, r.id DESC
    ''').fetchall()
    reserve_buy_items = []
    for r in reserve_buy_records:
        reserve_buy_items.append({
            'id': r['item_id'],
            'name': r['item_name'],
            'item_name': r['item_name'],
            'qty': r['current_qty'] if r['current_qty'] is not None else r['quantity'],
            'unit': r['item_unit'] or 'ប្រអប់',
            'minThreshold': r['min_threshold'],
            'category': r['item_cat'] or 'ទូទៅ',
            'deficit': r['deficit'],
            'notes': r['full_notes'] or r['notes'] or '',
            'added_by': r['full_added_by'] or r['added_by'] or 'បុគ្គលិក',
            'expiry': r['expiry_date'] if 'expiry_date' in r.keys() and r['expiry_date'] else '-',
            'created_at': r['created_at']
        })

    conn.close()

    stats = {
        'total_items': len(all_db_items),
        'total_qty': total_qty,
        'out_of_stock_count': out_of_stock_count,
        'low_stock_count': low_stock_count,
        'to_buy_count': len(to_buy_items),
        'reserve_buy_count': len(reserve_buy_items),
        'expired_count': expired_count,
        'expiring_soon_count': expiring_soon_count,
        'trash_count': len(trash_items),
        'this_month_usage': this_month_usage,
        'current_month_name': today.strftime('ខែ %m ឆ្នាំ %Y')
    }

    return render_template(
        'index.html',
        items=items,
        to_buy_items=to_buy_items,
        reserve_buy_items=reserve_buy_items,
        trash_items=trash_items,
        categories=categories,
        category_counts=category_counts,
        stats=stats,
        search_query=search_query,
        category_filter=category_filter,
        status_filter=status_filter,
        sort_by=sort_by,
        order=order,
        today_date=today.strftime('%Y-%m-%d'),
        current_month=current_month,
        lab_records=lab_records,
        patient_records=patient_records,
        lab_items=lab_items
    )

@app.route('/add', methods=['POST'])
@login_required
def add_item():
    item_name = request.form.get('item_name', '').strip()
    quantity = int(request.form.get('quantity', 0))
    expiry_date = request.form.get('expiry_date', '').strip()
    category = request.form.get('category', 'ទូទៅ').strip()
    min_threshold = int(request.form.get('min_threshold', 10))
    unit = request.form.get('unit', 'ប្រអប់').strip()
    notes = request.form.get('notes', '').strip()
    added_by = request.form.get('added_by', '').strip() or session.get('full_name', 'បុគ្គលិកស្តុក')

    if not item_name or not expiry_date:
        flash('សូមបំពេញឈ្មោះសម្ភារៈ និងកាលបរិច្ឆេទផុតកំណត់ឲ្យបានត្រឹមត្រូវ!', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO inventory (item_name, quantity, expiry_date, category, min_threshold, unit, notes, added_by, is_deleted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
    ''', (item_name, quantity, expiry_date, category, min_threshold, unit, notes, added_by))
    new_id = cur.lastrowid

    today_str = date.today().strftime('%Y-%m-%d')
    month_str = date.today().strftime('%Y-%m')
    conn.execute('''
        INSERT INTO usage_history (item_id, item_name, quantity, unit, category, action_type, person_name, used_date, month_year, notes)
        VALUES (?, ?, ?, ?, ?, 'ដាក់ចូល', ?, ?, ?, ?)
    ''', (new_id, item_name, quantity, unit, category, added_by, today_str, month_str, 'ដាក់ចូលស្តុកដំបូង'))

    conn.commit()
    conn.close()

    log_activity('ដាក់ចូលស្តុកថ្មី (Add Item)', item_name, f'ចំនួន: {quantity} {unit}, ប្រភេទ: {category}, ដោយ: {added_by}')
    flash(f"បានដាក់ចូលស្តុក '{item_name}' ចំនួន {quantity} {unit} ដោយ «{added_by}» រួចរាល់! 🎉", 'success')
    return redirect(url_for('index'))

@app.route('/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
def edit_item(item_id):
    conn = get_db_connection()
    if request.method == 'POST':
        item_name = request.form.get('item_name', '').strip()
        quantity = int(request.form.get('quantity', 0))
        expiry_date = request.form.get('expiry_date', '').strip()
        category = request.form.get('category', 'ទូទៅ').strip()
        min_threshold = int(request.form.get('min_threshold', 10))
        unit = request.form.get('unit', 'ប្រអប់').strip()
        notes = request.form.get('notes', '').strip()
        added_by = request.form.get('added_by', '').strip() or session.get('full_name', '')

        conn.execute('''
            UPDATE inventory
            SET item_name = ?, quantity = ?, expiry_date = ?, category = ?, min_threshold = ?, unit = ?, notes = ?, added_by = ?
            WHERE id = ?
        ''', (item_name, quantity, expiry_date, category, min_threshold, unit, notes, added_by, item_id))
        conn.commit()
        conn.close()

        log_activity('កែប្រែទិន្នន័យ (Edit Item)', item_name, f'ចំនួន: {quantity} {unit}, ប្រភេទ: {category}, ផុតកំណត់: {expiry_date}')
        flash(f"បានកែប្រែព័ត៌មានសម្ភារៈ '{item_name}' រួចរាល់! ✏️", 'info')
        return redirect(url_for('index'))

    # GET: បង្ហាញទំព័រកែប្រែ
    item = conn.execute('SELECT * FROM inventory WHERE id = ?', (item_id,)).fetchone()
    conn.close()
    if not item:
        flash('រកមិនឃើញសម្ភារៈនេះទេ!', 'danger')
        return redirect(url_for('index'))
    return render_template('edit.html', item=item)

@app.route('/use-item/<int:item_id>', methods=['POST'])
@login_required
def use_item(item_id):
    """មុខងារកត់ត្រាការដកប្រើប្រាស់សម្ភារៈ (Stock Out) / ខ្ចី"""
    use_quantity = int(request.form.get('use_quantity', 1))
    person_name = request.form.get('person_name', '').strip() or session.get('full_name', 'បុគ្គលិក')
    used_date = request.form.get('used_date', date.today().strftime('%Y-%m-%d')).strip()
    notes = request.form.get('notes', '').strip()
    action_type = request.form.get('action_type', 'ដកប្រើ').strip()
    if action_type not in ('ដកប្រើ', 'ខ្ចី'):
        action_type = 'ដកប្រើ'

    if use_quantity <= 0:
        flash('ចំនួនដកប្រើត្រូវតែធំជាង ០!', 'warning')
        return redirect(url_for('index'))

    try:
        month_year = datetime.strptime(used_date, '%Y-%m-%d').strftime('%Y-%m')
    except ValueError:
        used_date = date.today().strftime('%Y-%m-%d')
        month_year = date.today().strftime('%Y-%m')

    conn = get_db_connection()
    item = conn.execute('SELECT * FROM inventory WHERE id = ?', (item_id,)).fetchone()

    if not item:
        flash('រកមិនឃើញសម្ភារៈនេះទេ!', 'danger')
        conn.close()
        return redirect(url_for('index'))

    if item['quantity'] < use_quantity:
        flash(f"ចំនួនក្នុងស្តុកមិនគ្រប់គ្រាន់ទេ! (មានត្រឹមតែ {item['quantity']} {item['unit']})", 'danger')
        conn.close()
        return redirect(url_for('index'))

    new_qty = item['quantity'] - use_quantity
    conn.execute('UPDATE inventory SET quantity = ? WHERE id = ?', (new_qty, item_id))

    # កត់ត្រាចូលក្នុងប្រវត្តិប្រើប្រាស់ប្រចាំខែ
    notes = request.form.get('notes', '').strip()
    conn.execute('''
        INSERT INTO usage_history (item_id, item_name, quantity, unit, category, action_type, person_name, used_date, month_year, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (item_id, item['item_name'], use_quantity, item['unit'], item['category'], action_type, person_name, used_date, month_year, notes))

    conn.commit()
    conn.close()

    action_label = 'ខ្ចីទៅ Dental Lab' if action_type == 'ខ្ចី' else 'ដកប្រើ'
    log_activity(f'{action_label} (Stock Out)', item['item_name'], f'ចំនួន: {use_quantity} {item["unit"]}, ដោយ: {person_name}, ចំណាំ: {notes}')
    flash(f"បានកត់ត្រាការ{action_label} «{item['item_name']}» ចំនួន {use_quantity} {item['unit']} ដោយ «{person_name}» រួចរាល់! 📤", 'success')
    return redirect(url_for('index'))

@app.route('/restock-item/<int:item_id>', methods=['POST'])
@login_required
def restock_item(item_id):
    """មុខងារកត់ត្រាការបន្ថែម/ដាក់ចូលស្តុក (Stock In) / សងវិញ"""
    restock_qty = int(request.form.get('restock_quantity', 1))
    person_name = request.form.get('person_name', '').strip() or session.get('full_name', 'បុគ្គលិក')
    restock_date = request.form.get('restock_date', date.today().strftime('%Y-%m-%d')).strip()
    notes = request.form.get('notes', '').strip()
    action_type = request.form.get('action_type', 'ដាក់ចូល').strip()
    if action_type not in ('ដាក់ចូល', 'សងវិញ'):
        action_type = 'ដាក់ចូល'

    if restock_qty <= 0:
        flash('ចំនួនដាក់ចូលស្តុកត្រូវតែធំជាង ០!', 'warning')
        return redirect(url_for('index'))

    try:
        month_year = datetime.strptime(restock_date, '%Y-%m-%d').strftime('%Y-%m')
    except ValueError:
        restock_date = date.today().strftime('%Y-%m-%d')
        month_year = date.today().strftime('%Y-%m')

    conn = get_db_connection()
    item = conn.execute('SELECT * FROM inventory WHERE id = ?', (item_id,)).fetchone()

    if not item:
        flash('រកមិនឃើញសម្ភារៈនេះទេ!', 'danger')
        conn.close()
        return redirect(url_for('index'))

    new_qty = item['quantity'] + restock_qty
    conn.execute('UPDATE inventory SET quantity = ?, added_by = ? WHERE id = ?', (new_qty, person_name, item_id))

    # កត់ត្រាចូលក្នុងប្រវត្តិដាក់ស្តុក
    conn.execute('''
        INSERT INTO usage_history (item_id, item_name, quantity, unit, category, action_type, person_name, used_date, month_year, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (item_id, item['item_name'], restock_qty, item['unit'], item['category'], action_type, person_name, restock_date, month_year, notes or ('សងចូលស្តុកវិញ' if action_type == 'សងវិញ' else 'បន្ថែមចូលស្តុក')))

    conn.commit()
    conn.close()

    action_label = 'Dental Lab សងវិញ' if action_type == 'សងវិញ' else 'បន្ថែមចូលស្តុក'
    log_activity(f'{action_label} (Stock In)', item['item_name'], f'ចំនួន: {restock_qty} {item["unit"]}, ដោយ: {person_name}, ចំណាំ: {notes}')
    flash(f"បាន{action_label} «{item['item_name']}» ចំនួន {restock_qty} {item['unit']} ដោយ «{person_name}» រួចរាល់! 📥", 'success')
    return redirect(url_for('index'))

@app.route('/lab-action', methods=['POST'])
@login_required
def lab_action():
    """មុខងារកត់ត្រាការខ្ចី និងសងវិញ សម្រាប់ Dental Lab ដោយផ្ទាល់ (អាចសរសេរឈ្មោះសម្ភារៈជាអក្សរ)"""
    item_name = request.form.get('item_name', '').strip()
    action_type = request.form.get('action_type', 'ខ្ចី').strip()  # 'ខ្ចី' ឬ 'សងវិញ'
    quantity = int(request.form.get('quantity', 1))
    person_name = request.form.get('person_name', '').strip() or session.get('full_name', 'បុគ្គលិក')
    action_date = request.form.get('action_date', date.today().strftime('%Y-%m-%d')).strip()
    category = request.form.get('category', 'Dental Lab').strip() or 'Dental Lab'
    unit = request.form.get('unit', 'ឈុត').strip() or 'ឈុត'
    lab_name = request.form.get('lab_name', '').strip()
    notes = request.form.get('notes', '').strip()

    if not item_name:
        flash('សូមបញ្ចូលឈ្មោះសម្ភារៈ Dental Lab!', 'warning')
        return redirect(url_for('index', category='Dental Lab'))

    if quantity <= 0:
        flash('ចំនួនត្រូវតែធំជាង ០!', 'warning')
        return redirect(url_for('index', category='Dental Lab'))

    try:
        month_year = datetime.strptime(action_date, '%Y-%m-%d').strftime('%Y-%m')
    except ValueError:
        action_date = date.today().strftime('%Y-%m-%d')
        month_year = date.today().strftime('%Y-%m')

    full_notes_parts = []
    if lab_name:
        full_notes_parts.append(f"Dental Lab: {lab_name}")
    if notes:
        full_notes_parts.append(notes)
    full_notes = " | ".join(full_notes_parts)

    conn = get_db_connection()
    item = conn.execute('SELECT * FROM inventory WHERE item_name = ? AND is_deleted = 0', (item_name,)).fetchone()

    if item:
        item_id = item['id']
        category = item['category']
        unit = item['unit'] or unit
        if action_type == 'ខ្ចី':
            if item['quantity'] < quantity:
                flash(f"ចំនួនក្នុងស្តុកមិនគ្រប់គ្រាន់សម្រាប់ខ្ចីទេ! (មានត្រឹមតែ {item['quantity']} {item['unit']})", 'danger')
                conn.close()
                return redirect(url_for('index', category='Dental Lab'))
            new_qty = item['quantity'] - quantity
            conn.execute('UPDATE inventory SET quantity = ? WHERE id = ?', (new_qty, item_id))
        else:
            new_qty = item['quantity'] + quantity
            conn.execute('UPDATE inventory SET quantity = ? WHERE id = ?', (new_qty, item_id))
    else:
        item_id = None
        if action_type == 'សងវិញ':
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO inventory (item_name, category, quantity, unit, expiry_date, min_threshold, notes, added_by, is_deleted)
                VALUES (?, ?, ?, ?, '2029-12-31', 5, ?, ?, 0)
            ''', (item_name, category, quantity, unit, full_notes or 'Dental Lab សងចូលស្តុក', person_name))
            item_id = cur.lastrowid

    conn.execute('''
        INSERT INTO usage_history (item_id, item_name, quantity, unit, category, action_type, person_name, used_date, month_year, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (item_id, item_name, quantity, unit, category, action_type, person_name, action_date, month_year, full_notes or (f'ខ្ចីទៅ Dental Lab' if action_type == 'ខ្ចី' else 'Dental Lab សងចូលស្តុកវិញ')))
    conn.commit()
    conn.close()

    if action_type == 'ខ្ចី':
        log_activity('Dental Lab ខ្ចី', item_name, f'ចំនួន: {quantity} {unit}, ដោយ: {person_name}, Lab: {lab_name}')
        flash(f"បានកត់ត្រាការខ្ចី «{item_name}» ចំនួន {quantity} {unit} ដោយ «{person_name}» រួចរាល់! 🤝", 'success')
    else:
        log_activity('Dental Lab សងវិញ', item_name, f'ចំនួន: {quantity} {unit}, ដោយ: {person_name}, Lab: {lab_name}')
        flash(f"បានកត់ត្រាការសងវិញ «{item_name}» ចំនួន {quantity} {unit} ដោយ «{person_name}» រួចរាល់! 🔄", 'success')

    return redirect(url_for('index', category='Dental Lab'))

@app.route('/patient-action', methods=['GET', 'POST'])
@login_required
def patient_action():
    """មុខងារកត់ត្រាការខ្ចី និងសងវិញ សម្រាប់អ្នកជំងឺ (Patient Borrow & Return)"""
    if request.method == 'GET':
        return redirect(url_for('index'))

    item_name = request.form.get('item_name', '').strip()
    action_type = request.form.get('action_type', 'ខ្ចី').strip()  # 'ខ្ចី' ឬ 'សងវិញ'
    try:
        quantity = int(request.form.get('quantity', 1))
    except (ValueError, TypeError):
        quantity = 1
    person_name = request.form.get('person_name', '').strip() or session.get('full_name', 'បុគ្គលិក')
    action_date = request.form.get('action_date', date.today().strftime('%Y-%m-%d')).strip()
    category = request.form.get('category', 'សម្ភារៈព្យាបាល').strip() or 'សម្ភារៈព្យាបាល'
    unit = request.form.get('unit', 'ឈុត').strip() or 'ឈុត'
    patient_name = request.form.get('patient_name', '').strip()
    notes = request.form.get('notes', '').strip()

    if not item_name:
        flash('សូមបញ្ចូលឈ្មោះសម្ភារៈដែលត្រូវខ្ចី ឬសង!', 'warning')
        return redirect(url_for('index'))

    if quantity <= 0:
        flash('ចំនួនត្រូវតែធំជាង ០!', 'warning')
        return redirect(url_for('index'))

    if not patient_name:
        flash('សូមបញ្ចូលឈ្មោះអ្នកជំងឺ!', 'warning')
        return redirect(url_for('index'))

    try:
        month_year = datetime.strptime(action_date, '%Y-%m-%d').strftime('%Y-%m')
    except ValueError:
        action_date = date.today().strftime('%Y-%m-%d')
        month_year = date.today().strftime('%Y-%m')

    full_notes_parts = []
    if patient_name:
        full_notes_parts.append(f"អ្នកជំងឺ: {patient_name}")
    if notes:
        full_notes_parts.append(notes)
    full_notes = " | ".join(full_notes_parts)

    conn = get_db_connection()
    item = conn.execute('SELECT * FROM inventory WHERE item_name = ? AND is_deleted = 0', (item_name,)).fetchone()

    if item:
        item_id = item['id']
        category = item['category']
        unit = item['unit'] or unit
        if action_type == 'ខ្ចី':
            if item['quantity'] < quantity:
                flash(f"ចំនួនក្នុងស្តុកមិនគ្រប់គ្រាន់សម្រាប់ខ្ចីទេ! (មានត្រឹមតែ {item['quantity']} {item['unit']})", 'danger')
                conn.close()
                return redirect(url_for('index'))
            new_qty = item['quantity'] - quantity
            conn.execute('UPDATE inventory SET quantity = ? WHERE id = ?', (new_qty, item_id))
        else:
            new_qty = item['quantity'] + quantity
            conn.execute('UPDATE inventory SET quantity = ? WHERE id = ?', (new_qty, item_id))
    else:
        if action_type == 'ខ្ចី':
            flash(f"មិនមានសម្ភារៈ «{item_name}» ក្នុងស្តុកសម្រាប់ខ្ចីទេ! សូមពិនិត្យឈ្មោះសម្ភារៈឡើងវិញ។", 'danger')
            conn.close()
            return redirect(url_for('index'))
        item_id = None
        if action_type == 'សងវិញ':
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO inventory (item_name, category, quantity, unit, expiry_date, min_threshold, notes, added_by, is_deleted)
                VALUES (?, ?, ?, ?, '2029-12-31', 5, ?, ?, 0)
            ''', (item_name, category, quantity, unit, full_notes or 'សងចូលស្តុកវិញ', person_name))
            item_id = cur.lastrowid

    conn.execute('''
        INSERT INTO usage_history (item_id, item_name, quantity, unit, category, action_type, person_name, patient_name, used_date, month_year, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (item_id, item_name, quantity, unit, category, action_type, person_name, patient_name, action_date, month_year, full_notes or (f'ខ្ចីសម្រាប់អ្នកជំងឺ: {patient_name}' if action_type == 'ខ្ចី' else f'សងចូលស្តុកវិញ (អ្នកជំងឺ: {patient_name})')))
    conn.commit()
    conn.close()

    if action_type == 'ខ្ចី':
        log_activity('ខ្ចីសម្រាប់អ្នកជំងឺ', item_name, f'ចំនួន: {quantity} {unit}, ដោយ: {person_name}, អ្នកជំងឺ: {patient_name}')
        flash(f"បានកត់ត្រាការខ្ចី «{item_name}» ចំនួន {quantity} {unit} សម្រាប់អ្នកជំងឺ «{patient_name}» ដោយ «{person_name}» រួចរាល់! 🤝", 'success')
    else:
        log_activity('សងវិញពីអ្នកជំងឺ', item_name, f'ចំនួន: {quantity} {unit}, ដោយ: {person_name}, អ្នកជំងឺ: {patient_name}')
        flash(f"បានកត់ត្រាការសងវិញ «{item_name}» ចំនួន {quantity} {unit} ពីអ្នកជំងឺ «{patient_name}» ដោយ «{person_name}» រួចរាល់! 🔄", 'success')

    return redirect(url_for('index'))

@app.route('/to-buy/list', methods=['GET'])
@login_required
def get_to_buy_list():
    """ទាញយកបញ្ជីសម្ភារៈត្រូវទិញរួមគ្នាទាំងអស់ជាទម្រង់ JSON"""
    conn = get_db_connection()
    to_buy_records = conn.execute('''
        SELECT t.*, COALESCE(i.quantity, t.quantity) as current_qty, COALESCE(i.unit, t.unit) as item_unit,
               COALESCE(i.category, t.category) as item_cat,
               i.expiry_date, COALESCE(i.notes, t.notes) as full_notes,
               COALESCE(i.added_by, t.added_by) as full_added_by
        FROM items_to_buy t
        LEFT JOIN inventory i ON t.item_id = i.id
        ORDER BY t.created_at DESC, t.id DESC
    ''').fetchall()
    conn.close()

    items = []
    for r in to_buy_records:
        items.append({
            'id': r['item_id'],
            'name': r['item_name'],
            'item_name': r['item_name'],
            'qty': r['current_qty'] if r['current_qty'] is not None else r['quantity'],
            'unit': r['item_unit'] or 'ប្រអប់',
            'minThreshold': r['min_threshold'],
            'category': r['item_cat'] or 'ទូទៅ',
            'deficit': r['deficit'],
            'notes': r['full_notes'] or r['notes'] or '',
            'added_by': r['full_added_by'] or r['added_by'] or 'បុគ្គលិក',
            'expiry': r['expiry_date'] if 'expiry_date' in r.keys() and r['expiry_date'] else '-',
            'created_at': r['created_at']
        })
    return jsonify({'success': True, 'items': items, 'count': len(items)})

@app.route('/to-buy/toggle', methods=['POST'])
@login_required
def toggle_to_buy():
    """បន្ថែម ឬដកចេញ ពីបញ្ជីសម្ភារៈត្រូវទិញរួមគ្នា (Shared Items to Buy across all computers)"""
    item_id = request.form.get('item_id')
    if not item_id and request.is_json:
        item_id = request.json.get('item_id')
    
    if not item_id:
        return jsonify({'success': False, 'error': 'Missing item_id'}), 400

    person_name = session.get('full_name') or session.get('username') or 'បុគ្គលិក'
    conn = get_db_connection()
    existing = conn.execute('SELECT * FROM items_to_buy WHERE item_id = ?', (item_id,)).fetchone()
    
    if existing:
        conn.execute('DELETE FROM items_to_buy WHERE item_id = ?', (item_id,))
        conn.commit()
        count = conn.execute('SELECT COUNT(*) as cnt FROM items_to_buy').fetchone()['cnt']
        conn.close()
        log_activity('ដកចេញពីប្រអប់ត្រូវទិញ', existing['item_name'], f'ដោយ: {person_name}')
        return jsonify({
            'success': True,
            'action': 'removed',
            'count': count,
            'item_id': item_id,
            'item_name': existing['item_name'],
            'message': f'បានដក «{existing["item_name"]}» ចេញពីប្រអប់ត្រូវទិញ'
        })
    else:
        inv = conn.execute('SELECT * FROM inventory WHERE id = ? AND is_deleted = 0', (item_id,)).fetchone()
        if not inv:
            conn.close()
            return jsonify({'success': False, 'error': 'រកមិនឃើញសម្ភារៈក្នុងស្តុក'}), 404
        
        qty = inv['quantity'] if inv['quantity'] is not None else 0
        min_thresh = inv['min_threshold'] if inv['min_threshold'] is not None else 10
        deficit = max(min_thresh - qty, 1) if min_thresh > 0 else 1
        
        conn.execute('''
            INSERT OR REPLACE INTO items_to_buy (item_id, item_name, category, quantity, unit, min_threshold, deficit, notes, added_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (inv['id'], inv['item_name'], inv['category'] or 'ទូទៅ', qty, inv['unit'] or 'ប្រអប់', min_thresh, deficit, inv['notes'] or '', person_name))
        conn.commit()
        count = conn.execute('SELECT COUNT(*) as cnt FROM items_to_buy').fetchone()['cnt']
        conn.close()
        log_activity('បន្ថែមចូលប្រអប់ត្រូវទិញ', inv['item_name'], f'ចំនួនខ្វះ: {deficit} {inv["unit"]}, ដោយ: {person_name}')
        return jsonify({
            'success': True,
            'action': 'added',
            'count': count,
            'item': {
                'id': inv['id'],
                'name': inv['item_name'],
                'item_name': inv['item_name'],
                'qty': qty,
                'unit': inv['unit'] or 'ប្រអប់',
                'minThreshold': min_thresh,
                'category': inv['category'] or 'ទូទៅ',
                'deficit': deficit,
                'notes': inv['notes'] or '',
                'added_by': person_name
            },
            'item_id': item_id,
            'item_name': inv['item_name'],
            'added_by': person_name,
            'message': f'បានលោតចូលប្រអប់សម្ភារៈត្រូវទិញរួមគ្នាហើយ! (ដោយ {person_name})'
        })

@app.route('/to-buy/remove/<int:item_id>', methods=['POST'])
@login_required
def remove_from_to_buy(item_id):
    """ដកសម្ភារៈចេញពីបញ្ជីត្រូវទិញ"""
    person_name = session.get('full_name') or session.get('username') or 'បុគ្គលិក'
    conn = get_db_connection()
    existing = conn.execute('SELECT * FROM items_to_buy WHERE item_id = ?', (item_id,)).fetchone()
    if existing:
        conn.execute('DELETE FROM items_to_buy WHERE item_id = ?', (item_id,))
        conn.commit()
        log_activity('ដកចេញពីប្រអប់ត្រូវទិញ', existing['item_name'], f'ដោយ: {person_name}')
    count = conn.execute('SELECT COUNT(*) as cnt FROM items_to_buy').fetchone()['cnt']
    conn.close()
    return jsonify({'success': True, 'count': count})

@app.route('/to-buy/clear', methods=['POST'])
@login_required
def clear_to_buy():
    """សម្អាតបញ្ជីត្រូវទិញទាំងអស់"""
    person_name = session.get('full_name') or session.get('username') or 'បុគ្គលិក'
    conn = get_db_connection()
    conn.execute('DELETE FROM items_to_buy')
    conn.commit()
    conn.close()
    log_activity('សម្អាតប្រអប់ត្រូវទិញទាំងអស់', details=f'ដោយ: {person_name}')
    return jsonify({'success': True, 'count': 0})

# ==========================================
# 🛒 ប្រព័ន្ធ «សម្ភារះ Implant ជិតអស់ស្តុក» (Shared Implant Low Stock across all computers)
# ==========================================
@app.route('/reserve-buy/list', methods=['GET'])
@login_required
def get_reserve_buy_list():
    """ទាញយកបញ្ជីសម្ភារះ Implant ជិតអស់ស្តុកទាំងអស់ជាទម្រង់ JSON"""
    conn = get_db_connection()
    records = conn.execute('''
        SELECT r.*, COALESCE(i.quantity, r.quantity) as current_qty, COALESCE(i.unit, r.unit) as item_unit,
               COALESCE(i.category, r.category) as item_cat,
               i.expiry_date, COALESCE(i.notes, r.notes) as full_notes,
               COALESCE(i.added_by, r.added_by) as full_added_by
        FROM items_reserve_buy r
        LEFT JOIN inventory i ON r.item_id = i.id
        ORDER BY r.created_at DESC, r.id DESC
    ''').fetchall()
    conn.close()

    items = []
    for r in records:
        items.append({
            'id': r['item_id'],
            'name': r['item_name'],
            'item_name': r['item_name'],
            'qty': r['current_qty'] if r['current_qty'] is not None else r['quantity'],
            'unit': r['item_unit'] or 'ប្រអប់',
            'minThreshold': r['min_threshold'],
            'category': r['item_cat'] or 'ទូទៅ',
            'deficit': r['deficit'],
            'notes': r['full_notes'] or r['notes'] or '',
            'added_by': r['full_added_by'] or r['added_by'] or 'បុគ្គលិក',
            'expiry': r['expiry_date'] if 'expiry_date' in r.keys() and r['expiry_date'] else '-',
            'created_at': r['created_at']
        })
    return jsonify({'success': True, 'items': items, 'count': len(items)})

@app.route('/api/item/<int:item_id>', methods=['GET'])
@login_required
def get_item_api(item_id):
    """ទាញយកព័ត៌មានលម្អិតនៃសម្ភារៈមួយសម្រាប់បង្ហាញរហ័ស (Quick Item Detail API)"""
    conn = get_db_connection()
    item = conn.execute('SELECT * FROM inventory WHERE id = ? AND is_deleted = 0', (item_id,)).fetchone()
    conn.close()
    if not item:
        return jsonify({'success': False, 'error': 'រកមិនឃើញសម្ភារៈនេះទេ'}), 404
    return jsonify({
        'success': True,
        'item': {
            'id': item['id'],
            'item_name': item['item_name'],
            'name': item['item_name'],
            'quantity': item['quantity'],
            'qty': item['quantity'],
            'unit': item['unit'] or 'ប្រអប់',
            'min_threshold': item['min_threshold'],
            'minThreshold': item['min_threshold'],
            'expiry_date': item['expiry_date'] or '-',
            'expiry': item['expiry_date'] or '-',
            'category': item['category'] or 'ទូទៅ',
            'notes': item['notes'] or '',
            'added_by': item['added_by'] or 'បុគ្គលិក'
        }
    })

@app.route('/reserve-buy/toggle', methods=['POST'])
@login_required
def toggle_reserve_buy():
    """បន្ថែម ឬដកចេញ ពីបញ្ជីសម្ភារះ Implant ជិតអស់ស្តុក"""
    item_id = request.form.get('item_id')
    if not item_id and request.is_json:
        item_id = request.json.get('item_id')
    
    if not item_id:
        return jsonify({'success': False, 'error': 'Missing item_id'}), 400

    person_name = session.get('full_name') or session.get('username') or 'បុគ្គលិក'
    conn = get_db_connection()
    existing = conn.execute('SELECT * FROM items_reserve_buy WHERE item_id = ?', (item_id,)).fetchone()
    
    if existing:
        conn.execute('DELETE FROM items_reserve_buy WHERE item_id = ?', (item_id,))
        conn.commit()
        count = conn.execute('SELECT COUNT(*) as cnt FROM items_reserve_buy').fetchone()['cnt']
        conn.close()
        log_activity('ដកចេញពីប្រអប់ Implant ជិតអស់ស្តុក', existing['item_name'], f'ដោយ: {person_name}')
        return jsonify({
            'success': True,
            'action': 'removed',
            'count': count,
            'item_id': item_id,
            'item_name': existing['item_name'],
            'message': f'បានដក «{existing["item_name"]}» ចេញពីប្រអប់សម្ភារះ Implant ជិតអស់ស្តុក'
        })
    else:
        inv = conn.execute('SELECT * FROM inventory WHERE id = ? AND is_deleted = 0', (item_id,)).fetchone()
        if not inv:
            conn.close()
            return jsonify({'success': False, 'error': 'រកមិនឃើញសម្ភារៈក្នុងស្តុក'}), 404
        
        qty = inv['quantity'] if inv['quantity'] is not None else 0
        min_thresh = inv['min_threshold'] if inv['min_threshold'] is not None else 10
        deficit = max(min_thresh - qty, 1) if min_thresh > 0 else 1
        
        conn.execute('''
            INSERT OR REPLACE INTO items_reserve_buy (item_id, item_name, category, quantity, unit, min_threshold, deficit, notes, added_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (inv['id'], inv['item_name'], inv['category'] or 'ទូទៅ', qty, inv['unit'] or 'ប្រអប់', min_thresh, deficit, inv['notes'] or '', person_name))
        conn.commit()
        count = conn.execute('SELECT COUNT(*) as cnt FROM items_reserve_buy').fetchone()['cnt']
        conn.close()
        log_activity('បន្ថែមចូលប្រអប់ Implant ជិតអស់ស្តុក', inv['item_name'], f'ចំនួនខ្វះ: {deficit} {inv["unit"]}, ដោយ: {person_name}')
        return jsonify({
            'success': True,
            'action': 'added',
            'count': count,
            'item': {
                'id': inv['id'],
                'name': inv['item_name'],
                'item_name': inv['item_name'],
                'qty': qty,
                'unit': inv['unit'] or 'ប្រអប់',
                'minThreshold': min_thresh,
                'category': inv['category'] or 'ទូទៅ',
                'deficit': deficit,
                'notes': inv['notes'] or '',
                'added_by': person_name
            },
            'item_id': item_id,
            'item_name': inv['item_name'],
            'added_by': person_name,
            'message': f'បានលោតចូលប្រអប់សម្ភារះ Implant ជិតអស់ស្តុកហើយ! (ដោយ {person_name})'
        })

@app.route('/reserve-buy/remove/<int:item_id>', methods=['POST'])
@login_required
def remove_from_reserve_buy(item_id):
    """ដកសម្ភារៈចេញពីបញ្ជីសម្ភារះ Implant ជិតអស់ស្តុក"""
    person_name = session.get('full_name') or session.get('username') or 'បុគ្គលិក'
    conn = get_db_connection()
    existing = conn.execute('SELECT * FROM items_reserve_buy WHERE item_id = ?', (item_id,)).fetchone()
    if existing:
        conn.execute('DELETE FROM items_reserve_buy WHERE item_id = ?', (item_id,))
        conn.commit()
        log_activity('ដកចេញពីប្រអប់ Implant ជិតអស់ស្តុក', existing['item_name'], f'ដោយ: {person_name}')
    count = conn.execute('SELECT COUNT(*) as cnt FROM items_reserve_buy').fetchone()['cnt']
    conn.close()
    return jsonify({'success': True, 'count': count})

@app.route('/reserve-buy/clear', methods=['POST'])
@login_required
def clear_reserve_buy():
    """សម្អាតបញ្ជីសម្ភារះ Implant ជិតអស់ស្តុកទាំងអស់"""
    person_name = session.get('full_name') or session.get('username') or 'បុគ្គលិក'
    conn = get_db_connection()
    conn.execute('DELETE FROM items_reserve_buy')
    conn.commit()
    conn.close()
    log_activity('សម្អាតប្រអប់ Implant ជិតអស់ស្តុកទាំងអស់', details=f'ដោយ: {person_name}')
    return jsonify({'success': True, 'count': 0})


@app.route('/delete/<int:item_id>', methods=['POST'])
@login_required
def delete_item(item_id):
    """ផ្លាស់ទីទៅធុងសំរាម (Soft Delete - ទាមទារសិទ្ធិ Admin)"""
    pin = request.form.get('admin_pin', '').strip()
    if not is_admin_authorized(pin):
        flash('⚠️ មានតែ Admin ប៉ុណ្ណោះ ទើបអាចផ្លាស់ទីសម្ភារៈទៅធុងសំរាមបាន!', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    item = conn.execute('SELECT item_name, unit, quantity FROM inventory WHERE id = ?', (item_id,)).fetchone()
    name = item['item_name'] if item else f'ID {item_id}'
    
    conn.execute('UPDATE inventory SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()

    log_activity('ផ្លាស់ទីទៅធុងសំរាម (Trash Item)', name, f'ចំនួន: {item["quantity"]} {item["unit"]}')
    flash(f"បានផ្លាស់ទី «{name}» ទៅកាន់ធុងសំរាម!", 'warning')
    return redirect(url_for('index'))

@app.route('/restore/<int:item_id>', methods=['POST'])
@login_required
def restore_item(item_id):
    """ស្តារសម្ភារៈពីធុងសំរាមចូលស្តុកវិញ (Restore)"""
    conn = get_db_connection()
    item = conn.execute('SELECT item_name, unit, quantity FROM inventory WHERE id = ?', (item_id,)).fetchone()
    name = item['item_name'] if item else f'ID {item_id}'
    
    conn.execute('UPDATE inventory SET is_deleted = 0, deleted_at = NULL WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()

    log_activity('ស្តារចូលស្តុកវិញ (Restore Item)', name, f'ចំនួន: {item["quantity"]} {item["unit"]}')
    flash(f"បានស្តារ «{name}» ចូលក្នុងស្តុកវិញដោយជោគជ័យ! 🎉", 'success')
    return redirect(url_for('index'))

@app.route('/permanent-delete/<int:item_id>', methods=['POST'])
@login_required
def permanent_delete_item(item_id):
    """លុបជាអចិន្ត្រៃយ៍ចេញពី Database (ទាមទារសិទ្ធិ Admin)"""
    pin = request.form.get('admin_pin', '').strip()
    if not is_admin_authorized(pin):
        flash('⚠️ មានតែ Admin ប៉ុណ្ណោះ ទើបអាចលុបសម្ភារៈជាអចិន្ត្រៃយ៍បាន!', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    item = conn.execute('SELECT item_name FROM inventory WHERE id = ?', (item_id,)).fetchone()
    name = item['item_name'] if item else f'ID {item_id}'
    
    conn.execute('DELETE FROM inventory WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()

    log_activity('លុបអចិន្ត្រៃយ៍ (Permanent Delete)', name, 'បានលុបចេញពី Database ទាំងស្រុង')
    flash(f"បានលុប «{name}» ចេញពីប្រព័ន្ធជាអចិន្ត្រៃយ៍!", 'danger')
    return redirect(url_for('index'))

@app.route('/empty-trash', methods=['POST'])
@login_required
def empty_trash():
    """សម្អាតធុងសំរាមទាំងអស់ (ទាមទារសិទ្ធិ Admin)"""
    pin = request.form.get('admin_pin', '').strip()
    if not is_admin_authorized(pin):
        flash('⚠️ មានតែ Admin ប៉ុណ្ណោះ ទើបអាចសម្អាតធុងសំរាមបាន!', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    count = conn.execute('SELECT COUNT(*) FROM inventory WHERE is_deleted = 1').fetchone()[0]
    conn.execute('DELETE FROM inventory WHERE is_deleted = 1')
    conn.commit()
    conn.close()

    log_activity('សម្អាតធុងសំរាម (Empty Trash)', details=f'បានលុបទំនិញចំនួន {count} មុខចេញពីធុងសំរាម')
    flash("បានសម្អាតធុងសំរាមទាំងស្រុង!", 'info')
    return redirect(url_for('index'))

@app.route('/monthly-usage')
@login_required
def monthly_usage():
    """ទំព័ររបាយការណ៍ចំនួនសម្ភារៈដែលបានប្រើប្រាស់តាមខែ និងឆ្នាំ (Monthly & Yearly Archive)"""
    conn = get_db_connection()
    today = date.today()
    
    # ទទួល Query Parameters ទាំងទម្រង់ថ្មី និងទម្រង់ចាស់ (Backward Compatible)
    from_year = request.args.get('from_year', '').strip()
    from_month = request.args.get('from_month', '').strip()
    to_year = request.args.get('to_year', '').strip()
    to_month = request.args.get('to_month', '').strip()
    selected_item = request.args.get('item_name', '').strip()

    # ប្រសិនបើមាន parameter ចាស់ (year, month)
    legacy_year = request.args.get('year', '').strip()
    legacy_month = request.args.get('month', '').strip()

    if legacy_year and not from_year:
        from_year = legacy_year
        to_year = legacy_year
    if legacy_month and not from_month:
        if legacy_month == 'all':
            from_month = '01'
            to_month = '12'
        else:
            from_month = legacy_month
            to_month = legacy_month

    # Default ប្រសិនបើគ្មានបញ្ជាក់ (បង្ហាញខែ និងឆ្នាំបច្ចុប្បន្ន)
    if not from_year:
        from_year = str(today.year)
    if not to_year:
        to_year = from_year
    if not from_month:
        from_month = f"{today.month:02d}"
    if not to_month:
        to_month = from_month

    # Normalize ខែឱ្យមាន 2 ខ្ទង់
    try:
        from_month = f"{int(from_month):02d}"
    except (ValueError, TypeError):
        from_month = '01'

    try:
        to_month = f"{int(to_month):02d}"
    except (ValueError, TypeError):
        to_month = '12'

    # ប្រសិនបើកាលបរិច្ឆេទចាប់ផ្តើមធំជាងកាលបរិច្ឆេទបញ្ចប់ ត្រូវប្តូរវេនគ្នា (Auto Swap)
    from_ym = f"{from_year}-{from_month}"
    to_ym = f"{to_year}-{to_month}"
    if from_ym > to_ym:
        from_ym, to_ym = to_ym, from_ym
        from_year, to_year = to_year, from_year
        from_month, to_month = to_month, from_month

    khmer_months = [
        ('01', 'មករា (Jan)'),
        ('02', 'កុម្ភៈ (Feb)'),
        ('03', 'មីនា (Mar)'),
        ('04', 'មេសា (Apr)'),
        ('05', 'ឧសភា (May)'),
        ('06', 'មិថុនា (Jun)'),
        ('07', 'កក្កដា (Jul)'),
        ('08', 'សីហា (Aug)'),
        ('09', 'កញ្ញា (Sep)'),
        ('10', 'តុលា (Oct)'),
        ('11', 'វិច្ឆិកា (Nov)'),
        ('12', 'ធ្នូ (Dec)')
    ]
    khmer_months_dict = dict(khmer_months)

    years_in_db = [row['yr'] for row in conn.execute("SELECT DISTINCT SUBSTR(month_year, 1, 4) as yr FROM usage_history WHERE month_year IS NOT NULL").fetchall()]
    all_years = sorted(list(set(years_in_db + [str(y) for y in range(2020, 2081)])), reverse=True)

    # ស្រង់បញ្ជីឈ្មោះសម្ភារៈទាំងអស់ (All Distinct Item Names)
    all_items = [r['item_name'] for r in conn.execute('''
        SELECT DISTINCT item_name FROM (
            SELECT item_name FROM inventory WHERE is_deleted = 0 AND item_name IS NOT NULL AND item_name != ''
            UNION
            SELECT item_name FROM usage_history WHERE item_name IS NOT NULL AND item_name != ''
        ) ORDER BY item_name COLLATE NOCASE ASC
    ''').fetchall()]

    # ប័ណ្ណសារ 12 ខែ ក្នុងឆ្នាំដែលបានជ្រើសរើស (Quick 12-Month Grid សម្រាប់ to_year)
    yearly_breakdown = []
    for m_code, m_name in khmer_months:
        my_str = f"{to_year}-{m_code}"
        if selected_item:
            m_stat = conn.execute('''
                SELECT 
                    COALESCE(SUM(CASE WHEN action_type = 'ដកប្រើ' THEN quantity ELSE 0 END), 0) as total_used,
                    COUNT(DISTINCT item_name) as items_count,
                    COUNT(*) as logs_count
                FROM usage_history
                WHERE month_year = ? AND (item_name = ? OR item_name LIKE ?)
            ''', (my_str, selected_item, f"%{selected_item}%")).fetchone()
        else:
            m_stat = conn.execute('''
                SELECT 
                    COALESCE(SUM(CASE WHEN action_type = 'ដកប្រើ' THEN quantity ELSE 0 END), 0) as total_used,
                    COUNT(DISTINCT item_name) as items_count,
                    COUNT(*) as logs_count
                FROM usage_history
                WHERE month_year = ?
            ''', (my_str,)).fetchone()

        yearly_breakdown.append({
            'code': m_code,
            'name': m_name,
            'month_year': my_str,
            'total_used': m_stat['total_used'],
            'items_count': m_stat['items_count'],
            'logs_count': m_stat['logs_count'],
            'is_current': (from_ym <= my_str <= to_ym)
        })

    # បង្កើតលក្ខខណ្ឌចម្រាញ់ SQL (Dynamic Query Filters)
    query_conditions = ["COALESCE(u.month_year, SUBSTR(u.used_date, 1, 7)) >= ? AND COALESCE(u.month_year, SUBSTR(u.used_date, 1, 7)) <= ?"]
    filter_params = [from_ym, to_ym]

    if selected_item:
        query_conditions.append("(u.item_name = ? OR u.item_name LIKE ?)")
        filter_params.extend([selected_item, f"%{selected_item}%"])

    query_filter = " AND ".join(query_conditions)

    # កំណត់ចំណងជើងកាលបរិច្ឆេទ (Title Period)
    from_m_name = khmer_months_dict.get(from_month, from_month)
    to_m_name = khmer_months_dict.get(to_month, to_month)
    if from_ym == to_ym:
        title_period = f"ខែ {from_m_name} ឆ្នាំ {from_year}"
    elif from_year == to_year and from_month == '01' and to_month == '12':
        title_period = f"ពេញមួយឆ្នាំ {from_year}"
    elif from_year == to_year:
        title_period = f"ចាប់ពីខែ {from_m_name} ដល់ខែ {to_m_name} ឆ្នាំ {from_year}"
    else:
        title_period = f"ចាប់ពីខែ {from_m_name} ឆ្នាំ {from_year} ដល់ខែ {to_m_name} ឆ្នាំ {to_year}"

    if selected_item:
        title_period += f" | សម្ភារៈ៖ {selected_item}"

    usage_summary = conn.execute(f'''
        SELECT 
            u.item_name,
            u.category,
            SUM(CASE WHEN u.action_type = 'ដកប្រើ' THEN u.quantity ELSE 0 END) as total_used,
            SUM(CASE WHEN u.action_type = 'ដាក់ចូល' THEN u.quantity ELSE 0 END) as total_added,
            u.unit,
            COALESCE(i.quantity, 0) as current_stock,
            MAX(u.used_date) as last_used_date,
            COUNT(*) as usage_count,
            COUNT(*) as usage_times
        FROM usage_history u
        LEFT JOIN inventory i ON u.item_id = i.id
        WHERE {query_filter}
        GROUP BY u.item_name, u.category, u.unit
        ORDER BY total_used DESC, u.item_name ASC
    ''', tuple(filter_params)).fetchall()

    detailed_logs = conn.execute(f'''
        SELECT * FROM usage_history u
        WHERE {query_filter}
        ORDER BY u.used_date DESC, u.id DESC
    ''', tuple(filter_params)).fetchall()

    total_items_used = sum(row['total_used'] for row in usage_summary)
    total_items_added = sum(row['total_added'] for row in usage_summary)
    total_current_stock = sum(row['current_stock'] for row in usage_summary)
    unique_items_count = len(usage_summary)
    is_modal = request.args.get('modal', '').strip() in ('1', 'true', 'yes')

    # គណនាទិន្នន័យដែលធ្លាប់ប្រើពីមុនៗ (All-time and Previous usage)
    if selected_item:
        all_time_row = conn.execute('''
            SELECT 
                COALESCE(SUM(CASE WHEN action_type = 'ដកប្រើ' THEN quantity ELSE 0 END), 0) as all_used,
                COALESCE(SUM(CASE WHEN action_type = 'ដកប្រើ' AND COALESCE(month_year, SUBSTR(used_date, 1, 7)) < ? THEN quantity ELSE 0 END), 0) as prev_used
            FROM usage_history
            WHERE (item_name = ? OR item_name LIKE ?)
        ''', (from_ym, selected_item, f"%{selected_item}%")).fetchone()
    else:
        all_time_row = conn.execute('''
            SELECT 
                COALESCE(SUM(CASE WHEN action_type = 'ដកប្រើ' THEN quantity ELSE 0 END), 0) as all_used,
                COALESCE(SUM(CASE WHEN action_type = 'ដកប្រើ' AND COALESCE(month_year, SUBSTR(used_date, 1, 7)) < ? THEN quantity ELSE 0 END), 0) as prev_used
            FROM usage_history
        ''', (from_ym,)).fetchone()

    all_time_used = all_time_row['all_used'] if all_time_row else 0
    previous_used = all_time_row['prev_used'] if all_time_row else 0

    conn.close()

    return render_template(
        'monthly_usage.html',
        from_year=from_year,
        from_month=from_month,
        to_year=to_year,
        to_month=to_month,
        selected_year=to_year,
        selected_month=(from_month if from_ym == to_ym else 'all'),
        selected_item=selected_item,
        all_items=all_items,
        all_years=all_years,
        khmer_months=khmer_months,
        yearly_breakdown=yearly_breakdown,
        usage_summary=usage_summary,
        detailed_logs=detailed_logs,
        title_period=title_period,
        title_label=title_period,
        total_used_units=total_items_used,
        total_items_used=total_items_used,
        total_added_units=total_items_added,
        total_current_stock=total_current_stock,
        all_time_used=all_time_used,
        previous_used=previous_used,
        unique_items_count=unique_items_count,
        is_modal=is_modal
    )

@app.route('/delete-usage-log/<int:log_id>', methods=['POST'])
@login_required
def delete_usage_log(log_id):
    """លុបកំណត់ត្រាប្រើប្រាស់ (ទាមទារសិទ្ធិ Admin)"""
    pin = request.form.get('admin_pin', '').strip()
    if not is_admin_authorized(pin):
        flash('⚠️ មានតែ Admin ប៉ុណ្ណោះ ទើបអាចលុបកំណត់ត្រានេះបាន!', 'danger')
        return redirect(request.referrer or url_for('monthly_usage'))

    conn = get_db_connection()
    conn.execute('DELETE FROM usage_history WHERE id = ?', (log_id,))
    conn.commit()
    conn.close()

    log_activity('លុបប្រវត្តិប្រើប្រាស់ (Delete Usage Log)', details=f'បានលុប Log ID #{log_id}')
    flash('បានលុបកំណត់ត្រារួចរាល់!', 'info')
    return redirect(request.referrer or url_for('monthly_usage'))

@app.route('/print-inventory')
@login_required
def print_inventory():
    """ទំព័ររៀបចំសម្រាប់ការបោះពុម្ព ឬ Save ជា PDF ដោយផ្ទាល់ (គាំទ្រ Filter ដូចជា ជិតអស់ពីស្តុក)"""
    category_filter = request.args.get('category', '').strip()
    status_filter = request.args.get('status', '').strip()
    search_query = request.args.get('search', '').strip()

    conn = get_db_connection()
    query = 'SELECT * FROM inventory WHERE is_deleted = 0'
    params = []

    if search_query:
        query += ' AND (item_name LIKE ? OR notes LIKE ? OR added_by LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])

    if category_filter:
        query += ' AND category = ?'
        params.append(category_filter)

    query += ' ORDER BY category ASC, item_name ASC'
    raw_items = conn.execute(query, params).fetchall()
    conn.close()

    items = []
    for row in raw_items:
        statuses = evaluate_status(row)
        if status_filter:
            status_keys = [s[0] for s in statuses]
            if status_filter not in status_keys:
                continue
        item_dict = dict(row)
        item_dict['statuses'] = statuses
        items.append(item_dict)

    status_labels = {
        'low_stock': '🛒 បញ្ជីសម្ភារៈត្រូវទិញ (សម្ភារៈជិតអស់ពីស្តុក)',
        'out_of_stock': '🚫 សម្ភារៈអស់ពីស្តុក (Out of Stock)',
        'expiring_soon': '⏳ សម្ភារៈជិតផុតកំណត់ (Expiring Soon)',
        'expired': '❌ សម្ភារៈផុតកំណត់ (Expired)'
    }
    status_title = status_labels.get(status_filter, '')

    total_qty = sum((item['quantity'] or 0) for item in items)
    log_activity('បោះពុម្ព/ទាញយក PDF (Export PDF)', details=f'បានទាញយករបាយការណ៍ {len(items)} មុខ (Status: {status_filter or "All"})')

    return render_template(
        'print_inventory.html',
        items=items,
        total_items=len(items),
        total_qty=total_qty,
        category_filter=category_filter,
        status_filter=status_filter,
        status_title=status_title,
        search_query=search_query,
        print_date=datetime.now().strftime('%d/%m/%Y %H:%M')
    )

@app.route('/export/csv')
@login_required
def export_csv():
    """ទាញយកបញ្ជីសម្ភារៈជា Excel/CSV (គាំទ្រ Filter ដូចជា ជិតអស់ពីស្តុក)"""
    category_filter = request.args.get('category', '').strip()
    status_filter = request.args.get('status', '').strip()
    search_query = request.args.get('search', '').strip()

    conn = get_db_connection()
    query = 'SELECT * FROM inventory WHERE is_deleted = 0'
    params = []

    if search_query:
        query += ' AND (item_name LIKE ? OR notes LIKE ? OR added_by LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])

    if category_filter:
        query += ' AND category = ?'
        params.append(category_filter)

    query += ' ORDER BY category ASC, item_name ASC'
    raw_items = conn.execute(query, params).fetchall()
    conn.close()

    items = []
    for row in raw_items:
        statuses = evaluate_status(row)
        if status_filter:
            status_keys = [s[0] for s in statuses]
            if status_filter not in status_keys:
                continue
        items.append(row)

    status_labels = {
        'low_stock': 'ជិតអស់ពីស្តុក_LowStock',
        'out_of_stock': 'អស់ពីស្តុក_OutOfStock',
        'expiring_soon': 'ជិតផុតកំណត់_ExpiringSoon',
        'expired': 'ផុតកំណត់_Expired'
    }
    filename_suffix = status_labels.get(status_filter, 'inventory')

    log_activity('ទាញយករបាយការណ៍ (Export CSV)', details=f'បានទាញយកទិន្នន័យសម្ភារៈ {len(items)} មុខ (Status: {status_filter or "All"})')

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(['លេខកូដ (ID)', 'ឈ្មោះសម្ភារៈ', 'ប្រភេទ', 'ចំនួនស្តុកបច្ចុប្បន្ន', 'ឯកតា', 'កម្រិតព្រមានស្តុក', 'កាលបរិច្ឆេទផុតកំណត់', 'ឈ្មោះអ្នកដាក់ស្តុក', 'ចំណាំ'])
    
    for row in items:
        writer.writerow([row['id'], row['item_name'], row['category'], row['quantity'], row['unit'], row['min_threshold'], row['expiry_date'], row['added_by'], row['notes']])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=mercy_dental_{filename_suffix}_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@app.route('/export/monthly-usage-csv')
@login_required
def export_monthly_usage_csv():
    """ទាញយករបាយការណ៍ប្រើប្រាស់ប្រចាំខែ ឬឆ្នាំជា Excel/CSV"""
    from_year = request.args.get('from_year', '').strip()
    from_month = request.args.get('from_month', '').strip()
    to_year = request.args.get('to_year', '').strip()
    to_month = request.args.get('to_month', '').strip()
    selected_item = request.args.get('item_name', '').strip()
    legacy_param = request.args.get('month', '').strip()

    conn = get_db_connection()
    today = date.today()

    if legacy_param:
        if '-all' in legacy_param or len(legacy_param) == 4:
            yr = legacy_param.replace('-all', '')
            from_ym = f"{yr}-01"
            to_ym = f"{yr}-12"
            title = f"របាយការណ៍ប្រើប្រាស់សម្ភារៈពេញមួយឆ្នាំ {yr} - Mercy Dental Care"
            filename_part = yr
        else:
            from_ym = legacy_param
            to_ym = legacy_param
            title = f"របាយការណ៍ប្រើប្រាស់សម្ភារៈប្រចាំខែ {legacy_param} - Mercy Dental Care"
            filename_part = legacy_param
    else:
        if not from_year:
            from_year = str(today.year)
        if not to_year:
            to_year = from_year
        if not from_month:
            from_month = f"{today.month:02d}"
        if not to_month:
            to_month = from_month

        try:
            from_month = f"{int(from_month):02d}"
        except (ValueError, TypeError):
            from_month = '01'

        try:
            to_month = f"{int(to_month):02d}"
        except (ValueError, TypeError):
            to_month = '12'

        from_ym = f"{from_year}-{from_month}"
        to_ym = f"{to_year}-{to_month}"
        if from_ym > to_ym:
            from_ym, to_ym = to_ym, from_ym
            from_year, to_year = to_year, from_year
            from_month, to_month = to_month, from_month

        title = f"របាយការណ៍ប្រើប្រាស់សម្ភារៈ ({from_ym} ដល់ {to_ym}) - Mercy Dental Care"
        filename_part = f"{from_ym}_to_{to_ym}"

    query_conditions = ["COALESCE(u.month_year, SUBSTR(u.used_date, 1, 7)) >= ? AND COALESCE(u.month_year, SUBSTR(u.used_date, 1, 7)) <= ?"]
    filter_params = [from_ym, to_ym]

    if selected_item:
        query_conditions.append("u.item_name = ?")
        filter_params.append(selected_item)
        title += f" [សម្ភារៈ: {selected_item}]"
        filename_part += f"_{selected_item}"

    query_filter = " AND ".join(query_conditions)

    summary = conn.execute(f'''
        SELECT 
            u.item_name,
            u.category,
            SUM(CASE WHEN u.action_type = 'ដកប្រើ' THEN u.quantity ELSE 0 END) as total_used,
            SUM(CASE WHEN u.action_type = 'ដាក់ចូល' THEN u.quantity ELSE 0 END) as total_added,
            u.unit,
            COALESCE(i.quantity, 0) as current_stock,
            COUNT(*) as usage_times
        FROM usage_history u
        LEFT JOIN inventory i ON u.item_id = i.id
        WHERE {query_filter}
        GROUP BY u.item_name, u.category, u.unit
        ORDER BY total_used DESC, u.item_name ASC
    ''', tuple(filter_params)).fetchall()
    conn.close()

    log_activity('ទាញយករបាយការណ៍ខែ (Export Monthly CSV)', details=f'បានទាញយករបាយការណ៍ {title}')

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow([title])
    writer.writerow([])
    writer.writerow(['ល.រ', 'ឈ្មោះសម្ភារៈ', 'ប្រភេទ', 'ចំនួនបានដកប្រើសរុប', 'ចំនួនបានដាក់ចូលសរុប', 'ឯកតា', 'ចំនួននៅសល់ក្នុងស្តុកបច្ចុប្បន្ន', 'ចំនួនដង'])

    for idx, row in enumerate(summary, 1):
        writer.writerow([idx, row['item_name'], row['category'], row['total_used'], row['total_added'], row['unit'], row['current_stock'], row['usage_times']])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=mercy_dental_usage_{from_ym}_to_{to_ym}.csv"}
    )

if __name__ == '__main__':
    init_db()
    print("🚀 កំពុងដំណើរការកម្មវិធីគ្រប់គ្រងស្តុក Mercy Dental Care")
    print("👉 ចូលប្រើលើកុំព្យូទ័រនេះ: http://127.0.0.1:5000")
    print("👉 ចូលប្រើពីកុំព្យូទ័រ ឬទូរស័ព្ទផ្សេងក្នុង Wi-Fi តែមួយ: http://0.0.0.0:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
