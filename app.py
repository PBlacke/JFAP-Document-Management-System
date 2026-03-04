import os
import sqlite3
from flask import Flask, redirect, request, render_template, jsonify, url_for
from werkzeug.utils import secure_filename
import pytesseract
from PIL import Image
import pdf2image
import tempfile
from flask import send_from_directory, abort
from datetime import datetime

app = Flask(__name__)

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
    c.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")           

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

    conn.commit()
    conn.close()

init_db()

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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        #OCR text extract
        extracted_text = extract_text_from_file(filepath)

        #get form data
        doc_type = request.form.get('type', '')
        project = request.form.get('project', '')

        #get system current timee
        upload_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        #save to database
        conn = sqlite3.connect('documents.db')
        c = conn.cursor()
        c.execute("""INSERT INTO documents (filename, filepath, extracted_text, upload_date, doc_type, project) 
                  VALUES (?, ?, ?, ?, ?, ?)""",
                  (filename, filepath, extracted_text, upload_time, doc_type, project))
        conn.commit()
        doc_id = c.lastrowid
        conn.close()

        return jsonify({
            'id': doc_id,
            'filename': filename,
            'text': extracted_text[:500] + '...' if len(extracted_text) > 500 else extracted_text,
            'full_text': extracted_text
        })
    
    return jsonify({'error': 'File type not allowed'}), 400

@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify([])
    
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()

    # use fts match, join back to get all feilds by ordering newest first
    c.execute('''
              SELECT doc.id, doc.filename, doc.extracted_text
              FROM documents doc
              JOIN documents_fts fts ON doc.id = fts.rowid
              WHERE documents_fts MATCH ?
              ORDER BY doc.upload_date DESC
              ''', (query,))
    
    results = c.fetchall()
    conn.close()

    return jsonify([{
        'id': r[0],
        'filename': r[1],
        'snippet': r[2][:200] + '...' if len(r[2]) > 200 else r[2]
    } for r in results])

@app.route('/documents')
def list_documents():
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute("SELECT id, filename, upload_date, doc_type, project FROM documents ORDER BY upload_date DESC")
    docs = c.fetchall()
    conn.close()
    return render_template('documents.html', documents=docs)

#view document
@app.route('/view/<int:doc_id>')
def view_document(doc_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute("SELECT filepath FROM documents WHERE id = ?", (doc_id,))
    row =c.fetchone()
    conn.close()

    if row is None:
        abort(404)  #document not found

    filepath = row[0]

    #security check: ensure that file exist and is inside the uploads folder
    if not os.path.exists(filepath):
        abort(404)

    #serve file
    return send_from_directory(os.path.dirname(filepath), os.path.basename(filepath))

#preview document 
@app.route('/preview/<int:doc_id>')
def preview_document(doc_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute("SELECT filename, filepath FROM documents WHERE id = ?", (doc_id,))
    row = c.fetchone()
    conn.close()

    if row is None:
        abort(404)

    filename, filepath = row

    #determine file type
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext in ['png', 'jpg', 'jpeg', 'gif']:
        #for img -> img tag
        return render_template('preview_image.html', filename=filename, filepath=filepath, doc_id=doc_id)
    elif ext == 'pdf':
        #for pdf -> embed
        return render_template('preview_pdf.html', filename=filename, filepath=filepath, doc_id=doc_id)
    else:
        #other file
        return redirect(url_for('view_document', doc_id=doc_id))

#edit document
@app.route('/edit/<int:doc_id>', methods=['POST'])
def edit_document(doc_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()

    new_filename = request.form['filename'].strip()
    doc_type = request.form.get('type', '')
    project = request.form.get('project', '')

    if not new_filename:
        return "Filename can't be empty", 400

    # Get current file info
    c.execute("SELECT filename, filepath FROM documents WHERE id = ?", (doc_id,))
    row = c.fetchone()
    if not row:
        abort(404)
    old_filename, old_filepath = row

    # Determine new filename with extension
    ext = old_filename.rsplit('.', 1)[-1] if '.' in old_filename else ''
    if not new_filename.endswith('.' + ext):
        new_filename = new_filename + '.' + ext
    new_filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)

    # Rename file on disk
    try:
        os.rename(old_filepath, new_filepath)
    except Exception as e:
        return f"Error renaming file: {str(e)}", 500

    # Update database
    c.execute("""UPDATE documents 
                 SET filename = ?, filepath = ?, doc_type = ?, project = ? 
                 WHERE id = ?""",
              (new_filename, new_filepath, doc_type, project, doc_id))
    conn.commit()
    conn.close()

    return redirect(url_for('list_documents'))

#delete document
@app.route('/delete/<int:doc_id>', methods=['POST'])
def delete_document(doc_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()

    #get filepath
    c.execute("SELECT filepath FROM documents WHERE id = ?", (doc_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        abort(404)

    filepath = row[0]

    #delete from database
    c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()

    #delete file from disk
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        #log error, L file not so skibidi sigma 
        print(f"L deleting file {filepath}: {e}")

    return redirect(url_for('list_documents'))


if __name__ == '__main__':
    app.run(debug=True)