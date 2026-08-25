"""
AI Tutor backend
=================
Wraps the four tool functions from tutor_mcp_server_v2.py as plain HTTP
endpoints. No server-side OpenAI key is ever used for real requests —
every call must carry the caller's own key in the X-OpenAI-Key header.
That header never gets logged or persisted; a fresh OpenAI client is
built per-request and discarded.

Run locally:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000

Deploy anywhere that runs a Python process (Railway, Render, Fly.io,
a VPS). Nothing here needs an OPENAI_API_KEY environment variable —
that's the point.
"""

import json
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from openai import OpenAI, AuthenticationError

app = FastAPI(title="AI Tutor API")

# Lock this down to your real frontend domain(s) before going live.
# "*" is fine for local development only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
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


def client_for(x_openai_key: Optional[str]) -> OpenAI:
    """Build a per-request OpenAI client from the caller's own key.
    Never falls back to a server-side key."""
    if not x_openai_key or not x_openai_key.startswith("sk-"):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid OpenAI API key. Add your key to continue.",
        )
    return OpenAI(api_key=x_openai_key)


def auth_wrapped_stream(gen):
    """Run a generator that may raise AuthenticationError on first use,
    surfacing it as text the frontend can show instead of a silent 401
    mid-stream (streaming responses can't change their status code once
    they've started)."""
    try:
        for chunk in gen:
            yield chunk
    except AuthenticationError:
        yield "\n\n[Your OpenAI key was rejected — check it in Settings.]"


# ---------------------------------------------------------------- explain
class ExplainReq(BaseModel):
    question: str
    level: int = Field(default=3, ge=1, le=5)


@app.post("/api/explain")
def explain(req: ExplainReq, x_openai_key: Optional[str] = Header(default=None)):
    client = client_for(x_openai_key)
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

    return StreamingResponse(auth_wrapped_stream(gen()), media_type="text/plain")


# -------------------------------------------------------------- summarize
class SummarizeReq(BaseModel):
    text: str
    compression_ratio: float = Field(default=0.3, ge=0.1, le=0.8)


@app.post("/api/summarize")
def summarize(req: SummarizeReq, x_openai_key: Optional[str] = Header(default=None)):
    client = client_for(x_openai_key)

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

    return StreamingResponse(auth_wrapped_stream(gen()), media_type="text/plain")


# -------------------------------------------------------------- flashcards
class FlashcardsReq(BaseModel):
    topic: str
    num_cards: int = Field(default=5, ge=1, le=20)


@app.post("/api/flashcards")
def flashcards(req: FlashcardsReq, x_openai_key: Optional[str] = Header(default=None)):
    client = client_for(x_openai_key)
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
        raise HTTPException(status_code=401, detail="Your OpenAI key was rejected.")

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
def quiz(req: QuizReq, x_openai_key: Optional[str] = Header(default=None)):
    client = client_for(x_openai_key)
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
        raise HTTPException(status_code=401, detail="Your OpenAI key was rejected.")

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
