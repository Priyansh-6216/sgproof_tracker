import os
import csv
import subprocess
import threading
import sys
import json
from datetime import datetime
from pathlib import Path
# pyrefly: ignore [missing-import]
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
from email_sender import send_deals_email

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '.'

PRODUCTS_CSV = "products.csv"
STATUS_FILE = "status.json"

def update_status(msg, is_running=False):
    data = {"message": msg, "is_running": is_running}
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f)

def run_tracker_job():
    print("Running tracker.py...")
    update_status("Tracker is running... This may take a few minutes.", True)
    try:
        # Delete old file so we don't accidentally send a stale report
        Path("deals_today.html").unlink(missing_ok=True)
        # Run the tracker script
        os.system("python tracker.py")
        print("Tracker completed successfully. Sending email...")
        success = send_deals_email()
        if success:
            now = datetime.now().strftime("%Y-%m-%d %I:%M %p")
            update_status(f"Email successfully sent on {now}.", False)
        else:
            update_status("Tracker finished, but email failed to send.", False)
    except Exception as e:
        print(f"Error running tracker or sending email: {e}")
        update_status(f"Error occurred: {str(e)}", False)

@app.route("/api/status", methods=["GET"])
def get_status():
    if Path(STATUS_FILE).exists():
        with open(STATUS_FILE, "r") as f:
            return jsonify(json.load(f))
    return jsonify({"message": "Ready to run.", "is_running": False})

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/products", methods=["GET"])
def get_products():
    if not Path(PRODUCTS_CSV).exists():
        return jsonify([])
    
    products = []
    with open(PRODUCTS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            products.append(row)
    return jsonify(products)

@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.json
    if not data or 'name' not in data:
        return jsonify({"error": "Invalid data"}), 400

    fieldnames = ["name", "url", "category", "case_size"]
    
    file_exists = Path(PRODUCTS_CSV).exists()
    
    with open(PRODUCTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "name": data.get("name", ""),
            "url": data.get("url", ""),
            "category": data.get("category", "General"),
            "case_size": data.get("case_size", "12")
        })
        
    return jsonify({"success": True}), 201

@app.route("/api/products/<int:index>", methods=["DELETE"])
def delete_product(index):
    if not Path(PRODUCTS_CSV).exists():
        return jsonify({"error": "No products file found"}), 404
        
    products = []
    fieldnames = ["name", "url", "category", "case_size"]
    
    with open(PRODUCTS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            products.append(row)
            
    if index < 0 or index >= len(products):
        return jsonify({"error": "Invalid product index"}), 400
        
    products.pop(index)
    
    with open(PRODUCTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(products)
        
    return jsonify({"success": True}), 200

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file and file.filename.endswith('.csv'):
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], PRODUCTS_CSV))
        return jsonify({"success": True}), 200
        
    return jsonify({"error": "Invalid file format, must be CSV"}), 400

@app.route("/api/run_tracker", methods=["POST"])
def run_tracker():
    # Run tracker in background thread so UI doesn't hang
    thread = threading.Thread(target=run_tracker_job)
    thread.start()
    return jsonify({"success": True, "message": "Tracker started in background."})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
