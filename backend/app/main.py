"""VisionPath AI backend entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes, ws
from app.config import VERSION, Settings, get_settings
from app.services.privacy_service import install_log_redaction
from app.state import AppState


def create_app(settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_state = state is None
        app.state.vp = state or AppState(settings)

        async def sweeper():
            while True:
                await asyncio.sleep(settings.session_sweep_interval_s)
                try:
                    await app.state.vp.sessions.sweep()
                except Exception:  # never let housekeeping crash the server
                    logging.getLogger("visionpath").exception("session sweep failed")

        async def waiting_room_poller():
            """Low-overhead 1 Hz worker: reads derived metrics only, never decodes video."""
            while True:
                await asyncio.sleep(settings.waiting_sample_interval_s)
                try:
                    for sess in list(app.state.vp.sessions._sessions.values()):
                        app.state.vp.waiting_tick(sess)
                except Exception:
                    logging.getLogger("visionpath").exception("waiting-room poll failed")

        sweep_task = asyncio.create_task(sweeper())
        wr_task = asyncio.create_task(waiting_room_poller())
        yield
        sweep_task.cancel()
        wr_task.cancel()
        await app.state.vp.sessions.shutdown()
        if owns_state:  # an injected state (tests) owns its own model lifecycle
            app.state.vp.vision.close()

    app = FastAPI(
        title="VisionPath AI", version=VERSION, lifespan=lifespan,
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url=None, openapi_url="/openapi.json" if settings.env != "production" else None,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Session-Token"],
    )
    app.include_router(routes.router)
    app.include_router(ws.router)
    return app


app = create_app()
