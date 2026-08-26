"""
AI Tutor backend
=================
Wraps the four tool functions from tutor_mcp_server_v2.py as plain HTTP
endpoints.

Free tier: the first FREE_QUERY_LIMIT requests from any given visitor
(tracked by IP) ride on the server's own OpenAI key, set via the
OPENAI_API_KEY environment variable — never hardcoded in this file, so it
never ends up in the GitHub repo's history. After that, a request needs
the caller's own key in the X-OpenAI-Key header (never logged or
persisted; a fresh OpenAI client is built per-request and discarded).

Run locally:
    pip install -r requirements.txt
    OPENAI_API_KEY=sk-... uvicorn main:app --reload --port 8000

Deploy anywhere that runs a Python process (Railway, Render, Fly.io,
a VPS). Set OPENAI_API_KEY in that host's environment-variable settings
to fund the free tier — the app still works without it, it just has no
free tier and requires every caller to supply their own key.
"""

import json
import os
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from openai import OpenAI, AuthenticationError, APIError

app = FastAPI(title="AI Tutor API")

# Lock this down to your real frontend domain(s) before going live.
# "*" is fine for local development only.
# NOTE: CORS origins must NOT have a trailing slash — browsers send the
# Origin header as scheme://host[:port] only, so "…onrender.com/" never
# matches "…onrender.com" and every real request from that origin gets
# blocked by the browser at the CORS-preflight stage.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ai-tutor-1-xlj0.onrender.com",
        "http://127.0.0.1:5500",
        # "https://your-frontend-domain.com",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_NAME = "gpt-4o-mini"

EXPLANATION_LEVELS = {
    1: "like I'm 5 years old",
    2: "like I'm 10 years old",
    3: "like a high school student",
    4: "like a college student",
    5: "like an expert in the field",
}

# ------------------------------------------------------------- free tier
FREE_QUERY_LIMIT = 5

# Set this in your host's environment variables (Render: Settings ->
# Environment). Deliberately NOT read from a hardcoded string here — a key
# committed to GitHub is one leak away from being drained by bots that
# scan public repos for "sk-" strings, and if the repo is or becomes
# public, GitHub/OpenAI's secret scanning will typically auto-revoke it
# within minutes anyway.
SERVER_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

# In-memory per-IP free-query counter, shared across all four endpoints.
# This is best-effort, not bulletproof: it resets whenever the process
# restarts (Render's free tier spins a service down after ~15 minutes idle
# and back up on the next request, clearing this dict), it isn't shared
# across multiple instances if you ever scale beyond one, and a visitor
# who really wants more than 5 free queries can get them by switching
# networks/VPNs. It stops casual overuse, not a determined abuser — set a
# hard monthly spending limit on the OpenAI account behind this key as the
# actual backstop.
usage_by_ip = defaultdict(int)


def real_client_ip(request: Request) -> str:
    """Render (and most hosts) sit behind a proxy, so request.client.host
    is the proxy's address, not the visitor's. The real IP is the first
    entry in X-Forwarded-For when present."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def client_for(request: Request, x_openai_key: Optional[str]) -> tuple[OpenAI, bool]:
    """Pick which OpenAI key to bill this request to.

    Returns (client, used_free_tier). A caller-supplied key always wins
    and is never rate-limited. Otherwise the first FREE_QUERY_LIMIT
    requests from an IP ride on the server's own key; after that a
    caller-supplied key becomes required.
    """
    if x_openai_key and x_openai_key.startswith("sk-"):
        return OpenAI(api_key=x_openai_key), False

    ip = real_client_ip(request)
    if usage_by_ip[ip] < FREE_QUERY_LIMIT:
        if not SERVER_OPENAI_KEY:
            raise HTTPException(
                status_code=500,
                detail="This server has no free-tier key configured. Add your own OpenAI API key to continue.",
            )
        usage_by_ip[ip] += 1
        return OpenAI(api_key=SERVER_OPENAI_KEY), True

    raise HTTPException(
        status_code=429,
        detail=f"You've used your {FREE_QUERY_LIMIT} free questions. Add your own OpenAI API key to keep going.",
    )


@app.get("/api/usage")
def usage(request: Request):
    """Lets the frontend show 'N free queries left' without spending one."""
    used = usage_by_ip[real_client_ip(request)]
    return {"used": used, "limit": FREE_QUERY_LIMIT, "remaining": max(0, FREE_QUERY_LIMIT - used)}


def auth_wrapped_stream(gen, used_free_tier: bool = False):
    """Run a generator that may raise on first use, surfacing errors as
    text the frontend can show instead of the connection just dying
    mid-stream (streaming responses can't change their status code once
    they've started, so this is the only way the caller finds out)."""
    try:
        for chunk in gen:
            yield chunk
    except AuthenticationError:
        if used_free_tier:
            yield "\n\n[The server's free-tier key isn't working right now. Please try again later, or add your own key.]"
        else:
            yield "\n\n[Your OpenAI key was rejected — check it in Settings.]"
    except APIError as e:
        yield f"\n\n[The AI provider had a problem ({e.__class__.__name__}). Please try again.]"
    except Exception:
        yield "\n\n[Something went wrong generating a response. Please try again.]"


# ---------------------------------------------------------------- explain
class ExplainReq(BaseModel):
    question: str
    level: int = Field(default=3, ge=1, le=5)


@app.post("/api/explain")
def explain(req: ExplainReq, request: Request, x_openai_key: Optional[str] = Header(default=None)):
    client, used_free_tier = client_for(request, x_openai_key)
    level_desc = EXPLANATION_LEVELS.get(req.level, "clearly and concisely")

    def gen():
        stream = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": f"You are a helpful AI Tutor. Explain the following concept {level_desc}."},
                {"role": "user", "content": req.question},
            ],
            stream=True,
            temperature=0.7,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    return StreamingResponse(auth_wrapped_stream(gen(), used_free_tier), media_type="text/plain")


