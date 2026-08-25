# AI Tutor — run it locally

## 1. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

No `.env` file, no `OPENAI_API_KEY` needed here — every request carries the
caller's own key in the `X-OpenAI-Key` header. Your personal key from the
notebook stays out of this entirely.

Check it's up: open `http://localhost:8000/api/health` → `{"status":"ok"}`.

## 2. Frontend

Open `ai-tutor-premium-ui.html` directly in a browser, or serve it so the
port matches the CORS list in `main.py` (VS Code's "Live Server" on
`http://localhost:5500` works out of the box).

On first load you'll be prompted for an OpenAI API key. That's expected —
paste in a key from https://platform.openai.com/api-keys (yours, for
testing). It's stored only in that browser's `localStorage`, sent to your
own backend on each request, never logged or written to disk there.

## 3. Going live

**Backend** — deploy `backend/` to Railway, Render, or Fly.io (any host
that runs a Python process). Set the start command to
`uvicorn main:app --host 0.0.0.0 --port $PORT`. No environment variables
required.

**Frontend** — host the HTML file as a static site (Vercel, Netlify,
GitHub Pages, or served by the backend itself). Before deploying:
- change `API_BASE` near the top of the `<script>` block to your deployed
  backend URL
- add that same frontend domain to `allow_origins` in `main.py`'s CORS
  config, and remove the `localhost` entries

**Never** put an API key inside `main.py`, an `.env` file that ships to
production, or anywhere in the HTML/JS — anything in the frontend source
is visible to every visitor via "View Source." The whole point of the
`X-OpenAI-Key` header pattern is that each visitor's key only ever lives
in their own browser and their own request.
