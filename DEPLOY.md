Deployment instructions — simple options

Option A — Render (recommended for Flask apps):
1. Create a GitHub repo and push this project.
2. Go to https://render.com and create a new Web Service.
3. Connect your GitHub repo, choose branch `main`.
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app --log-file -`
6. Add environment variables as needed and deploy.

Option B — Railway / Fly / Render alternatives:
- Similar flow: connect repo, set build and start commands to use `gunicorn`.

Option C — Local (development):

```powershell
py -m pip install -r requirements.txt
py app.py
```

Security notes:
- This app serves user-uploaded HTML/JS. Do not expose it publicly without sandboxing uploads.
- Consider running uploaded projects inside isolated containers or use a static-only hosting flow.
