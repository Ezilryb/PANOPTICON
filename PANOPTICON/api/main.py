"""Application FastAPI PANOPTICON."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import init_db
from api.routes import cameras, daemon, events, objects, persons, rules, websocket
from daemon.orchestrator import orchestrator
from shared.config import settings
from shared.logging_utils import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    await init_db()
    await orchestrator.start()
    yield
    await orchestrator.stop()


app = FastAPI(
    title="PANOPTICON",
    description="Système de vision multi-caméras orchestré par DAEMON",
    version="0.1.0-mvp",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(daemon.router)
app.include_router(cameras.router)
app.include_router(events.router)
app.include_router(persons.router)
app.include_router(rules.router)
app.include_router(objects.router)
app.include_router(websocket.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "profile": settings.panopticon_profile}
