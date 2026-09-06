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
"""

import os
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

# Required scope for uploading videos
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

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
    """
    creds = None

    # Load cached token if it exists
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # If no valid credentials, go through the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired access token...")
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"credentials.json not found at '{CREDENTIALS_FILE}'.\n"
                    "Download it from Google Cloud Console > APIs & Services > Credentials."
                )
            print("Starting OAuth2 authorization flow — your browser will open...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Persist the token for next run
        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())
        print(f"Token saved to {TOKEN_FILE}")

    return creds
