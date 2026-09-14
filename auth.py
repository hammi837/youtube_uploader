"""
auth.py — Handles Google OAuth2 authentication for the YouTube Data API.

Flow:
  1. Check for an existing token.json (cached credentials).
  2. If missing or expired, run the browser-based OAuth consent flow.
  3. Save the refreshed/new token back to disk.

Path resolution:
  - CREDENTIALS_FILE and TOKEN_FILE env vars are resolved relative to the
    directory containing this file (the project root) when they are relative.
  - This means the CLI and the FastAPI backend both find the same files
    regardless of the working directory from which they are invoked.

Error handling:
  - invalid_grant (revoked/expired refresh token): the bad token is renamed
    to token.json.invalid so it is not loaded again.  credentials.json is
    NEVER touched.  YouTubeAuthError is raised so callers can guide the user
    through re-authorization.
  - Access tokens and refresh tokens are NEVER written to logs.
"""

import logging
import os
from pathlib import Path

import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Required scopes for uploading videos and playlist management
# Note: Adding the youtube scope will trigger reauthorization for existing tokens
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # For playlist operations
]

# auth.py lives at the project root, so __file__ gives us a stable anchor.
_HERE = Path(__file__).resolve().parent


def _resolve(env_var: str, default: str) -> str:
    """Resolve a credential path relative to the project root if not absolute."""
    raw = os.getenv(env_var, default)
    p = Path(raw)
    return str(p if p.is_absolute() else _HERE / p)


CREDENTIALS_FILE = _resolve("CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = _resolve("TOKEN_FILE", "token.json")


def get_credentials() -> Credentials:
    """
    Return valid Google OAuth2 credentials, refreshing or re-authorizing as needed.

    Raises:
        YouTubeAuthError: When the refresh token has been revoked or expired
            (``invalid_grant``).  The bad token file is renamed so it cannot be
            re-used.  credentials.json is preserved.
        FileNotFoundError: When credentials.json is missing and no token exists.
    """
    from backend.services.video.exceptions import YouTubeAuthError

    creds = None

    # Load cached token if it exists
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except ValueError as exc:
            # This happens when scopes don't match between token and requested scopes
            logger.warning("Token scopes mismatch (likely due to scope additions), reauthorization required: %s", exc)
            # Rename the outdated token to force reauthorization
            invalid_token_path = TOKEN_FILE + ".invalid"
            try:
                os.rename(TOKEN_FILE, invalid_token_path)
                logger.warning("Token with outdated scopes renamed to %s", Path(invalid_token_path).name)
            except OSError as rename_exc:
                logger.warning("Could not rename outdated token file: %s", rename_exc)
            creds = None

    # If no valid credentials, go through the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired access token...")
            try:
                creds.refresh(Request())
            except google.auth.exceptions.RefreshError as exc:
                err_str = str(exc)
                # Detect invalid_grant specifically — this is a permanent failure.
                # The same token will never work again; retrying is pointless.
                if "invalid_grant" in err_str.lower():
                    # Rename the bad token so it is not loaded on the next run.
                    # Do NOT expose the token contents in logs.
                    invalid_token_path = TOKEN_FILE + ".invalid"
                    try:
                        os.rename(TOKEN_FILE, invalid_token_path)
                        logger.warning(
                            "YouTube refresh token has been revoked or expired "
                            "(invalid_grant). Bad token renamed to %s. "
                            "credentials.json is preserved. "
                            "Reauthorize by running: python auth.py",
                            Path(invalid_token_path).name,
                        )
                    except OSError as rename_exc:
                        logger.warning(
                            "Could not rename invalid token file: %s", rename_exc
                        )
                    raise YouTubeAuthError(
                        "YouTube OAuth token is invalid (invalid_grant). "
                        "The refresh token has been revoked or expired, or the required scopes have changed. "
                        "To reauthorize: delete token.json and run 'python auth.py' "
                        "(or visit the /api/auth/reauthorize endpoint if available). "
                        "credentials.json has NOT been modified."
                    ) from exc
                # Other refresh errors (network, etc.) — re-raise as-is
                raise
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"credentials.json not found at '{CREDENTIALS_FILE}'.\n"
                    "Download it from Google Cloud Console > APIs & Services > Credentials."
                )
            logger.info("Starting OAuth2 authorization flow — your browser will open...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Persist the token for next run
        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())
        logger.info("Token saved to %s", Path(TOKEN_FILE).name)

    return creds

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        get_credentials()
        print(f"Successfully authenticated and saved to {TOKEN_FILE}")
    except Exception as e:
        print(f"Error authenticating: {e}")

