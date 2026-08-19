import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

# import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from .database import add_db_session_middleware, close_engine, initialize_engine
from .db import close_db, init_db
from .logging import configure_logging
from .routers import (
    analytics,
    api,
    client_api,
    companion_shares,
    context_engine_testing,
    conversations,
    dialogmachine,
    memories,
    memory_v2_testing,
    oauth_cli,
    public_companions,
    public_shares,
    sessions,
    tools,
)
from .routers.v2 import (
    behaviors_router,
    inbox_router,
    memory_router,
    messages_router,
    relationships_router,
    sessions_router,
    summaries_router,
    websockets_router,
)
from .routers.v2.websockets import connection_manager
from .routers.voice import (
    cancel_active_session_tasks,
    voice_connection_manager,
)
from .routers.voice import (
    openclaw_router as voice_openclaw_router,
)
from .routers.voice import (
    twilio_router as voice_twilio_router,
)
from .routers.voice import (
    v2_router as voice_v2_router,
)
from .routers.voice import (
    workspace_router as voice_workspace_router,
)
from .tracing import setup_tracing

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database connection pool (asyncpg - existing)
    try:
        await init_db()
        log.info("Database initialized successfully (asyncpg)")
    except Exception as e:
        log.error(f"Failed to initialize database: {e}")
        raise

    # Initialize SQLAlchemy engine (new - coexists with asyncpg during migration)
    try:
        await initialize_engine()
        log.info("SQLAlchemy engine initialized successfully")
    except Exception as e:
        log.error(f"Failed to initialize SQLAlchemy engine: {e}")
        raise

    # Pre-warm Silero VAD model to avoid cold-start latency on first connection
    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        _vad_warmup = SileroVADAnalyzer(sample_rate=16000)
        del _vad_warmup
        log.info("Silero VAD model pre-warmed successfully")
    except Exception as e:
        log.warning(f"Failed to pre-warm Silero VAD model (non-fatal): {e}")

    log.info("Application startup complete")
    try:
        yield  # ⬅️  app runs here
    finally:
        # 1) Close WebSocket connections gracefully
        try:
            await connection_manager.graceful_shutdown()
        except Exception:
            pass
        # 2) Close voice WebSocket connections gracefully
        try:
            await voice_connection_manager.graceful_shutdown()
        except Exception:
            pass
        # 3) Cancel long-lived session tasks first to stop any DB work
        try:
            await cancel_active_session_tasks()
        except Exception:
            pass
        # 4) Cleanup SQLAlchemy engine
        try:
            await close_engine()
        except Exception:
            pass
        # 5) Cleanup asyncpg database connections
        await close_db()
        log.info("Application shutdown complete")


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="Emotion Machine API",
        version="0.1.0",
        description="API for building AI companions",
        routes=app.routes,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your Clerk JWT token",
        },
        "ProjectApiKey": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "APIKey",
            "description": "Use `Authorization: Bearer <PROJECT_API_KEY>`",
        },
    }
    # Apply security to all /api routes
    for path_data in openapi_schema["paths"].values():
        for operation in path_data.values():
            if isinstance(operation, dict) and "tags" in operation:
                if "api" in operation["tags"]:
                    operation["security"] = [{"BearerAuth": []}]
                if "dialogmachine" in operation["tags"]:
                    operation["security"] = [{"BearerAuth": []}]
                if "client-api" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]
                if "v2-behaviors" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]
                if "v2-relationships" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]
                if "v2-messages" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]
                if "v2-websockets" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]
                if "v2-voice" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]
                if "v2-sessions" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]
                if "v2-memory" in operation["tags"]:
                    operation["security"] = [{"ProjectApiKey": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app = FastAPI(title="Emotion Machine Service", version="0.1.0", lifespan=lifespan)

app.openapi = custom_openapi


def _parse_origins(env_value: str | None) -> list[str]:
    try:
        if not env_value:
            return ["*"]
        parts = [p.strip() for p in env_value.split(",")]
        return [p for p in parts if p]
    except Exception:
        return ["*"]


allow_origins = _parse_origins(os.getenv("CORS_ALLOW_ORIGINS"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_origin_regex=r"https://emotion-machine.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add database middleware (pure ASGI - supports SSE streaming)
# Provides request-scoped asyncpg connection via RequestContext
# SQLAlchemy session creation disabled for now (create_session=False)
add_db_session_middleware(app, create_session=False)

app.include_router(sessions.router)
app.include_router(api.router)
app.include_router(dialogmachine.router)
app.include_router(oauth_cli.router)
app.include_router(conversations.router)
app.include_router(analytics.router)
app.include_router(public_shares.router)
app.include_router(memories.router)
app.include_router(companion_shares.router)
app.include_router(public_companions.router)
app.include_router(client_api.router)
app.include_router(context_engine_testing.router)
app.include_router(memory_v2_testing.router)
app.include_router(tools.router)

# API v2 routers
app.include_router(behaviors_router)
app.include_router(inbox_router)
app.include_router(memory_router)
app.include_router(relationships_router)
app.include_router(messages_router)
app.include_router(sessions_router)
app.include_router(summaries_router)
app.include_router(websockets_router)
app.include_router(voice_v2_router)
app.include_router(voice_twilio_router)
app.include_router(voice_openclaw_router)
app.include_router(voice_workspace_router)

# Initialize tracing after all routers are added
setup_tracing(app)


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/api/auth/test-token")
async def test_auth_instructions():
    """Instructions for testing authenticated endpoints"""
    return {
        "message": "To test authenticated endpoints in FastAPI docs:",
        "steps": [
            "1. Go to your web client at http://localhost:3001",
            "2. Open browser developer tools (F12)",
            "3. In console, run: localStorage.getItem('clerk-db-jwt')",
            "4. Copy the JWT token (without quotes)",
            "5. Come back to this docs page",
            "6. Click the 'Authorize' button at the top",
            "7. Enter: Bearer YOUR_JWT_TOKEN",
            "8. Click 'Authorize' and close the modal",
            "9. Now you can test the /api/companions endpoint",
        ],
        "alternative": "You can also get the token from Network tab when making requests from the web client",
        "note": "The token expires, so you may need to get a fresh one if requests start failing",
    }
