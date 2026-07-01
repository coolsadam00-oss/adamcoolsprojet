# Adam Studio Render Deploy

Use this when deploying Adam Studio as its own Render website.

## Render settings

Create a new Render Web Service from this repository.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn adam_studio_app:app --log-file -
```

The Adam Studio page renders at `/` for this standalone service.

## Files needed

- `adam_studio_app.py`
- `templates/adam_studio.html`
- `static/adam-studio.css`
- `static/adam-studio-logo.jpg`
- `requirements.txt`

Optional blueprint/reference file:

- `render-adam-studio.yaml`
