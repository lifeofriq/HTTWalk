import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import easyocr
import re

# === Flask Setup ===
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['DATABASE'] = 'steps.db'

# === Hardcoded admin credentials ===
ADMINS = {
    "admin1": "pass123",
    "admin2": "superuser",
    "admin3": "root123",
    "admin4": "manager",
    "admin5": "htteam"
}

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "replace_this_with_a_real_secret")

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

model_dir = os.path.join(os.path.dirname(__file__), 'models')
reader = easyocr.Reader(['en'], model_storage_directory=model_dir)


def get_db_connection():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

# === Database Initialization ===
with get_db_connection() as conn:
    conn.execute('''CREATE TABLE IF NOT EXISTS steps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        steps INTEGER,
                        filename TEXT,
                        created_at TEXT
                    )''')
    conn.commit()


# === Helper: Save Steps with “Keep Biggest per Day” Logic ===
def save_steps(name, steps, filename):
    conn = get_db_connection()
    today = datetime.now().strftime("%Y-%m-%d")

    existing = conn.execute('''
        SELECT id, steps FROM steps
        WHERE name = ? AND DATE(created_at) = ?
    ''', (name, today)).fetchone()

    if existing:
        if steps > existing['steps']:
            conn.execute('''
                UPDATE steps
                SET steps = ?, filename = ?, created_at = ?
                WHERE id = ?
            ''', (steps, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), existing['id']))
            print(f"🔄 Updated {name}'s steps for {today} → {steps}")
        else:
            print(f"⚠️ Ignored smaller steps ({steps}) for {name} on {today}")
    else:
        conn.execute('''
            INSERT INTO steps (name, steps, filename, created_at)
            VALUES (?, ?, ?, ?)
        ''', (name, steps, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        print(f"✅ Added new record for {name} ({steps} steps)")

    conn.commit()
    conn.close()


# === ROUTE: Main Page ===
@app.route('/')
def index():
    user = request.args.get('user', '')
    page = int(request.args.get('page', 1))
    per_page = 10
    offset = (page - 1) * per_page

    conn = get_db_connection()

    # Get total number of leaderboard entries
    total_count = conn.execute('''
        SELECT COUNT(DISTINCT name) as count FROM steps
    ''').fetchone()['count']
    pages = (total_count + per_page - 1) // per_page

    # Paginated leaderboard
    leaderboard = conn.execute('''
        SELECT name, SUM(steps) as total_steps
        FROM steps
        GROUP BY name
        ORDER BY total_steps DESC
        LIMIT ? OFFSET ?
    ''', (per_page, offset)).fetchall()

    # Dropdown user list
    users = [row['name'] for row in conn.execute('SELECT DISTINCT name FROM steps')]

    # Personal records (if user selected)
    personal_records = []
    if user:
        personal_records = conn.execute('''
            SELECT steps, created_at, filename
            FROM steps
            WHERE name = ?
            ORDER BY created_at DESC
        ''', (user,)).fetchall()

    conn.close()

    return render_template('index.html',
                           leaderboard=leaderboard,
                           users=users,
                           personal_records=personal_records,
                           selected_name=user,
                           page=page,
                           pages=pages)

# === ROUTE: Screenshot Upload (Smartband) ===
@app.route('/upload_1', methods=['GET', 'POST'])
def upload_1():
    if request.method == 'POST':
        name = request.form.get('name')
        if 'file' not in request.files:
            return "No file uploaded", 400

        file = request.files['file']
        if file.filename == '':
            return "No selected file", 400

        if file:
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)

            # Jalankan OCR pakai EasyOCR
            result = reader.readtext(save_path, detail=0)
            text = " ".join(result)

            # Ambil angka langkah dari teks hasil OCR
            import re
            match = re.search(r'\b\d{3,6}\b', text.replace(',', ''))
            steps = int(match.group()) if match else 0

            # Simpan ke database (pakai logic baru)
            save_steps(name, steps, filename)

            return redirect(url_for('index'))

    return render_template('upload.html', endpoint='upload_1', title="Smartband Upload")

# === ROUTE: Screenshot Upload (Origin / Apple Health) Auto Processing (Fixed) ===
# iPhone OK, Origin Light Only
@app.route('/upload_2', methods=['GET', 'POST'])
def upload_2():
    if request.method == 'POST':
        name = request.form.get('name')
        if 'file' not in request.files:
            return "No file uploaded", 400

        file = request.files['file']
        if file.filename == '':
            return "No selected file", 400

        if file:
            import cv2
            import numpy as np
            import re
            from PIL import Image

            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)

            ext = filename.lower().split('.')[-1]
            processed_path = save_path  # Default

            # === Case 1: PNG (iPhone Screenshot) ===
            if ext == 'png':
                print("📸 Detected PNG (iPhone) → No preprocessing applied.")
                processed_path = save_path

            # === Case 2: JPG (Origin / Vivo) ===
            elif ext in ['jpg', 'jpeg']:
                print("📷 Detected JPG (Origin) → Converting to PNG and preprocessing...")

                # Convert JPG → PNG
                img_pil = Image.open(save_path).convert("RGB")
                converted_path = save_path.rsplit('.', 1)[0] + "_converted.png"
                img_pil.save(converted_path, format='PNG')

                # Load PNG for preprocessing
                img = cv2.imread(converted_path)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (gray.shape[1]*2, gray.shape[0]*2))
                blur = cv2.GaussianBlur(gray, (3, 3), 0)
                thresh = cv2.adaptiveThreshold(
                    blur, 255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY_INV,
                    11, 2
                )

                processed_path = converted_path.replace('.png', '_proc.png')
                cv2.imwrite(processed_path, thresh)

                print(f"✅ JPG converted and preprocessed → {processed_path}")

            else:
                print("⚠️ Unknown format, skipping preprocessing.")

            # === OCR ===
            text_result = reader.readtext(processed_path, detail=0)
            text = " ".join(text_result)

            print("=== OCR RAW TEXT ===")
            print(text)
            print("====================")

            # === Cleaning (Multilingual Support) ===
            cleaned_text = text.replace(',', '').replace('.', '').lower()
            cleaned_text = re.sub(r'\b\d{1,2}[:]\d{2}\b', '', cleaned_text)  # hapus jam

            # Hilangkan kata umum dalam beberapa bahasa terkait "goal" atau "average"
            multilingual_patterns = [
                r'\b\d+(\.\d+)?\s*(kb/s|mb/s|gb/s|kbps|mbps|gbps)\b',
                r'goal\s*\d{1,6}\s*steps',  # English
                r'average\s*\d{1,6}\s*steps',  # English
                r'target\s*\d{1,6}\s*langkah',  # Indonesian
                r'rata[-\s]*rata\s*\d{1,6}\s*langkah',  # Indonesian
                r'matlamat\s*\d{1,6}\s*langkah',  # Malay
                r'平均\s*\d{1,6}\s*步',  # Chinese
                r'เป้าหมาย\s*\d{1,6}\s*ก้าว',  # Thai (goal)
                r'เฉลี่ย\s*\d{1,6}\s*ก้าว'  # Thai (average)
            ]

            for pattern in multilingual_patterns:
                cleaned_text = re.sub(pattern, '', cleaned_text)

            # === Extract steps (Stable Logic) ===
            pattern_priority = re.search(
                r'(total|today|jumlah|hari ini|รวม|วันนี้|總計|今日)\D{0,10}(\d{3,6})\s*(steps|langkah|ก้าว|步)?',
                cleaned_text)
            steps = 0

            if pattern_priority:
                steps = int(pattern_priority.group(2))
            else:
                match = re.search(r'(\d{3,6})\s*(steps|langkah|ก้าว|步)?', cleaned_text)
                if match:
                    steps = int(match.group(1))
                else:
                    numbers = re.findall(r'\b\d{3,6}\b', cleaned_text)
                    if numbers:
                        steps = max(map(int, numbers))
                    if steps > 50000:
                        steps = 0

            print(f"➡️  Hasil akhir langkah untuk {name}: {steps}")

            # === Simpan ke database ===
            save_steps(name, steps, os.path.basename(processed_path))

            return redirect(url_for('index'))

    return render_template('upload.html', endpoint='upload_2', title="Origin Health and Apple Health Upload")

