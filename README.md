# adamcoolsprojet.com — Game Gallery (MVP)

A minimal website for `adamcoolsprojet.com` to upload and share HTML5 games. Projects are uploaded as ZIP files (containing an `index.html`) and served from the local server.

Run locally:

```powershell
py -m pip install -r requirements.txt
py app.py
```

Then open http://localhost:5000

Notes:
- This is a simple MVP for local usage. Do NOT use it as-is in production — running user-uploaded HTML/JS has security and hosting concerns.
- Recommended improvement: add authentication, virus scanning, and run uploaded code in isolated containers if you want public hosting.
