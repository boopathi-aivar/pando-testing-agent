from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from models.auth import LoginRequest, TokenResponse
from config import settings

router = APIRouter(tags=["auth"])

# ── constants ────────────────────────────────────────────────────────────────
SECRET_KEY = settings.JWT_SECRET_KEY
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

VALID_USER = {
    "email": "pando@aivar.tech",
    "password": "pando@123",
    "name": "Pando Admin",
    "role": "admin",
}

_security = HTTPBearer(auto_error=False)


# ── helpers ───────────────────────────────────────────────────────────────────
def _create_token(email: str) -> str:
    payload = {
        "sub": email,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> dict:
    """FastAPI dependency — validates Bearer JWT and returns the user dict."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        return {"email": email, "name": VALID_USER["name"], "role": VALID_USER["role"]}
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── endpoints ─────────────────────────────────────────────────────────────────
@router.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest):
    """Authenticate with email + password and receive a JWT access token."""
    if req.email != VALID_USER["email"] or req.password != VALID_USER["password"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = _create_token(req.email)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user={"email": req.email, "name": VALID_USER["name"], "role": VALID_USER["role"]},
    )


@router.get("/auth/me")
def me(current_user: dict = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return current_user
