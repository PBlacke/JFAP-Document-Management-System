import os
import sqlite3
from flask import Flask, redirect, request, render_template, jsonify, url_for
from werkzeug.utils import secure_filename
import pytesseract
from PIL import Image
import pdf2image
import tempfile
from flask import send_from_directory, abort, send_file, Response, flash 
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import re
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
import time
import io
import secrets
import csv


#app configuration
app = Flask(__name__)

app.config['SECRET_KEY'] = secrets.token_hex(16)  # Use a secure random secret key

#for user class
class User(UserMixin):
    def __init__(self, id, username, email, is_admin=False):
        self.id = id
        self.username = username
        self.email = email
        self.is_admin = is_admin

#login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute("SELECT id, username, email, is_admin FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return User(row[0], row[1], row[2], row[3])
    return None

#admin-only decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        
        #check if user is admin
        conn = sqlite3.connect('documents.db')
        c = conn.cursor()   
        c.execute("SELECT is_admin FROM users WHERE id = ?", (current_user.id,))
        row = c.fetchone()
        conn.close()
        if not row or row[0] != 1:
            abort(403)  #forbidden
        return f(*args, **kwargs)
    return decorated_function    


# Configuration
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit
app.config['SECRET_KEY'] = 'your-secret-key-change-this'

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Database setup
def init_db():
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS documents
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  filename TEXT,
                  filepath TEXT,
                  extracted_text TEXT,
                  upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    #add new columns doc_type and project, if missinf   
    for col in ['doc_type', 'project']:
        try:
            c.execute(f"ALTER TABLE documents ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass    

    #create fts5 virtual table, use try/except because CREATE VIRTUAL TABLE doesn't support IF NOT EXISTS
    try:
        c.execute('''CREATE VIRTUAL TABLE documents_fts USING fts5(
                  filename,
                  extracted_text,
                  doc_type,
                  project,
                  content=documents,
                  content_rowid=id
                  )''')
        print("Created FTS table")
    except sqlite3.OperationalError as e:
        if "already exists" in str(e):
            print("FTS table already exists")
        else:
            raise e

    #rebuil fts index 
    #c.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")           

    #triggers to keep fts in sync
    c.executescript('''
                    CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN INSERT INTO documents_fts(rowid, filename, extracted_text, doc_type, project)
                    VALUES (new.id, new.filename, new.extracted_text, new.doc_type, new.project);
                    END;
                    CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN DELETE FROM documents_fts WHERE rowid = old.id;
                    END;
                    CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN DELETE FROM documents_fts WHERE rowid = old.id;
                    INSERT INTO documents_fts(rowid, filename, extracted_text, doc_type, project)
                    VALUES(new.id, new.filename, new.extracted_text, new.doc_type, new.project);
                    END;
                    ''')
    
    #add user
    c.execute('''CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT UNIQUE NOT NULL,
              email TEXT,
              password_hash TEXT NOT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
              )''')

    
    try:
        c.execute("ALTER TABLE documents ADD COLUMN user_id INTEGER REFERENCES users(id)")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists

    #create activity log table
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              action TEXT NOT NULL,
              details TEXT,
              timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(user_id) REFERENCES users(id)
              )''')

    #create document versions table
    c.execute('''CREATE TABLE IF NOT EXISTS document_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id INTEGER NOT NULL,
              version INTEGER NOT NULL,
              filename TEXT NOT NULL,
              filepath TEXT NOT NULL,
              doc_type TEXT,
              project TEXT,
              action TEXT NOT NULL, -- 'edit' or 'delete'
              changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              changed_by INTEGER NOT NULL,
              FOREIGN KEY(doc_id) REFERENCES documents(id),
              FOREIGN KEY(changed_by) REFERENCES users(id)
              )''')       

    c.execute("CREATE INDEX IF NOT EXISTS idx_document_versions_doc_id ON document_versions(doc_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_documents_id ON documents(id)")  # already primary key, but explicit
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_user_id ON activity_log(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp ON activity_log(timestamp);")       

    conn.commit()
    conn.close()

init_db()

#password validation and hashing
def validate_password(password):
    """Return (is_valid, error_message)"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number"
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "Password must contain at least one special character" 
    return True, ""

def generate_username(first, last):
    """Format first.last"""
    first_part = first.split()[0] if first else ""
    last_part = last.split()[-1] if last else ""
    return f"{first_part}.{last_part}".lower()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_file(filepath):
    """Extract text from image or PDF using Tesseract"""
    try:
        if filepath.lower().endswith('.pdf'):
            images = pdf2image.convert_from_path(filepath)
            text = ""
            for image in images:
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                    image.save(tmp.name)
                    text += pytesseract.image_to_string(Image.open(tmp.name))
                    os.unlink(tmp.name)
            return text
        else:
            return pytesseract.image_to_string(Image.open(filepath))
    except Exception as e:
        return f"Error extracting text: {str(e)}"

#activity logging
def log_activity(user_id, action, details=''):
    start = time.time()
    retries = 5
    while retries > 0:
        try:
            conn = sqlite3.connect('documents.db')
            conn.execute("PRAGMA journal_mode=WAL")
            c = conn.cursor()
            c.execute("INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
                      (user_id, action, details))
            conn.commit()
            conn.close()
            elapsed = time.time() - start
            print(f"[LOG] activity logged in {elapsed:.3f}s")
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                retries -= 1
                print(f"[LOG] database is locked, retrying... ({5 - retries}/5)")
                time.sleep(0.1)
                continue
            else:
                print(f"[LOG] error: {e}")
                raise
        except Exception as e:
            print(f"[LOG] error: {e}")
            raise



@app.route('/')
def index():
    if current_user.is_authenticated: 
        return render_template('index.html')
    else:
        return redirect(url_for('login'))


#registration route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first = request.form['firstname'].strip()
        last = request.form['lastname'].strip()
        email = request.form.get('email', '').strip()
        password = request.form['password']
        confirm = request.form['confirm_password']

        #basic checks
        if not first or not last:
            return "First and last name are required", 400
        if password != confirm:
            return "Passwords do not match", 400

        #validate password
        valid, msg = validate_password(password)
        if not valid:
            return msg, 400
        
        #generate username
        username = generate_username(first, last)

        #check uniqueness
        conn = sqlite3.connect('documents.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        if c.fetchone():
            conn.close()
            return "Username already exists. Try different name combination", 400

        #hash password and insert
        pw_hash = generate_password_hash(password)
        c.execute("INSERT INTO users (username, email, password_hash, approved) VALUES (?, ?, ?, 0)",
                  (username, email, pw_hash))
        conn.commit()
        conn.close()

        return redirect(url_for('login'))

    return render_template('register.html')   


#login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = sqlite3.connect('documents.db')
        c = conn.cursor()
        c.execute("SELECT id, username, email, password_hash, approved FROM users WHERE username = ?", (username,))
        row = c.fetchone()
        conn.close()

        if row and check_password_hash(row[3], password):
            if row[4] == 0:
                flash("Your account is pending approval by an administrator.")
                return redirect(url_for('login'))
            user = User(row[0], row[1], row[2])
            login_user(user)
            log_activity(user.id, 'login', f'User {user.username} logged in')
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password")
            return redirect(url_for('login'))
        
    return render_template('login.html')


#logout route
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    start_total = time.time()

    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        # --- File save ---
        start_save = time.time()
        file.save(filepath)
        save_time = time.time() - start_save
        print(f"[UPLOAD] File save: {save_time:.3f}s")

        # --- OCR ---
        start_ocr = time.time()
        extracted_text = extract_text_from_file(filepath)
        ocr_time = time.time() - start_ocr
        print(f"[UPLOAD] OCR: {ocr_time:.3f}s")

        # Get form data
        doc_type = request.form.get('type', '')
        project = request.form.get('project', '')

        upload_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # --- MAIN DATABASE INSERT ---
        start_insert = time.time()
        conn = sqlite3.connect('documents.db')
        conn.execute("PRAGMA journal_mode=WAL")
        c = conn.cursor()
        c.execute("""INSERT INTO documents 
                     (filename, filepath, extracted_text, upload_date, doc_type, project, user_id) 
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (filename, filepath, extracted_text, upload_time, doc_type, project, current_user.id))
        doc_id = c.lastrowid
        conn.commit()
        conn.close()
        insert_time = time.time() - start_insert
        print(f"[UPLOAD] Main insert + commit: {insert_time:.3f}s")

        # --- LOG ACTIVITY SEPARATELY ---
        start_log = time.time()
        log_activity(current_user.id, 'upload', f'Uploaded "{filename}"')
        log_time = time.time() - start_log
        print(f"[UPLOAD] log_activity: {log_time:.3f}s")

        total = time.time() - start_total
        print(f"[UPLOAD] TOTAL: {total:.3f}s")

        return jsonify({
            'id': doc_id,
            'filename': filename,
            'text': extracted_text[:500] + '...' if len(extracted_text) > 500 else extracted_text,
            'full_text': extracted_text
        })

    return jsonify({'error': 'File type not allowed'}), 400

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify([])
    
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()

    # use fts match with bm25, join back to get all feilds by ordering newest first
    c.execute('''
              SELECT doc.id, doc.filename, doc.extracted_text, bm25(documents_fts) as rank
              FROM documents doc
              JOIN documents_fts fts ON doc.id = fts.rowid
              WHERE documents_fts MATCH ? AND doc.user_id = ?
              ORDER BY rank ASC, doc.upload_date DESC
              ''', (query, current_user.id))
    
    results = c.fetchall()
    conn.close()

    return jsonify([{
        'id': r[0],
        'filename': r[1],
        'snippet': r[2][:200] + '...' if len(r[2]) > 200 else r[2]
    } for r in results])

@app.route('/documents')
@login_required
def list_documents():
    # Get query parameters
    search_term = request.args.get('q', '').strip()
    doc_type_filter = request.args.get('type', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    conn = sqlite3.connect('documents.db')
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    # Base query: select all documents with uploader info
    base_sql = """
        SELECT d.id, d.filename, d.upload_date, d.doc_type, d.project, u.username, d.user_id
        FROM documents d
        JOIN users u ON d.user_id = u.id
    """
    where_clauses = []
    params = []

    # Search term: match filename, doc_type, project (case-insensitive)
    if search_term:
        where_clauses.append("(d.filename LIKE ? OR d.doc_type LIKE ? OR d.project LIKE ?)")
        term = f"%{search_term}%"
        params.extend([term, term, term])

    # Document type filter: exact match
    if doc_type_filter:
        where_clauses.append("d.doc_type = ?")
        params.append(doc_type_filter)

    # Build final query
    if where_clauses:
        full_sql = base_sql + " WHERE " + " AND ".join(where_clauses)
    else:
        full_sql = base_sql

    # Count total items
    count_sql = f"SELECT COUNT(*) FROM ({full_sql})"
    c.execute(count_sql, params)
    total_count = c.fetchone()[0]

    # Add pagination
    offset = (page - 1) * per_page
    paginated_sql = full_sql + " ORDER BY d.upload_date DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
    c.execute(paginated_sql, params)
    docs = c.fetchall()
    conn.close()

    # Pagination calculations
    total_pages = (total_count + per_page - 1) // per_page
    # Ensure page is within bounds
    page = max(1, min(page, total_pages)) if total_pages > 0 else 1

    return render_template('documents.html',
                           documents=docs,
                           total_pages=total_pages,
                           current_page=page,
                           search_term=search_term,
                           type_filter=doc_type_filter,
                           total_count=total_count)
#view document
@app.route('/view/<int:doc_id>')
@login_required
def view_document(doc_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    
    # Check if admin
    c.execute("SELECT is_admin FROM users WHERE id = ?", (current_user.id,))
    admin_row = c.fetchone()
    is_admin = admin_row and admin_row[0] == 1

    if is_admin:
        c.execute("""SELECT filename, filepath, doc_type, project, upload_date, username
                     FROM documents d
                     JOIN users u ON d.user_id = u.id
                     WHERE d.id = ?""", (doc_id,))
    else:
        c.execute("""SELECT filename, filepath, doc_type, project, upload_date, username
                     FROM documents d
                     JOIN users u ON d.user_id = u.id
                     WHERE d.id = ? AND d.user_id = ?""", (doc_id, current_user.id))
    
    row = c.fetchone()
    conn.close()
    if not row:
        abort(404)

    filename, filepath, doc_type, project, upload_date, uploader = row  # unpack all

    if not os.path.exists(filepath):
        abort(404)

    return send_from_directory(os.path.dirname(filepath), os.path.basename(filepath))


#preview document 
@app.route('/preview/<int:doc_id>')
@login_required
def preview_document(doc_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()

    # Check if admin
    c.execute("SELECT is_admin FROM users WHERE id = ?", (current_user.id,))
    admin_row = c.fetchone()
    is_admin = admin_row and admin_row[0] == 1

    if is_admin:
        c.execute("""SELECT d.filename, d.filepath, d.doc_type, d.project, d.upload_date, u.username
                     FROM documents d
                     JOIN users u ON d.user_id = u.id
                     WHERE d.id = ?""", (doc_id,))
    else:
        c.execute("""SELECT d.filename, d.filepath, d.doc_type, d.project, d.upload_date, u.username
                     FROM documents d
                     JOIN users u ON d.user_id = u.id
                     WHERE d.id = ? AND d.user_id = ?""", (doc_id, current_user.id))

    row = c.fetchone()
    conn.close()
    if not row:
        abort(404)

    filename, filepath, doc_type, project, upload_date, uploader_username = row
    
    #determine file type
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    template_kwargs = {
        'doc_id': doc_id,
        'filename': filename,
        'filepath': filepath,
        'doc_type': doc_type,
        'project': project,
        'upload_date': upload_date,
        'uploader_username': uploader_username
    }

    if ext in ['png', 'jpg', 'jpeg', 'gif']:
        #for img -> img tag
        return render_template('preview_image.html', **template_kwargs)
    elif ext == 'pdf':
        #for pdf -> embed
        return render_template('preview_pdf.html', **template_kwargs)
    else:
        #other file
        return redirect(url_for('view_document', doc_id=doc_id))

#edit document (rename, change type/project)
@app.route('/edit/<int:doc_id>', methods=['POST'])
@login_required
def edit_document(doc_id):
    # Get form data first
    new_filename = request.form['filename'].strip()
    doc_type = request.form.get('type', '')
    project = request.form.get('project', '')

    if not new_filename:
        return "Filename can't be empty", 400

    conn = sqlite3.connect('documents.db')
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    # Get current file info (including type/project)
    c.execute("SELECT filename, filepath, doc_type, project, user_id FROM documents WHERE id = ?", (doc_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        abort(404)
    old_filename, old_filepath, old_doc_type, old_project, owner_id = row

    # Check ownership or admin
    if owner_id != current_user.id:
        c.execute("SELECT is_admin FROM users WHERE id = ?", (current_user.id,))
        admin_row = c.fetchone()
        if not admin_row or admin_row[0] != 1:
            conn.close()
            abort(403)

    # Versioning – count and insert
    c.execute("SELECT COUNT(*) FROM document_versions WHERE doc_id = ?", (doc_id,))
    count = c.fetchone()[0]
    new_version = count + 1
    c.execute("""INSERT INTO document_versions (doc_id, version, filename, filepath, doc_type, project, action, changed_by)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (doc_id, new_version, old_filename, old_filepath, old_doc_type, old_project, 'edit', current_user.id))

    # Determine new filename with extension
    ext = old_filename.rsplit('.', 1)[-1] if '.' in old_filename else ''
    if not new_filename.endswith('.' + ext):
        new_filename = new_filename + '.' + ext
    new_filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)

    # Rename file on disk
    try:
        os.rename(old_filepath, new_filepath)
    except Exception as e:
        conn.close()
        return f"Error renaming file: {str(e)}", 500

    # Update database
    c.execute("""UPDATE documents 
                 SET filename = ?, filepath = ?, doc_type = ?, project = ? 
                 WHERE id = ?""",
              (new_filename, new_filepath, doc_type, project, doc_id))

    # Commit everything (version + update) before logging
    conn.commit()
    conn.close()

    # Log activity AFTER commit (no lock)
    log_activity(current_user.id, 'edit', f'Edited document "{old_filename}" to "{new_filename}", type: {doc_type}, project: {project}')

    return redirect(url_for('list_documents'))



#delete document
@app.route('/delete/<int:doc_id>', methods=['POST'])
@login_required
def delete_document(doc_id):
    conn = sqlite3.connect('documents.db')
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    # Get full info
    c.execute("SELECT filename, filepath, doc_type, project, user_id FROM documents WHERE id = ?", (doc_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        abort(404)
    filename, filepath, doc_type, project, owner_id = row

    # Check ownership or admin
    if owner_id != current_user.id:
        c.execute("SELECT is_admin FROM users WHERE id = ?", (current_user.id,))
        admin_row = c.fetchone()
        if not admin_row or admin_row[0] != 1:
            conn.close()
            abort(403)

    # Versioning
    c.execute("SELECT COUNT(*) FROM document_versions WHERE doc_id = ?", (doc_id,))
    count = c.fetchone()[0]
    new_version = count + 1
    c.execute("""INSERT INTO document_versions (doc_id, version, filename, filepath, doc_type, project, action, changed_by)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (doc_id, new_version, filename, filepath, doc_type, project, 'delete', current_user.id))

    # Delete from database
    c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    # Commit everything (version + delete) before logging
    conn.commit()
    conn.close()

    # Log activity AFTER commit (no lock)
    log_activity(current_user.id, 'delete', f'Deleted "{filename}"')

    # Delete file
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        print(f"Error deleting file {filepath}: {e}")

    return redirect(url_for('list_documents'))  


#admin route
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    conn.execute("PRAGMA journal_mode=WAL")

    #counter (you want salad with that sir ehurhur)
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM documents")
    total_docs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM activity_log")  
    total_logs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE approved = 0")
    pending_count = c.fetchone()[0]

    #get user stats
    c.execute("SELECT id, username, email, is_admin, created_at, approved FROM users ORDER BY id")
    users = c.fetchall()

    #get document stats
    c.execute("""
              SELECT d.id, d.filename, d.doc_type, d.project, d.upload_date, u.username, d.user_id
              FROM documents d
              JOIN users u ON d.user_id = u.id
              ORDER BY d.upload_date DESC
              """)
    documents = c.fetchall()

    #get activity logs
    c.execute("""
              SELECT a.action, a.details, a.timestamp, u.username
              FROM activity_log a
              JOIN users u ON a.user_id = u.id
              ORDER BY a.timestamp DESC
              LIMIT 100
              """)
    log = c.fetchall()

    print(f"Number of log entries: {len(log)}")
    print("First log entry:", log[0] if log else "No logs")

    conn.close()
    return render_template('admin_dashboard.html', 
                           users=users, 
                           documents=documents, 
                           log=log,
                           total_users=total_users,
                           total_docs=total_docs,
                           total_logs=total_logs,
                           pending_count=pending_count
                           )

@app.route('/approve-user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def approve_user(user_id):
    conn = sqlite3.connect('documents.db')
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()
    c.execute("UPDATE users SET approved = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    log_activity(current_user.id, 'approve', f'Approved user ID {user_id}')
    return redirect(url_for('admin_dashboard'))


@app.route('/export_documents')
@login_required
def export_documents():
    import pandas as pd

    conn = sqlite3.connect('documents.db')

    query = """
    SELECT d.filename, d.doc_type, d.project, d.upload_date, u.username as uploader
        FROM documents d
        JOIN users u ON d.user_id = u.id
        ORDER BY d.upload_date DESC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Documents')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='documents_export.xlsx'
    )

@app.route('/export-documents-csv')
@login_required
def export_documents_csv():
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()

    query = """
    SELECT d.filename, d.doc_type, d.project, d.upload_date, u.username as uploader
        FROM documents d
        JOIN users u ON d.user_id = u.id
        ORDER BY d.upload_date DESC
    """
    c.execute(query)
    rows = c.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Filename', 'Document Type', 'Project', 'Upload Date', 'Uploader'])
    writer.writerows(rows)
    output.seek(0)

    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=documents_export.csv'}
    )



if __name__ == '__main__':
    app.run(debug=True, threaded=False)