# Admin Privilege
# === ROUTE: Admin Login (from popup) ===
@app.route('/admin_login', methods=['POST'])
def admin_login():
    # accept multiple possible field names so HTML and backend stay tolerant
    admin_id = request.form.get('admin_id') or request.form.get('username') or request.form.get('user')
    password = request.form.get('password') or request.form.get('admin_pass')

    # basic validation
    if not admin_id or not password:
        flash("Please fill in both username and password.", "danger")
        return redirect(url_for('index'))

    # credential check
    if admin_id in ADMINS and ADMINS[admin_id] == password:
        session['admin_logged'] = True
        session['admin_name'] = admin_id
        flash(f"Welcome, {admin_id}!", "success")
        return redirect(url_for('admin_page'))
    else:
        flash("Invalid ID or Password", "danger")
        return redirect(url_for('index'))

# === ROUTE: Admin Dashboard ===
@app.route('/admin')
def admin_page():
    if not session.get('admin_logged'):
        flash("You must log in as admin first.", "warning")
        return redirect(url_for('index'))

    search_query = request.args.get('q', '')
    conn = get_db_connection()

    if search_query:
        records = conn.execute('''
            SELECT id, name, steps, created_at, filename
            FROM steps
            WHERE name LIKE ?
            ORDER BY created_at DESC
        ''', (f'%{search_query}%',)).fetchall()
    else:
        records = conn.execute('''
            SELECT id, name, steps, created_at, filename
            FROM steps
            ORDER BY created_at DESC
            LIMIT 100
        ''').fetchall()

    conn.close()
    return render_template('admin.html', records=records, search_query=search_query)

