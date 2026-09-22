import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, projects, results, jobs, intake, dashboard, docprojects, observability
from routers import prompt_generator as prompt_generator_router
from database import check_connection, ensure_tables
from config import check_aws_credentials
from seed import seed_if_empty


def _warm_observability_cache() -> None:
    """Full table scans — run off the startup path so the API can accept traffic."""
    try:
        from services.observability_logs import warm_cache
        print("\n  Observability cache warm started in background...\n")
        warm_cache()
        print("\n  Observability cache warm finished.\n")
    except Exception as exc:
        print(f"\n  WARNING: Observability cache warm failed — {exc}")
        print("  Observability endpoints may be slow or fail until DynamoDB is reachable.\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_aws_credentials()
    check_connection()
    try:
        ensure_tables()
        seed_if_empty()
    except Exception as exc:
        print(f"\n  WARNING: Could not initialize database — {exc}")
        print("  The server will start, but database operations may fail.")
        print("  Check IAM permissions, PROJECTS_TABLE/RESULTS_TABLE/JOBS_TABLE, and AWS_REGION.\n")
    threading.Thread(
        target=_warm_observability_cache,
        name="obs-cache-warm",
        daemon=True,
    ).start()
    yield


app = FastAPI(
    title="Pando Testing Agent API",
    version="2.0.0",
    description="Invoice testing orchestration backend — powered by Strands agents and DynamoDB",
    lifespan=lifespan,
)

_frontend_url = os.getenv("FRONTEND_URL", "")
_allowed_origins = [o for o in [
    _frontend_url,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
] if o]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,      prefix="/api")
app.include_router(projects.router,  prefix="/api")
app.include_router(results.router,   prefix="/api")
app.include_router(jobs.router,      prefix="/api")
app.include_router(intake.router,    prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(docprojects.router, prefix="/api")
app.include_router(observability.router, prefix="/api/observability")
app.include_router(prompt_generator_router.router, prefix="/api/prompt-generator")


@app.get("/")
def root():
    return {"status": "ok", "service": "pando-testing-agent", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}
