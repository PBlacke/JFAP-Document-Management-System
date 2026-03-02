import os
import sqlite3
from flask import Flask, request, render_template, jsonify
from werkzeug.utils import secure_filename
import pytesseract
from PIL import Image
import pdf2image
import tempfile
from flask import send_from_directory, abort

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

        #save to database
        conn = sqlite3.connect('documents.db')
        c = conn.cursor()
        c.execute("INSERT INTO documents (filename, filepath, extracted_text) VALUES (?, ?, ?)",
                  (filename, filepath, extracted_text))
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
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute("SELECT id, filename, extracted_text FROM documents WHERE extracted_text LIKE ? ORDER BY upload_date DESC",
              (f'%{query}%',))
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
    c.execute("SELECT id, filename, upload_date FROM documents ORDER BY upload_date DESC")
    docs = c.fetchall()
    conn.close()
    return render_template('documents.html', documents=docs)

#view route
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

if __name__ == '__main__':
    app.run(debug=True)