# === ROUTE: Edit Record ===
@app.route('/admin/edit/<int:record_id>', methods=['POST'])
def edit_record(record_id):
    if not session.get('admin_logged'):
        return redirect(url_for('index'))

    new_steps = request.form.get('steps')
    if not new_steps or not new_steps.isdigit():
        flash("Invalid steps value.", "danger")
        return redirect(url_for('admin_page'))

    conn = get_db_connection()
    conn.execute('UPDATE steps SET steps = ? WHERE id = ?', (new_steps, record_id))
    conn.commit()
    conn.close()

    flash("Record updated successfully!", "success")
    return redirect(url_for('admin_page'))

# === ROUTE: Delete Record ===
@app.route('/admin/delete/<int:record_id>', methods=['POST'])
def delete_record(record_id):
    if not session.get('admin_logged'):
        return redirect(url_for('index'))

    conn = get_db_connection()
    conn.execute('DELETE FROM steps WHERE id = ?', (record_id,))
    conn.commit()
    conn.close()

    flash("Record deleted successfully.", "info")
    return redirect(url_for('admin_page'))

# === ROUTE: Admin Logout ===
@app.route('/admin/logout')
def admin_logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('index'))


# === Run App ===
if __name__ == '__main__':
    import atexit
    import shutil

    os.makedirs("persistent", exist_ok=True)
    BACKUP_DB = os.path.join("persistent", "steps_backup.db")

    def backup_db():
        if os.path.exists(app.config['DATABASE']):
            shutil.copy(app.config['DATABASE'], BACKUP_DB)
            print("💾 Database disalin ke backup!")

    atexit.register(backup_db)

    if not os.path.exists(app.config['DATABASE']) and os.path.exists(BACKUP_DB):
        shutil.copy(BACKUP_DB, app.config['DATABASE'])
        print("♻️ Database dipulihkan dari backup")

    app.run(host='0.0.0.0', port=5000, debug=True)
