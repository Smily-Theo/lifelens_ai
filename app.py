import os, sqlite3, uuid, math, json
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import jwt

load_dotenv()
BASE = Path(__file__).resolve().parent
# Vercel functions run on an immutable deployment filesystem. Use /tmp there
# for the hackathon demo; local development keeps data beside the app.
IS_VERCEL = bool(os.getenv("VERCEL"))
RUNTIME_DIR = Path("/tmp/lifelens_ai") if IS_VERCEL else BASE
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
DB = Path(os.getenv("LIFELENS_DB_PATH", str(RUNTIME_DIR / "lifelens.db")))
UPLOADS = Path(os.getenv("LIFELENS_UPLOADS_DIR", str(RUNTIME_DIR / "uploads")))
UPLOADS.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "lifelens-dev-secret-change-me")
JWT_SECRET = os.getenv("JWT_SECRET", "lifelens-jwt-secret-change-me")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
ALLOWED_EXT = {"png","jpg","jpeg","webp","gif","mp4","webm","mov"}

# Dindigul MVP geofence (approximate demo rectangle; not an official municipal boundary)
DINDIGUL_BOUNDS = {"south": 10.30, "west": 77.93, "north": 10.52, "east": 78.05}
DINDIGUL_CENTER = [10.3673, 77.9803]

