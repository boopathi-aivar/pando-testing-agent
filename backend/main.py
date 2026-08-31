from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, projects, results, jobs, intake
from database import ensure_indexes, check_connection
from seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_connection()
    try:
        ensure_indexes()
        seed_if_empty()
    except Exception as exc:
        print(f"\n  WARNING: Could not initialize database — {exc}")
        print("  The server will start, but database operations will fail.")
        print("  Fix MONGODB_URL in backend/.env and restart.\n")
    yield


app = FastAPI(
    title="Pando Testing Agent API",
    version="2.0.0",
    description="Invoice testing orchestration backend — powered by Strands agents and MongoDB",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,     prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(results.router,  prefix="/api")
app.include_router(jobs.router,     prefix="/api")
app.include_router(intake.router,   prefix="/api")


@app.get("/")
def root():
    return {"status": "ok", "service": "pando-testing-agent", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}
