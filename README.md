# AI Tutor — run it locally

## 1. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...   # Windows: set OPENAI_API_KEY=sk-...  (optional, see below)
uvicorn main:app --reload --port 8000
```

`OPENAI_API_KEY` funds the app's free tier — the first 5 queries from any
visitor (tracked by IP, shared across Explain/Summarize/Flashcards/Quiz)
run on this key automatically, no key prompt needed. It's optional: leave
it unset and the app still works, it just has no free tier and every
visitor needs to paste in their own key from the first request.

Check it's up: open `http://localhost:8000/api/health` → `{"status":"ok"}`.
`http://localhost:8000/api/usage` shows the free-query count for your IP.

## 2. Frontend

Open `index.html` directly in a browser, or serve it so the port matches
the CORS list in `main.py` (VS Code's "Live Server" on
`http://localhost:5500` works out of the box).

There's no upfront key prompt anymore — the app is usable immediately on
the free tier. The sidebar shows how many free queries are left; once
they're used up, a popup appears asking for a personal key
(https://platform.openai.com/api-keys). A key entered there is stored
only in that browser's `localStorage`, sent to your own backend on each
request, never logged or written to disk there, and always bypasses the
free-tier limit (a visitor's own key is never rate-limited).

## 3. Going live

**Backend** — deploy `backend/` to Railway, Render, or Fly.io (any host
that runs a Python process). Set the start command to
`uvicorn main:app --host 0.0.0.0 --port $PORT`, and set `OPENAI_API_KEY`
in that host's environment-variable settings (Render: Settings →
Environment) to fund the free tier.

**Do not paste the key directly into `main.py`.** A key committed to a
GitHub repo lives in that repo's history forever — if the repo is or ever
becomes public, bots that scan GitHub for leaked `sk-` strings typically
find and drain it within minutes, and GitHub/OpenAI's own secret-scanning
will likely auto-revoke it on its own. The environment-variable route
keeps the key out of source control entirely.

**Set a hard spending limit** on the OpenAI account behind this key
(platform.openai.com → Settings → Limits). The 5-free-queries-per-IP cap
in `main.py` stops casual overuse, not a determined abuser — it's
in-memory (resets whenever the backend process restarts, e.g. Render's
free tier spinning down after ~15 minutes idle) and trivially bypassed by
switching networks or using a VPN. Treat the spending cap as the real
backstop, the per-IP limit as a courtesy.

**Frontend** — host the HTML file as a static site (Vercel, Netlify,
GitHub Pages, or served by the backend itself). Before deploying:
- change `API_BASE` near the top of the `<script>` block to your deployed
  backend URL
- add that same frontend domain to `allow_origins` in `main.py`'s CORS
  config (no trailing slash — browsers send `Origin` without one), and
  remove the `localhost`/`127.0.0.1` entries once you're done testing
  against them

**Never** put a visitor-supplied key anywhere but `X-OpenAI-Key` /
`localStorage` — anything written into the HTML/JS source itself is
visible to every visitor via "View Source."
