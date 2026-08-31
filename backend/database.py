"""
MongoDB connection — single client instance reused across requests.
Collections:
  projects  — project configurations
  results   — test results per project
  jobs      — in-flight test jobs (TTL-indexed, expire after 1 h)
"""

import os
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

load_dotenv()

_MONGO_URL: str = os.environ["MONGODB_URL"]
_DB_NAME:   str = os.getenv("MONGODB_DB", "pando_testing_agent")

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(_MONGO_URL, serverSelectionTimeoutMS=10_000)
    return _client


def get_db():
    return get_client()[_DB_NAME]


def check_connection() -> None:
    """
    Ping MongoDB and print a clear connected / failed message to stdout.
    Called once at startup from main.py lifespan.
    """
    # Mask credentials in the URL before printing
    safe_url = _MONGO_URL
    try:
        import re
        safe_url = re.sub(r"//([^:]+):([^@]+)@", r"//\1:****@", _MONGO_URL)
    except Exception:
        pass

    print("\n" + "─" * 60)
    print("  MongoDB connection check")
    print(f"  URL : {safe_url}")
    print(f"  DB  : {_DB_NAME}")
    print("─" * 60)

    try:
        client = get_client()
        client.admin.command("ping")          # raises if unreachable
        info   = client.server_info()
        version = info.get("version", "unknown")
        host    = client.primary or safe_url.split("@")[-1].split("/")[0]
        print(f"  ✓  Connected  (MongoDB {version}  |  host: {host})")
    except ServerSelectionTimeoutError:
        print("  ✗  FAILED — could not reach MongoDB (timeout).")
        print("     Check: network access whitelist, cluster status, MONGODB_URL.")
    except ConnectionFailure as exc:
        print(f"  ✗  FAILED — connection error: {exc}")
        print("     Check: username / password in MONGODB_URL.")
    except Exception as exc:
        print(f"  ✗  FAILED — {exc}")

    print("─" * 60 + "\n")


# ── Collection helpers ────────────────────────────────────────────────────────
def col_projects() -> Collection:
    return get_db()["projects"]


def col_results() -> Collection:
    return get_db()["results"]


def col_jobs() -> Collection:
    return get_db()["jobs"]


# ── Index bootstrap (called once at startup) ──────────────────────────────────
def ensure_indexes() -> None:
    col_projects().create_index([("project_id", ASCENDING)], unique=True)
    col_results().create_index([("project_id", ASCENDING)])
    col_results().create_index([("result_id", ASCENDING)], unique=True)
    col_jobs().create_index([("job_id", ASCENDING)], unique=True)
    # Auto-expire jobs after 3600 seconds (1 hour)
    col_jobs().create_index([("created_at_dt", ASCENDING)], expireAfterSeconds=3600)
