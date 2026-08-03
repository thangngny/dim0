"""FastAPI application setup."""

import asyncio
import logging
import os

from argparse import ArgumentParser
from contextlib import asynccontextmanager

import uvicorn

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from topix.api.router import (
    billing,
    boards,
    chats,
    collab,
    documents,
    files,
    finance,
    integration,
    mini_app_state,
    sharing,
    subscriptions,
    tools,
    users,
    utils,
)
from topix.collab.agent_bridge import AgentBoardBridge
from topix.collab.room import RoomRegistry
from topix.config.config import Config
from topix.datatypes.stage import StageEnum
from topix.nlp.pipeline.parsing import ParsingPipeline
from topix.setup import setup
from topix.store.chat import ChatStore
from topix.store.email_verification import EmailVerificationStore
from topix.store.graph import GraphStore
from topix.store.mini_app_state import MiniAppStateStore
from topix.store.password_reset import PasswordResetStore
from topix.store.postgres.pool import create_pool
from topix.store.postgres.schema import apply_schema
from topix.store.redis.store import RedisStore
from topix.store.subscription import SubscriptionStore
from topix.store.user import UserStore
from topix.store.user_billing import UserBillingStore
from topix.utils.logging import logging_config

logging_config()
logger = logging.getLogger(__name__)


