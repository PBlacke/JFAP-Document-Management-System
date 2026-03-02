✨ Features
📤 Upload documents – Supports PNG, JPG, JPEG, GIF, and PDF files.

🔍 OCR text extraction – Uses Tesseract to read text from images and multi‑page PDFs.

💾 Store metadata – Saves filename, file path, extracted text, and upload date in an SQLite database.

🔎 Full‑text search – Search for any word or phrase inside the uploaded documents.

🖱️ Clickable results – Search results and document list entries link directly to the original file for viewing.

🕒 Local timestamps – Upload times are stored using the system’s local time (e.g., Philippine Time).

🖥️ Clean, responsive interface – Simple HTML/CSS/JavaScript frontend with a custom favicon.

🛠️ Tech Stack
Component	Technology
Backend	Python + Flask
OCR Engine	Tesseract + pytesseract
PDF Handling	pdf2image + poppler
Database	SQLite3
Frontend	HTML, CSS, JavaScript (vanilla)
Templating	Jinja2 (Flask built‑in)
📋 Prerequisites
Python 3.7+ – Download Python

Tesseract OCR – Installation guide

Windows: Download installer from UB Mannheim and ensure “Add Tesseract to the system PATH” is checked during installation.

macOS: brew install tesseract

Linux: sudo apt install tesseract-ocr

Poppler (for PDF processing) – Required by pdf2image.

Windows: Download binaries from poppler for Windows and add the bin folder to your PATH.

macOS: brew install poppler

Linux: sudo apt install poppler-utils

🚀 Installation & Setup
Clone or download the project repository.

Navigate to the project folder:

bash
cd dms_prototype
Create and activate a virtual environment (recommended):

bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
Install Python dependencies:

bash
pip install flask pytesseract pillow pdf2image
Ensure Tesseract is accessible:

If Tesseract is installed but not in your PATH, uncomment and set the path in app.py:

python
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
Create the uploads folder (the app does this automatically, but you can create it manually if you prefer).

Run the application:

bash
python app.py
Open your browser and go to http://127.0.0.1:5000.

📖 Usage
Upload a document:

Click “Choose File”, select an image or PDF.

Click “Upload & Process”. The app extracts text and displays a preview.

Search:

Type any word or phrase in the search box. Results appear live as you type (after at least 2 characters).

Click on a filename to view the original document in a new tab.

View all documents:

Click “View All Documents” in the navigation bar to see a list of all uploaded files with their timestamps.

🔧 Key Changes & Improvements Made
During development, several modifications were implemented to enhance functionality and fix issues:

Change	Description	Rationale
Favicon fix	Moved fav.png to a static/ folder and used url_for('static', filename='fav.png') in the HTML templates.	Flask does not serve files from the root by default; the static folder is the correct location.
Document viewing	Added /view/<int:doc_id> route that serves the file from the uploads folder.	Allows users to click on search results or document list entries to view the actual document.
Tuple bug in SQL query	Corrected (doc_id) to (doc_id,) in the view_document route.	SQLite expects a tuple of parameters; a single value without a comma is not a tuple.
os.path.exists typo	Changed os.path.exist(filepath) to os.path.exists(filepath).	The correct method name is exists (with an 's').
Local timestamps (Philippine Time)	Replaced SQLite’s CURRENT_TIMESTAMP (UTC) with datetime.now().strftime('%Y-%m-%d %H:%M:%S') in the upload_file route.	Ensures upload times reflect the local timezone (e.g., Philippine Time).
Date format for Windows	Changed '%Y-%-m-%d %H:%M:%S' to '%Y-%m-%d %H:%M:%S' because the %- modifier is not supported on Windows.	Prevents a ValueError that would cause the upload to fail and return an HTML error page.
Visual polish	Updated button and link colours to a consistent shade of blue (#415f97).	Improves the user interface and brand consistency.
🗄️ Project Structure
text
dms_prototype/
│
├── app.py                 # Main Flask application (all backend logic)
├── documents.db           # SQLite database (created automatically)
├── uploads/               # Folder where uploaded files are stored
├── static/                # Static assets
│   └── fav.png            # Custom favicon
├── templates/             # HTML templates
│   ├── index.html         # Upload & search page
│   └── documents.html     # List of all uploaded documents
└── README.md              # This documentation
⚠️ Known Issues / Limitations
Search uses a simple LIKE query; it is case‑insensitive but does not handle typos or advanced queries.

PDF processing can be slow for large files because each page is converted to an image and OCR‑ed separately.

Tesseract must be installed separately and accessible via PATH (or specified in code).

No user authentication – the prototype is intended for single‑user or local use only.

File names are not checked for duplicates; uploading a file with the same name will overwrite the previous one (though the database retains the original entry). This could be improved by generating unique filenames.

🚧 Future Enhancements (Ideas)
Add unique filename generation (e.g., timestamp‑prefixed) to avoid overwrites.

Implement full‑text search with SQLite FTS (Full‑Text Search) for better performance.

Provide a document preview (e.g., embedded PDF viewer or image thumbnail).

Add document deletion and editing capabilities.

Support for more file types (e.g., Office documents via additional libraries).

User authentication and multi‑user support.

📄 License
This project is open source and available under the MIT License. Feel free to use, modify, and distribute it as you wish.

🙏 Acknowledgements
Flask – lightweight WSGI web framework.

Tesseract OCR – open‑source OCR engine.

pdf2image – Python wrapper for poppler‑utils.

SQLite – self‑contained, serverless database.
