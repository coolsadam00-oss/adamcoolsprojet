# adamcoolsprojet.com — Game Gallery (MVP)

A minimal website for `adamcoolsprojet.com` to upload and share HTML5 games. Projects are uploaded as ZIP files (containing an `index.html`) and served from the local server.

Run locally:

```powershell
py -m pip install -r requirements.txt
py app.py
```

Then open http://localhost:5000

Deploy on Render:
1. Create a Render account at https://render.com
2. Create a new Web Service and connect to this GitHub repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --log-file -`

Notes:
- This is a simple MVP for local usage. Do NOT use it as-is in production — running user-uploaded HTML/JS has security concerns.
- Recommended improvement: add authentication, virus scanning, and run uploaded code in isolated containers if you want public hosting.