# -------------------------------------------------------------- summarize
class SummarizeReq(BaseModel):
    text: str
    compression_ratio: float = Field(default=0.3, ge=0.1, le=0.8)


@app.post("/api/summarize")
def summarize(req: SummarizeReq, request: Request, x_openai_key: Optional[str] = Header(default=None)):
    client, used_free_tier = client_for(request, x_openai_key)

    def gen():
        stream = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": (
                    "You are a world-class summarizer. Reduce the following text to about "
                    f"{int(req.compression_ratio * 100)}% of its original length while preserving key ideas."
                )},
                {"role": "user", "content": req.text},
            ],
            stream=True,
            temperature=0.5,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    return StreamingResponse(auth_wrapped_stream(gen(), used_free_tier), media_type="text/plain")


# -------------------------------------------------------------- flashcards
class FlashcardsReq(BaseModel):
    topic: str
    num_cards: int = Field(default=5, ge=1, le=20)


@app.post("/api/flashcards")
def flashcards(req: FlashcardsReq, request: Request, x_openai_key: Optional[str] = Header(default=None)):
    client, used_free_tier = client_for(request, x_openai_key)
    try:
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": (
                    'You are an AI that generates study flashcards. Return ONLY a JSON array, '
                    'no markdown fences, no extra text: [{"q": "...", "a": "..."}]'
                )},
                {"role": "user", "content": f"Create {req.num_cards} flashcards about {req.topic}."},
            ],
            temperature=0.8,
        )
    except AuthenticationError:
        if used_free_tier:
            raise HTTPException(status_code=500, detail="The server's free-tier key isn't working right now. Please try again later or use your own key.")
        raise HTTPException(status_code=401, detail="Your OpenAI key was rejected.")
    except APIError as e:
        raise HTTPException(status_code=502, detail=f"The AI provider had a problem ({e.__class__.__name__}). Please try again.")

    raw = resp.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        cards = json.loads(raw)
    except json.JSONDecodeError:
        cards = [{"q": "Couldn't parse a flashcard set — try again.", "a": raw[:300]}]
    return {"cards": cards}


# -------------------------------------------------------------------- quiz
class QuizReq(BaseModel):
    topic: str
    level: int = Field(default=3, ge=1, le=5)
    num_questions: int = Field(default=5, ge=1, le=15)


@app.post("/api/quiz")
def quiz(req: QuizReq, request: Request, x_openai_key: Optional[str] = Header(default=None)):
    client, used_free_tier = client_for(request, x_openai_key)
    level_desc = EXPLANATION_LEVELS.get(req.level, "at an intermediate level")
    try:
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": (
                    f"You are a quiz generator. Create {req.num_questions} multiple-choice questions "
                    f"about {req.topic}, {level_desc}. Return ONLY a JSON array, no markdown fences: "
                    '[{"q": "...", "opts": ["...", "...", "...", "..."], "correct": 0, "note": "short explanation"}]'
                )},
            ],
            temperature=0.7,
        )
    except AuthenticationError:
        if used_free_tier:
            raise HTTPException(status_code=500, detail="The server's free-tier key isn't working right now. Please try again later or use your own key.")
        raise HTTPException(status_code=401, detail="Your OpenAI key was rejected.")
    except APIError as e:
        raise HTTPException(status_code=502, detail=f"The AI provider had a problem ({e.__class__.__name__}). Please try again.")

    raw = resp.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        questions = json.loads(raw)
    except json.JSONDecodeError:
        questions = []
    return {"questions": questions}


@app.get("/api/health")
def health():
    return {"status": "ok"}
