"""APIX dashboard REST API — FastAPI over the in-process backend.

Run (from usecases/apix/application):  uvicorn api.main:app --host 0.0.0.0 --port 8000
The React SPA (application/web) is the sole first-party client; auth is a signed
HttpOnly session cookie, and the chatbot is reached with a bearer JWT minted here.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes_auth import router as auth_router
from .routes_data import router as data_router

app = FastAPI(title="APIX Dashboard API", version="0.1.0")

# The SPA sends the session cookie, so CORS must allow credentials and cannot use
# a wildcard origin — set APIX_SPA_ORIGIN(S) to the dashboard web origin(s).
_origins = [o.strip() for o in os.environ.get(
    "APIX_SPA_ORIGINS", os.environ.get("APIX_SPA_URL", "http://localhost:5173")
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(data_router)


@app.get("/api/health", tags=["health"])
@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "service": "apix-dashboard-api"}
