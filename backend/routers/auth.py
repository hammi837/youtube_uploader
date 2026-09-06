"""
routers/auth.py — OAuth status endpoint.

Security:
  - NEVER returns credentials, tokens, client secrets, or refresh tokens.
  - Only reports whether a valid token exists and is usable.

Path resolution:
  - TOKEN_FILE and CREDENTIALS_FILE env vars are resolved relative to the
    project root (two levels up from this file) when they are relative paths.
  - This makes the backend CWD-independent: running uvicorn from inside
    the backend/ folder still finds token.json at the project root.
"""

import os
from pathlib import Path

from fastapi import APIRouter
from backend.models import AuthStatusResponse

# Project root = G:\youtube-uploader  (this file lives at .../backend/routers/auth.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_path(env_var: str, default: str) -> Path:
    """
    Return an absolute Path for a credential file.

    If the env var value (or the default) is already absolute, use it as-is.
    Otherwise resolve it relative to the project root so the backend works
    regardless of the current working directory.
    """
    raw = os.getenv(env_var, default)
    p = Path(raw)
    return p if p.is_absolute() else _PROJECT_ROOT / p


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatusResponse)
def auth_status() -> AuthStatusResponse:
    """
    Check whether a valid OAuth token exists.

    Returns authenticated=True if token.json exists and credentials load
    without error. Does NOT return any token values or secrets.
    """
    token_file = _resolve_path("TOKEN_FILE", "token.json")

    if not token_file.exists():
        return AuthStatusResponse(
            authenticated=False,
            message="No token found. Run the CLI once to authorize: python main.py --file test.mp4 --title test",
        )

    try:
        from google.oauth2.credentials import Credentials
        scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        if creds and creds.valid:
            return AuthStatusResponse(authenticated=True, message="Authenticated and token is valid.")
        if creds and creds.expired and creds.refresh_token:
            return AuthStatusResponse(
                authenticated=True,
                message="Token is expired but has a refresh token — will auto-refresh on next request.",
            )
        return AuthStatusResponse(
            authenticated=False,
            message="Token exists but is invalid or missing refresh token. Re-run the CLI to re-authorize.",
        )
    except Exception as e:
        # Do NOT include any token content in the error message
        return AuthStatusResponse(
            authenticated=False,
            message=f"Token file could not be read: {type(e).__name__}",
        )