CATEGORIES = [
    ("Road", "road"), ("Street Light", "lighting"), ("Waste", "waste"),
    ("Water", "water"), ("Drainage", "drainage"), ("Accessibility", "accessibility"),
    ("Public Safety", "safety"), ("Environment", "environment"), ("Other", "other")
]
STATUSES = ["Reported", "Verified", "Assigned", "In Progress", "Resolved", "AI Verified"]

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'citizen', points INTEGER DEFAULT 0,
      leaderboard_visible INTEGER DEFAULT 1, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cities(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, state TEXT, country TEXT,
      latitude REAL, longitude REAL, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS incidents(
      id INTEGER PRIMARY KEY AUTOINCREMENT, incident_code TEXT UNIQUE, city_id INTEGER,
      category TEXT, latitude REAL, longitude REAL, severity TEXT, priority_score INTEGER,
      report_count INTEGER DEFAULT 0, status TEXT DEFAULT 'Reported', description TEXT,
      ai_confidence REAL DEFAULT 0, created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS reports(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, city_id INTEGER, incident_id INTEGER,
      category TEXT, description TEXT, image_url TEXT, video_url TEXT, latitude REAL, longitude REAL,
      ai_category TEXT, ai_confidence REAL, severity TEXT, priority_score INTEGER,
      status TEXT DEFAULT 'Reported', created_at TEXT, ai_analysis TEXT
    );
    CREATE TABLE IF NOT EXISTS resolutions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER, authority_id INTEGER,
      before_image TEXT, after_image TEXT, verification_score REAL, verification_status TEXT,
      notes TEXT, resolved_at TEXT
    );
    CREATE TABLE IF NOT EXISTS badges(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, description TEXT
    );
    CREATE TABLE IF NOT EXISTS user_badges(
      user_id INTEGER, badge_id INTEGER, earned_at TEXT,
      UNIQUE(user_id,badge_id)
    );
    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, message TEXT,
      read INTEGER DEFAULT 0, created_at TEXT
    );
    """)
    city = con.execute("SELECT id FROM cities WHERE name='Dindigul'").fetchone()
    if not city:
        con.execute("INSERT INTO cities(name,state,country,latitude,longitude,active) VALUES(?,?,?,?,?,1)",
                    ("Dindigul","Tamil Nadu","India",DINDIGUL_CENTER[0],DINDIGUL_CENTER[1]))
    badges = [
        ("Community Guardian","Submitted a verified civic report."),
        ("Problem Hunter","Submitted 5 verified reports."),
        ("Safety Reporter","Reported a verified safety-critical issue."),
        ("Accessibility Champion","Reported an accessibility issue."),
        ("Green Citizen","Reported an environmental issue.")
    ]
    for b in badges:
        con.execute("INSERT OR IGNORE INTO badges(name,description) VALUES(?,?)", b)
    seed_users(con)
    seed_demo(con)
    con.commit()
    con.close()

def seed_users(con):
    users = [
        ("Demo Citizen","citizen@lifelens.local","Citizen@123","citizen",1200),
        ("Demo Authority","authority@lifelens.local","Authority@123","authority",0),
        ("Demo Admin","admin@lifelens.local","Admin@123","admin",0)
    ]
    for name,email,pw,role,points in users:
        if not con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            con.execute("INSERT INTO users(name,email,password_hash,role,points,created_at) VALUES(?,?,?,?,?,?)",
                        (name,email,generate_password_hash(pw),role,points,now()))

def seed_demo(con):
    if con.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"] > 0: return
    city = con.execute("SELECT id FROM cities WHERE name='Dindigul'").fetchone()["id"]
    demo = [
      ("Road",10.369,77.978,"Critical",94,"Large pothole near a busy road", "High"),
      ("Street Light",10.361,77.985,"High",81,"Broken street light", "High"),
      ("Waste",10.375,77.972,"Medium",61,"Garbage overflow reported", "Medium"),
      ("Water",10.354,77.992,"High",79,"Water leakage on roadside", "High"),
      ("Accessibility",10.371,77.989,"High",87,"Blocked wheelchair ramp", "High"),
      ("Drainage",10.381,77.975,"Medium",65,"Blocked drainage channel", "Medium"),
      ("Public Safety",10.365,77.995,"Critical",92,"Open manhole reported", "Critical"),
      ("Environment",10.349,77.968,"Low",42,"Fallen branch / green waste", "Low"),
      ("Road",10.391,77.986,"High",84,"Damaged footpath", "High"),
      ("Waste",10.343,77.979,"Medium",58,"Waste collection overflow", "Medium")
    ]
    for i,(cat,lat,lon,sev,score,desc,_risk) in enumerate(demo,1):
        code=f"INC-{1000+i}"
        status = "Resolved" if i in (3,8) else ("In Progress" if i in (2,6) else "Verified")
        con.execute("""INSERT INTO incidents(incident_code,city_id,category,latitude,longitude,severity,priority_score,
                    report_count,status,description,ai_confidence,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code,city,cat,lat,lon,sev,score,i+1,status,desc,0.93,now(),now()))
        iid=con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("""INSERT INTO reports(user_id,city_id,incident_id,category,description,latitude,longitude,
                    ai_category,ai_confidence,severity,priority_score,status,created_at,ai_analysis)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (1,city,iid,cat,desc,lat,lon,cat.lower(),0.93,sev,score,status,now(),
                     json.dumps({"mode":"demo","category":cat,"confidence":0.93,"severity":sev})))
    # A resolution for one demo incident
    iid=con.execute("SELECT id FROM incidents WHERE incident_code='INC-1003'").fetchone()["id"]
    con.execute("""INSERT INTO resolutions(incident_id,authority_id,before_image,after_image,verification_score,
                   verification_status,notes,resolved_at) VALUES(?,?,?,?,?,?,?,?)""",
                (iid,2,"","",0.96,"AI Verified","Demo resolution verification.",now()))

def current_user():
    uid=session.get("user_id")
    if not uid: return None
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def login_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        if not current_user():
            if request.path.startswith("/api/"):
                return jsonify({"error":"authentication_required"}),401
            flash("Please login to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return fn(*a,**kw)
    return wrap

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrap(*a,**kw):
            u=current_user()
            if not u or u["role"] not in roles:
                if request.path.startswith("/api/"): return jsonify({"error":"forbidden"}),403
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("home"))
            return fn(*a,**kw)
        return wrap
    return deco

def in_dindigul(lat,lon):
    try:
        lat,lon=float(lat),float(lon)
        return DINDIGUL_BOUNDS["south"] <= lat <= DINDIGUL_BOUNDS["north"] and DINDIGUL_BOUNDS["west"] <= lon <= DINDIGUL_BOUNDS["east"]
    except: return False

def haversine(a,b,c,d):
    R=6371
    p1,p2=math.radians(a),math.radians(c)
    dp=math.radians(c-a); dl=math.radians(d-b)
    x=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R*2*math.asin(math.sqrt(x))

def analyze_image(filename, category_hint="Other", description=""):
    # Deterministic hackathon fallback. Replace this function with a real CV provider later.
    name=(filename or "").lower()+" "+(description or "").lower()+" "+(category_hint or "").lower()
    rules=[
      (["pothole","road","asphalt","hole"],"Road",0.95,"High","Vehicle safety risk detected"),
      (["light","lamp","streetlight"],"Street Light",0.93,"High","Reduced nighttime visibility risk"),
      (["garbage","waste","trash","dump"],"Waste",0.94,"Medium","Public cleanliness concern"),
      (["water","leak","pipe"],"Water",0.92,"High","Potential water loss / road hazard"),
      (["drain","drainage"],"Drainage",0.91,"Medium","Potential flooding risk"),
      (["wheelchair","ramp","sidewalk","access"],"Accessibility",0.93,"High","Accessibility obstruction risk"),
      (["manhole","danger","unsafe"],"Public Safety",0.95,"Critical","Immediate public safety risk"),
      (["tree","branch","green"],"Environment",0.90,"Low","Environmental/obstruction concern")
    ]
    for words,cat,conf,sev,reason in rules:
        if any(w in name for w in words):
            return {"category":cat,"confidence":conf,"severity":sev,"description":f"AI-assisted detection: {cat}.","risk_factors":[reason]}
    return {"category":category_hint if category_hint in [c[0] for c in CATEGORIES] else "Other",
            "confidence":0.78,"severity":"Medium","description":"AI-assisted civic issue analysis completed.","risk_factors":["Manual authority verification recommended"]}

def calc_priority(severity, lat, lon, report_count=1, category="Other"):
    sev={"Low":25,"Medium":45,"High":70,"Critical":90}.get(severity,45)
    safety=10 if category in ("Public Safety","Road","Accessibility") else 5
    reports=min(10,int(report_count))
    # Demo sensitivity heuristic: proximity to center is a proxy only.
    center_dist=haversine(lat,lon,DINDIGUL_CENTER[0],DINDIGUL_CENTER[1])
    loc=max(0,10-int(center_dist*5))
    return min(100,sev+safety+reports+loc)

@app.context_processor
def inject():
    return {
      "user": current_user(),
      "google_maps_key": GOOGLE_MAPS_API_KEY,
      "dindigul_center": DINDIGUL_CENTER,
      "categories": [c[0] for c in CATEGORIES],
      "statuses": STATUSES
    }

@app.route("/")
def home():
    con=db()
    stats={
      "reports":con.execute("SELECT COUNT(*) c FROM reports").fetchone()["c"],
      "critical":con.execute("SELECT COUNT(*) c FROM incidents WHERE severity='Critical' AND status!='Resolved'").fetchone()["c"],
      "in_progress":con.execute("SELECT COUNT(*) c FROM incidents WHERE status='In Progress'").fetchone()["c"],
      "resolved":con.execute("SELECT COUNT(*) c FROM incidents WHERE status IN ('Resolved','AI Verified')").fetchone()["c"],
      "zones":con.execute("SELECT COUNT(DISTINCT ROUND(latitude,2)||','||ROUND(longitude,2)) c FROM incidents").fetchone()["c"]
    }
    con.close()
    return render_template("index.html",stats=stats)

@app.route("/map")
def public_map(): return render_template("map.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        pw=request.form.get("password","")
        con=db(); u=con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone(); con.close()
        if u and check_password_hash(u["password_hash"],pw):
            session["user_id"]=u["id"]
            flash("Welcome back!", "success")
            nxt=request.args.get("next") or request.form.get("next") or url_for("home")
            return redirect(nxt)
        flash("Invalid email or password.", "danger")
    return render_template("auth.html",mode="login")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        pw=request.form.get("password","")
        if len(name)<2 or "@" not in email or len(pw)<8:
            flash("Enter a valid name, email and password (8+ characters).","danger")
            return render_template("auth.html",mode="register")
        con=db()
        try:
            con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                        (name,email,generate_password_hash(pw),"citizen",now()))
            con.commit()
            uid=con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
            session["user_id"]=uid
            flash("Account created. You can now report an issue.","success")
            return redirect(url_for("report"))
        except sqlite3.IntegrityError:
            flash("An account with this email already exists.","danger")
        finally: con.close()
    return render_template("auth.html",mode="register")

@app.route("/logout")
def logout():
    session.clear(); flash("You have been logged out.","success"); return redirect(url_for("home"))

@app.route("/report", methods=["GET","POST"])
@login_required
def report():
    if request.method=="POST":
        return create_report()
    return render_template("report.html")

def create_report():
    u=current_user()
    category=request.form.get("category","Other")
    description=request.form.get("description","").strip()
    lat=request.form.get("latitude","")
    lon=request.form.get("longitude","")
    if not in_dindigul(lat,lon):
        flash("Reports are currently limited to Dindigul City.","danger")
        return redirect(url_for("report"))
    img=request.files.get("image"); vid=request.files.get("video")
    img_url=save_upload(img); vid_url=save_upload(vid)
    analysis=analyze_image(img.filename if img else "",category,description)
    ai_cat=analysis["category"]; severity=analysis["severity"]
    con=db()
    nearby=con.execute("""SELECT * FROM incidents WHERE city_id=(SELECT id FROM cities WHERE name='Dindigul')
                         AND category=?""",(ai_cat,)).fetchall()
    linked=None
    for inc in nearby:
        if haversine(float(lat),float(lon),inc["latitude"],inc["longitude"]) <= 0.15:
            linked=inc; break
    if linked:
        report_count=linked["report_count"]+1
        priority=calc_priority(severity if severity!="Medium" else linked["severity"],float(lat),float(lon),report_count,ai_cat)
        con.execute("UPDATE incidents SET report_count=?,priority_score=?,updated_at=? WHERE id=?",
                    (report_count,priority,now(),linked["id"]))
        incident_id=linked["id"]
    else:
        priority=calc_priority(severity,float(lat),float(lon),1,ai_cat)
        code="INC-"+str(uuid.uuid4())[:8].upper()
        city_id=con.execute("SELECT id FROM cities WHERE name='Dindigul'").fetchone()["id"]
        con.execute("""INSERT INTO incidents(incident_code,city_id,category,latitude,longitude,severity,priority_score,
                    report_count,status,description,ai_confidence,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code,city_id,ai_cat,float(lat),float(lon),severity,priority,1,"Reported",
                     description or analysis["description"],analysis["confidence"],now(),now()))
        incident_id=con.execute("SELECT last_insert_rowid()").fetchone()[0]
    city_id=con.execute("SELECT id FROM cities WHERE name='Dindigul'").fetchone()["id"]
    con.execute("""INSERT INTO reports(user_id,city_id,incident_id,category,description,image_url,video_url,latitude,longitude,
                    ai_category,ai_confidence,severity,priority_score,status,created_at,ai_analysis)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (u["id"],city_id,incident_id,category,description,img_url,vid_url,float(lat),float(lon),
                 ai_cat,analysis["confidence"],severity,priority,"Reported",now(),json.dumps(analysis)))
    con.execute("UPDATE users SET points=points+50 WHERE id=?",(u["id"],))
    con.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                (u["id"],"Report submitted",f"Your report has been linked to incident #{incident_id}.",now()))
    con.commit(); con.close()
    flash(f"Report submitted successfully. Incident #{incident_id}. AI confidence {int(analysis['confidence']*100)}%.","success")
    return redirect(url_for("my_reports"))

def save_upload(f):
    if not f or not f.filename:
        return ""
    ext=f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXT:
        return ""
    # Keep uploads modest for serverless requests. For production-scale media,
    # use direct-to-object-storage uploads (e.g. Vercel Blob/S3).
    name=f"{uuid.uuid4().hex}.{ext}"
    try:
        f.save(UPLOADS / name)
        return "/uploads/"+name
    except (OSError, IOError):
        return ""

@app.route("/uploads/<path:name>")
def uploads(name):
    from flask import send_from_directory
    return send_from_directory(UPLOADS,name)

@app.route("/my-reports")
@login_required
def my_reports():
    con=db()
    rows=con.execute("""SELECT r.*,i.incident_code,i.report_count,i.status incident_status
                        FROM reports r JOIN incidents i ON i.id=r.incident_id
                        WHERE r.user_id=? ORDER BY r.id DESC""",(current_user()["id"],)).fetchall()
    con.close()
    return render_template("my_reports.html",reports=rows)

@app.route("/profile")
@login_required
def profile():
    con=db()
    u=current_user()
    badges=con.execute("""SELECT b.* FROM badges b JOIN user_badges ub ON ub.badge_id=b.id WHERE ub.user_id=?""",(u["id"],)).fetchall()
    reports=con.execute("SELECT COUNT(*) c FROM reports WHERE user_id=?",(u["id"],)).fetchone()["c"]
    verified=con.execute("SELECT COUNT(*) c FROM reports WHERE user_id=? AND status!='Reported'",(u["id"],)).fetchone()["c"]
    resolved=con.execute("""SELECT COUNT(*) c FROM reports WHERE user_id=? AND incident_id IN
                           (SELECT id FROM incidents WHERE status IN ('Resolved','AI Verified'))""",(u["id"],)).fetchone()["c"]
    con.close()
    return render_template("profile.html",u=u,badges=badges,reports=reports,verified=verified,resolved=resolved)

@app.route("/leaderboard")
def leaderboard():
    con=db()
    rows=con.execute("SELECT name,points FROM users WHERE leaderboard_visible=1 ORDER BY points DESC LIMIT 20").fetchall()
    con.close()
    return render_template("leaderboard.html",rows=rows)

@app.route("/authority")
@role_required("authority","admin")
def authority():
    return render_template("authority.html")

@app.route("/authority/incident/<int:iid>")
@role_required("authority","admin")
def incident(iid):
    con=db()
    inc=con.execute("SELECT i.*,c.name city FROM incidents i JOIN cities c ON c.id=i.city_id WHERE i.id=?",(iid,)).fetchone()
    reports=con.execute("SELECT r.*,u.name FROM reports r JOIN users u ON u.id=r.user_id WHERE r.incident_id=? ORDER BY r.id DESC",(iid,)).fetchall()
    res=con.execute("SELECT * FROM resolutions WHERE incident_id=? ORDER BY id DESC LIMIT 1",(iid,)).fetchone()
    con.close()
    return render_template("incident.html",inc=inc,reports=reports,resolution=res)

@app.route("/admin")
@role_required("admin")
def admin():
    con=db()
    users=con.execute("SELECT id,name,email,role,points,created_at FROM users ORDER BY id DESC").fetchall()
    con.close()
    return render_template("admin.html",users=users)

@app.route("/api/issues")
def api_issues():
    con=db()
    rows=con.execute("""SELECT i.*, c.name city FROM incidents i JOIN cities c ON c.id=i.city_id
                        WHERE c.name='Dindigul' ORDER BY i.priority_score DESC""").fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/public-stats")
def api_public_stats():
    con=db()
    data={
      "reports":con.execute("SELECT COUNT(*) c FROM reports").fetchone()["c"],
      "critical":con.execute("SELECT COUNT(*) c FROM incidents WHERE severity='Critical' AND status NOT IN ('Resolved','AI Verified')").fetchone()["c"],
      "resolved":con.execute("SELECT COUNT(*) c FROM incidents WHERE status IN ('Resolved','AI Verified')").fetchone()["c"],
      "in_progress":con.execute("SELECT COUNT(*) c FROM incidents WHERE status='In Progress'").fetchone()["c"]
    }
    con.close(); return jsonify(data)

@app.route("/api/reports/analyze",methods=["POST"])
@login_required
def api_analyze():
    f=request.files.get("image")
    category=request.form.get("category","Other")
    description=request.form.get("description","")
    a=analyze_image(f.filename if f else "",category,description)
    return jsonify(a)

@app.route("/api/authority/incidents")
@role_required("authority","admin")
def api_authority_incidents():
    con=db(); rows=con.execute("SELECT * FROM incidents ORDER BY priority_score DESC").fetchall(); con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/incidents/<int:iid>/status",methods=["PATCH"])
@role_required("authority","admin")
def api_status(iid):
    data=request.get_json(silent=True) or {}
    status=data.get("status")
    if status not in STATUSES: return jsonify({"error":"invalid_status"}),400
    con=db()
    inc=con.execute("SELECT * FROM incidents WHERE id=?",(iid,)).fetchone()
    if not inc: con.close(); return jsonify({"error":"not_found"}),404
    con.execute("UPDATE incidents SET status=?,updated_at=? WHERE id=?",(status,now(),iid))
    users=con.execute("SELECT DISTINCT user_id FROM reports WHERE incident_id=?",(iid,)).fetchall()
    for x in users:
        con.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                    (x["user_id"],"Incident updated",f"{inc['incident_code']} is now {status}.",now()))
    con.commit(); con.close()
    return jsonify({"ok":True,"status":status})

@app.route("/api/incidents/<int:iid>/resolve",methods=["POST"])
@role_required("authority","admin")
def api_resolve(iid):
    before=save_upload(request.files.get("before_image"))
    after=save_upload(request.files.get("after_image"))
    notes=request.form.get("notes","").strip()
    # Demo AI-assisted visual verification: score based on presence of before/after plus current incident severity.
    con=db(); inc=con.execute("SELECT * FROM incidents WHERE id=?",(iid,)).fetchone()
    if not inc: con.close(); return jsonify({"error":"not_found"}),404
    score=0.94 if before and after else 0.82
    status="AI Verified" if score>=0.9 else "Resolved"
    con.execute("INSERT INTO resolutions(incident_id,authority_id,before_image,after_image,verification_score,verification_status,notes,resolved_at) VALUES(?,?,?,?,?,?,?,?)",
                (iid,current_user()["id"],before,after,score,status,notes,now()))
    con.execute("UPDATE incidents SET status=?,updated_at=? WHERE id=?",(status,now(),iid))
    users=con.execute("SELECT DISTINCT user_id FROM reports WHERE incident_id=?",(iid,)).fetchall()
    for x in users:
        con.execute("UPDATE users SET points=points+75 WHERE id=?",(x["user_id"],))
        con.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                    (x["user_id"],"Issue resolved",f"{inc['incident_code']} was marked {status}.",now()))
    con.commit(); con.close()
    return jsonify({"ok":True,"verification_score":score,"verification_status":status,"before_image":before,"after_image":after})

@app.route("/api/analytics")
def analytics():
    con=db()
    bycat=con.execute("SELECT category,COUNT(*) count FROM incidents GROUP BY category ORDER BY count DESC").fetchall()
    bysev=con.execute("SELECT severity,COUNT(*) count FROM incidents GROUP BY severity").fetchall()
    statuses=con.execute("SELECT status,COUNT(*) count FROM incidents GROUP BY status").fetchall()
    con.close()
    return jsonify({"by_category":[dict(x) for x in bycat],"by_severity":[dict(x) for x in bysev],"by_status":[dict(x) for x in statuses],
                    "prediction":{"title":"Emerging Risk Zone","message":"Road-related reports show increasing concentration in the central Dindigul demo dataset.","confidence":"Prototype / demo insight"}})

@app.route("/api/notifications")
@login_required
def notifications():
    con=db(); rows=con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 20",(current_user()["id"],)).fetchall(); con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/profile/toggle-leaderboard",methods=["POST"])
@login_required
def toggle_leaderboard():
    u=current_user(); con=db(); val=0 if u["leaderboard_visible"] else 1
    con.execute("UPDATE users SET leaderboard_visible=? WHERE id=?",(val,u["id"])); con.commit(); con.close()
    return jsonify({"leaderboard_visible":bool(val)})

@app.route("/api/admin/users/<int:uid>/role",methods=["PATCH"])
@role_required("admin")
def change_role(uid):
    data=request.get_json(silent=True) or {}
    role=data.get("role")
    if role not in ("citizen","authority","admin"): return jsonify({"error":"invalid_role"}),400
    con=db(); con.execute("UPDATE users SET role=? WHERE id=?",(role,uid)); con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/health")
def health(): return jsonify({"status":"ok","city":"Dindigul","service":"LifeLens AI"})

init_db()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
