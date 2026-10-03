# LifeLens AI

**See the Problem. Predict the Impact. Fix the Future.**

Hackathon-ready civic intelligence MVP focused on **Dindigul City, Tamil Nadu, India**.

## Included
- Public website: no login required
- Login required only to submit reports
- Citizen, Authority and Admin roles
- Dindigul-focused interactive map
- AI-assisted image analysis with deterministic fallback demo mode
- Voice reporting using browser speech recognition when supported
- Severity and priority scoring
- Duplicate/incident clustering
- Public heatmap
- Authority dashboard
- Before/after resolution verification
- Predictive analytics prototype
- Citizen points, badges and leaderboard
- Notifications
- Mobile-responsive UI
- SQLite database with seeded Dindigul demo data
- Google Maps option with automatic Leaflet fallback
- No secret keys required for the default demo mode

## Windows setup

```powershell
cd lifelens_ai
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
venv\Scripts\python.exe run.py
```

Open http://127.0.0.1:5000

This avoids the PowerShell Activate.ps1 execution-policy problem by invoking the virtual-environment Python directly.

## Demo accounts
- Citizen: `citizen@lifelens.local` / `Citizen@123`
- Authority: `authority@lifelens.local` / `Authority@123`
- Admin: `admin@lifelens.local` / `Admin@123`

## Google Maps
Set `GOOGLE_MAPS_API_KEY` in `.env` to use Google Maps. If it is blank, the app automatically uses OpenStreetMap/Leaflet so the demo remains runnable.

## AI
The default application uses an on-device/demo analysis fallback so the hackathon demo works without an external AI key. The `/api/reports/analyze` endpoint is structured so a real computer-vision/LLM provider can be connected later.

## Notes
The Dindigul boundary is an MVP geofence, not an official municipal boundary. Replace `DINDIGUL_BOUNDS` in `app.py` with an authoritative polygon if required for production.


## Vercel deployment

This project is prepared for Vercel's native Flask/Python deployment. Vercel detects Flask automatically; deploy from the directory containing `app.py`.

### CLI

```powershell
npm install -g vercel
vercel login
vercel
vercel --prod
```

Set these optional environment variables in Vercel Project Settings → Environment Variables:

- `FLASK_SECRET_KEY` — a long random secret
- `JWT_SECRET` — a long random secret
- `GOOGLE_MAPS_API_KEY` — optional
- `AI_API_KEY` — optional for future real AI integration

The included MVP uses deterministic AI-assisted analysis, so it can run without an external AI key.

### Important Vercel data note

The Vercel version uses `/tmp` for SQLite and uploaded evidence because the deployment filesystem is not persistent. This is suitable for a hackathon demo, but a production rollout should move incident data to a hosted database and evidence to object storage such as Vercel Blob/S3.

### Vercel root

Deploy from the folder containing:

- `app.py`
- `requirements.txt`
- `vercel.json`
- `pyproject.toml`
- `templates/`
- `static/`

Do not deploy the parent ZIP folder that contains another copy of the project.
