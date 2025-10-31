import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from werkzeug.utils import secure_filename
import easyocr

# === Flask Setup ===
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['DATABASE'] = 'steps.db'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

reader = easyocr.Reader(['en'])

def get_db_connection():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

with get_db_connection() as conn:
    conn.execute('''CREATE TABLE IF NOT EXISTS steps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        steps INTEGER,
                        filename TEXT,
                        created_at TEXT
                    )''')
    conn.commit()


# === ROUTE: Main Page ===
@app.route('/')
def index():
    user = request.args.get('user', '')
    conn = get_db_connection()

    leaderboard = conn.execute('''
        SELECT name, SUM(steps) as total_steps
        FROM steps
        GROUP BY name
        ORDER BY total_steps DESC
    ''').fetchall()

    users = [row['name'] for row in conn.execute('SELECT DISTINCT name FROM steps')]
    personal_records = []

    if user:
        personal_records = conn.execute('''
            SELECT steps, created_at, filename
            FROM steps WHERE name = ?
            ORDER BY created_at DESC
        ''', (user,)).fetchall()

    conn.close()
    return render_template('index.html',
                           leaderboard=leaderboard,
                           users=users,
                           personal_records=personal_records,
                           selected_name=user)


# === ROUTE: Screenshot Upload ===
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

            # Simpan ke database
            conn = get_db_connection()
            conn.execute('''
                INSERT INTO steps (name, steps, filename, created_at)
                VALUES (?, ?, ?, ?)
            ''', (name, steps, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()

            return redirect(url_for('index'))

    return render_template('upload.html', endpoint='upload_1', title="Smartband Upload")

# Upload iphone ok, origin ok smartband not ok
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
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)

            import cv2
            import numpy as np
            import re

            img = cv2.imread(save_path)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (gray.shape[1]*2, gray.shape[0]*2))
            blur = cv2.GaussianBlur(gray, (3,3), 0)
            thresh = cv2.adaptiveThreshold(
                blur, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV,
                11, 2
            )

            processed_path = save_path.replace('.jpg', '_proc.jpg')
            cv2.imwrite(processed_path, thresh)

            text_result = reader.readtext(processed_path, detail=0)
            text = " ".join(text_result)

            print("=== OCR RAW TEXT ===")
            print(text)
            print("====================")

            cleaned_text = text.replace(',', '').replace('.', '').lower()
            cleaned_text = re.sub(r'\b\d{1,2}[:]\d{2}\b', '', cleaned_text)  # Hapus jam

            # Hapus teks yang mengandung 'goal' atau 'average'
            cleaned_text = re.sub(r'goal\s*\d{1,6}\s*steps', '', cleaned_text)
            cleaned_text = re.sub(r'average\s*\d{1,6}\s*steps', '', cleaned_text)

            # Cari pola di sekitar kata 'total' atau 'today'
            pattern_priority = re.search(r'(total|today)\D{0,10}(\d{3,6})\s*steps', cleaned_text)
            steps = 0

            if pattern_priority:
                steps = int(pattern_priority.group(2))
            else:
                # fallback kalau tidak ketemu di atas
                match = re.search(r'(\d{3,6})\s*steps', cleaned_text)
                if match:
                    steps = int(match.group(1))
                else:
                    # Ambil angka masuk akal kalau tetap tidak ketemu
                    numbers = re.findall(r'\b\d{3,6}\b', cleaned_text)
                    if numbers:
                        steps = max(map(int, numbers))
                    if steps > 50000:
                        steps = 0

            print(f"➡️  Hasil akhir langkah untuk {name}: {steps}")

            # Simpan ke DB
            conn = get_db_connection()
            conn.execute('''
                INSERT INTO steps (name, steps, filename, created_at)
                VALUES (?, ?, ?, ?)
            ''', (name, steps, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()

            return redirect(url_for('index'))

    return render_template('upload.html', endpoint='upload_2', title="Origin Health and Apple Health Upload")


# # === Run app Local ===
# if __name__ == '__main__':
#     # Gunakan host='0.0.0.0' agar bisa diakses dari HP dalam satu WiFi
#     app.run(host='0.0.0.0', port=5000, debug=True)

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
