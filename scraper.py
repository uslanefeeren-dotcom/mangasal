import sqlite3
import json
import os
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from functools import wraps
import hashlib
import secrets

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voidscans.db")

app = Flask(__name__)
CORS(app)

def rows(sql, params=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    result = [dict(row) for row in cur.fetchall()]
    conn.close()
    return result

# ---------- STATIK DOSYALAR ----------
@app.route('/')
def home():
    return send_from_directory('.', 'index.html')

@app.route('/reader')
def reader():
    return send_from_directory('.', 'reader.html')

@app.route('/admin')
def admin():
    return send_from_directory('.', 'admin.html')

# ---------- API ENDPOINTLERI ----------
@app.route("/api/trending")
def trending():
    limit = min(int(request.args.get("limit", 12)), 200)
    data = rows("SELECT * FROM series ORDER BY view_count DESC LIMIT ?", (limit,))
    return jsonify({"status": "ok", "data": data})

@app.route("/api/latest")
def latest():
    limit = min(int(request.args.get("limit", 12)), 200)
    data = rows("SELECT * FROM series ORDER BY last_updated DESC LIMIT ?", (limit,))
    return jsonify({"status": "ok", "data": data})

@app.route("/api/search")
def search():
    q = request.args.get("q", "")
    limit = min(int(request.args.get("limit", 20)), 100)
    data = rows("SELECT * FROM series WHERE title LIKE ? ORDER BY view_count DESC LIMIT ?", (f"%{q}%", limit))
    return jsonify({"status": "ok", "data": data})

@app.route("/api/series/<path:slug>")
def series_detail(slug):
    # slug'ı url'den çıkar (tam url de gelebilir)
    if slug.startswith("http"):
        data = rows("SELECT * FROM series WHERE url = ?", (slug,))
    else:
        data = rows("SELECT * FROM series WHERE slug = ?", (slug,))
    if not data:
        return jsonify({"status": "error", "message": "Bulunamadı"}), 404
    s = data[0]
    chaps = rows("SELECT * FROM chapters WHERE series_url = ?", (s["url"],))
    s["chapters"] = chaps
    return jsonify({"status": "ok", "data": s})

@app.route("/api/proxy/image")
def proxy_image():
    # Resim proxy'si (basit, kaynak siteye yönlendir)
    img_url = request.args.get("url", "")
    if not img_url or not img_url.startswith("http"):
        return "", 400
    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "/".join(img_url.split("/")[:3]) + "/"}
        r = requests.get(img_url, headers=headers, timeout=10)
        return r.content, 200, {"Content-Type": r.headers.get("Content-Type", "image/jpeg")}
    except:
        return "", 502

@app.route("/api/stats")
def stats():
    total_series = rows("SELECT COUNT(*) as c FROM series")[0]["c"]
    return jsonify({"status": "ok", "total_series": total_series})

# ---------- KULLANICI SISTEMI (isteğe bağlı, basit) ----------
def hash_pwd(pwd): return hashlib.sha256(pwd.encode()).hexdigest()
def gen_token(): return secrets.token_hex(32)

def get_user_by_token(token):
    if not token: return None
    try:
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE token = ?", (token,))
        user = cur.fetchone()
        conn.close()
        return dict(user) if user else None
    except:
        return None

@app.route("/api/auth/me")
def me():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = get_user_by_token(token)
    if not user:
        return jsonify({"status": "error", "message": "Giriş gerekli"}), 401
    return jsonify({"status": "ok", "user": user})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
