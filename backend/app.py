import os, requests, sqlite3, hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
import jwt

IA_ACCESS = os.getenv("IA_ACCESS_KEY", "").strip()
IA_SECRET = os.getenv("IA_SECRET_KEY", "").strip()
DEFAULT_COLLECTION = os.getenv("IA_COLLECTION", "opensource")
S3_ENDPOINT = "https://s3.us.archive.org"
JWT_SECRET = os.getenv("ORYX_JWT_SECRET", "oryx-secret-change-me")
DB_PATH = "/data/oryx.db"

app = FastAPI(title="OryxVault IA API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def init_db():
    os.makedirs("/data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT,
        is_admin INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS uploads (
        id INTEGER PRIMARY KEY, user_id INTEGER, filename TEXT, bucket TEXT,
        size INTEGER, status TEXT, started_at TIMESTAMP, completed_at TIMESTAMP, url TEXT
    )""")
    pwd = hashlib.sha256("admin123".encode()).hexdigest()
    c.execute("INSERT OR IGNORE INTO users (id, username, password_hash, is_admin) VALUES (1,?,?,1)", ("admin", pwd))
    conn.commit(); conn.close()
init_db()

def ia_headers(auto_make=False):
    h = {"Authorization": f"LOW {IA_ACCESS}:{IA_SECRET}", "x-archive-queue-derive": "0"}
    if auto_make:
        h["x-archive-auto-make-bucket"] = "1"
        h["x-archive-meta-mediatype"] = "data"
        h["x-archive-meta-collection"] = DEFAULT_COLLECTION
    return h

def get_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    try:
        payload = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
        return payload["user_id"]
    except Exception:
        raise HTTPException(401, "Invalid token")

@app.get("/api/health")
def health():
    return {"ok": True, "ia_configured": bool(IA_ACCESS and IA_SECRET)}

@app.post("/api/auth/register")
def register(username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    try:
        pwd = hashlib.sha256(password.encode()).hexdigest()
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (username, pwd))
        uid = c.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Username exists")
    finally:
        conn.close()
    token = jwt.encode({"user_id": uid, "exp": datetime.utcnow()+timedelta(days=30)}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

@app.post("/api/auth/login")
def login(username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    pwd = hashlib.sha256(password.encode()).hexdigest()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username=? AND password_hash=?", (username, pwd))
    row = c.fetchone(); conn.close()
    if not row: raise HTTPException(401, "Invalid credentials")
    token = jwt.encode({"user_id": row[0], "exp": datetime.utcnow()+timedelta(days=30)}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

@app.get("/api/auth/me")
def me(uid: int = Depends(get_user)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, created_at, is_admin FROM users WHERE id=?", (uid,))
    r = c.fetchone(); conn.close()
    return {"id": uid, "username": r[0], "created_at": r[1], "is_admin": bool(r[2])}

@app.get("/api/buckets")
def buckets(uid: int = Depends(get_user)):
    return {"buckets": [{"id":"my-photos"},{"id":"project-backups"},{"id":"videos-2026"}]}

@app.get("/api/history")
def history(uid: int = Depends(get_user)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename,bucket,status,started_at,url FROM uploads WHERE user_id=? ORDER BY id DESC LIMIT 100", (uid,))
    rows = [{"filename":r[0],"bucket":r[1],"status":r[2],"started_at":r[3],"url":r[4]} for r in c.fetchall()]
    conn.close()
    return {"uploads": rows}

@app.post("/api/upload")
async def upload(bucket: str = Form(...), file: UploadFile = File(...), uid: int = Depends(get_user)):
    if not IA_ACCESS or not IA_SECRET:
        raise HTTPException(500, "Internet Archive keys not configured")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO uploads (user_id,filename,bucket,status,started_at,size) VALUES (?,?,?,?,?,?)",
              (uid, file.filename, bucket, "uploading", datetime.utcnow(), 0))
    conn.commit(); conn.close()
    
    url = f"{S3_ENDPOINT}/{bucket}/{file.filename}"
    headers = ia_headers(True)
    headers["Content-Type"] = file.content_type or "application/octet-stream"
    
    r = requests.put(url, data=await file.read(), headers=headers, timeout=600)
    
    status = "completed" if r.status_code in (200,201,204) else "failed"
    download_url = f"https://archive.org/download/{bucket}/{file.filename}"
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE uploads SET status=?,completed_at=?,url=? WHERE user_id=? AND filename=? AND bucket=?",
              (status, datetime.utcnow(), download_url if status=="completed" else None, uid, file.filename, bucket))
    conn.commit(); conn.close()
    
    if not r.ok:
        raise HTTPException(500, f"Upload failed: {r.status_code} {r.text[:200]}")
    return {"ok": True, "url": download_url}
