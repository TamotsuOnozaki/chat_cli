"""FastAPI minimal entrypoint.

Responsibilities ONLY:
  * load .env from common locations (non-destructive)
  * create FastAPI app with CORS
  * include modular routers (conversation / admin / agents)
  * mount frontend static assets
All orchestration/state logic is in separate modules.
"""
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# internal side-effect imports early so Ruff sees them as module-level
from . import core  # noqa: F401
from .routers import conversation, admin, agents


def _load_dotenv_multi() -> None:
    """Load .env files from several plausible locations without overriding existing vars."""
    for candidate in [
        Path.cwd() / ".env",                               # current working dir
        Path(__file__).resolve().parent.parent / ".env",   # playground/.env
        Path(__file__).resolve().parents[2] / ".env",      # repository root .env
    ]:
        try:
            if candidate.exists():
                load_dotenv(candidate, override=False)
        except Exception:
            pass


_load_dotenv_multi()
app = FastAPI(title="Motivator Orchestrator Playground")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversation.router)
app.include_router(admin.router)
app.include_router(agents.router)


@app.get("/api/healthz")
async def healthz():  # async for symmetry with other endpoints
    return {"ok": True}


# Serve frontend (if present)
static_dir = Path(__file__).resolve().parent.parent / "frontend"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="frontend")