def create_app(stage: StageEnum):
    """Create and configure the FastAPI application."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan context manager."""
        # One shared Postgres pool for every store. Per-store pools used to
        # multiply our connection footprint and exhaust Postgres under burst.
        app.pg_pool = await create_pool()

        # Apply idempotent schema so existing self-hosted DBs pick up additive
        # changes (new tables, new columns) without a manual migration step.
        await apply_schema(app.pg_pool)

        # Initialize stores
        app.graph_store = GraphStore()
        await app.graph_store.open(app.pg_pool)
        app.user_store = UserStore()
        await app.user_store.open(app.pg_pool)
        app.chat_store = ChatStore()
        await app.chat_store.open(app.pg_pool)
        app.user_billing_store = UserBillingStore()
        await app.user_billing_store.open(app.pg_pool)
        app.email_verification_store = EmailVerificationStore()
        await app.email_verification_store.open(app.pg_pool)
        app.password_reset_store = PasswordResetStore()
        await app.password_reset_store.open(app.pg_pool)
        app.mini_app_state_store = MiniAppStateStore()
        await app.mini_app_state_store.open(app.pg_pool)
        try:
            app.subscription_store = SubscriptionStore()
            await app.subscription_store.open()
        except Exception as _sub_err:
            # Newsfeed/subscription features require LLM API keys.
            # Degrade gracefully: canvas, board, collab, and integration all
            # continue to work; only subscription-based newsfeed is disabled.
            logger.warning(
                "SubscriptionStore failed to init (newsfeed disabled): %s", _sub_err
            )
            app.subscription_store = None
        try:
            app.parser_pipeline = ParsingPipeline()
        except Exception as _parse_err:
            logger.warning(
                "ParsingPipeline failed to init (document parsing disabled): %s", _parse_err
            )
            app.parser_pipeline = None

        # Initialize Redis
        app.redis_store = RedisStore.from_config()

        # Per-worker collab room registry (in-process; single-worker for v1).
        app.collab_rooms = RoomRegistry()
        # Agent → room bridge: agent tools call this so their edits
        # surface to live peers via `peer-op` (collab-archi §5.3 Phase 2).
        app.agent_board_bridge = AgentBoardBridge(
            graph_store=app.graph_store,
            registry=app.collab_rooms,
        )

        yield

        # Close stores. They no-op the pool close when sharing, then we close
        # the shared pool exactly once at the end.
        await app.graph_store.close()
        await app.user_store.close()
        await app.chat_store.close()
        await app.user_billing_store.close()
        await app.email_verification_store.close()
        await app.password_reset_store.close()
        await app.mini_app_state_store.close()
        await app.subscription_store.close()
        # Close Redis
        await app.redis_store.close()
        await app.pg_pool.close()

    # Expose interactive docs and the OpenAPI schema only in local/dev. In
    # staging/prod they leak the full route + payload surface, which makes
    # targeted abuse easier, so disable them outright.
    docs_enabled = stage in (StageEnum.LOCAL, StageEnum.DEV)
    app = FastAPI(
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(boards.router)
    app.include_router(chats.router)
    app.include_router(collab.router)
    app.include_router(sharing.router)
    app.include_router(tools.router)
    app.include_router(users.router)
    app.include_router(subscriptions.router)
    app.include_router(billing.router)
    app.include_router(mini_app_state.router)
    app.include_router(utils.router)
    app.include_router(finance.router)
    app.include_router(files.router)
    app.include_router(documents.router)
    app.include_router(integration.router)

    # Optionally serve the built webui (single-origin deploy: Replit, etc.).
    _mount_webui_if_configured(app)

    return app


def _mount_webui_if_configured(app: FastAPI) -> None:
    """Serve the built webui (launcher + canvas) from DIM0_WEBUI_DIST.

    No-op unless ``DIM0_WEBUI_DIST`` points at a built dist dir, so the
    default separate-origin deploy (webui on its own host) is unaffected.

    A browser page-load to a client-side route like ``/boards/{id}`` sends
    ``Accept: text/html`` with no Authorization. The API route
    ``GET /boards/{graph_id}`` would 401 that navigation, so an HTTP
    middleware intercepts HTML navigations and serves the SPA shell
    instead. Real API calls (fetch → ``Accept */*``/``application/json``,
    or carrying ``Authorization``) pass through to the routers.
    """
    dist = os.getenv("DIM0_WEBUI_DIST")
    if not dist or not os.path.isdir(dist):
        return

    assets_dir = os.path.join(dist, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="webui-assets")

    @app.middleware("http")
    async def _serve_spa_for_navigation(request: Request, call_next):
        # Serve the SPA shell for browser navigations only: a GET with
        # Accept: text/html, no Authorization, not an API/docs path, and not
        # a real static file. Everything else (API fetches, assets, docs)
        # falls through to the routers / static mount.
        accept = request.headers.get("accept", "")
        path = request.url.path
        candidate = os.path.join(dist, path.lstrip("/"))
        is_navigation = (
            request.method == "GET"
            and "text/html" in accept
            and not request.headers.get("authorization")
            and not path.startswith(("/docs", "/openapi", "/redoc", "/integration", "/utils"))
            and not (path != "/" and os.path.isfile(candidate))
        )
        if is_navigation:
            index = os.path.join(dist, "index.html")
            if os.path.isfile(index):
                return FileResponse(index)
        return await call_next(request)

    @app.get("/{full_path:path}")
    async def _serve_webui(full_path: str) -> FileResponse:
        """Serve a known static file, else fall back to the SPA index."""
        candidate = os.path.join(dist, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(dist, "index.html"))


async def main(args) -> tuple[FastAPI, int]:
    """Run the application entry point."""
    await setup(stage=args.stage, env_filename=args.env_file)

    config: Config = Config.instance()

    app = create_app(stage=args.stage)

    return app, args.port or config.app.settings.port


if __name__ == "__main__":
    args = ArgumentParser(description="Run the Dim0 application.")
    args.add_argument(
        "--stage",
        default=StageEnum.LOCAL,
        help="The stage to run the application in.",
        choices=list(StageEnum)
    )
    args.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to run the application on."
    )
    args.add_argument(
        "--env-file",
        type=str,
        default=".env",
        help="Overridden name to the .env file to load. For example: .env.staging",
    )
    args = args.parse_args()

    app, port = asyncio.run(main(args))

    host = "0.0.0.0"
    logger.info(f"Starting Dim0 API on {host}:{port}...")

    uvicorn.run(app, host=host, port=port, log_level="info")
