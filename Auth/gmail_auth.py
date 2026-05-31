import os
import socket
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

CREDENTIALS_FILE = "google_credentials.json"
PORT = 8080


async def get_gmail_token() -> str:
    """Get Gmail token, handling stale port from cancelled auth flows."""

    token_file = "gmail_token.json"
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    elif not creds or not creds.valid:
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(f"❌ {CREDENTIALS_FILE} not found!")

        # --- Free the port if it's stuck from a previous cancelled flow ---
        if _is_port_in_use(PORT):
            print(f"⚠️  Port {PORT} already in use — freeing it...")
            _free_port(PORT)

        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(
            port=PORT,
            prompt='consent'
        )

    with open(token_file, "w") as f:
        f.write(creds.to_json())

    return creds.token


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _free_port(port: int):
    """Kill whatever process is holding the port."""
    import subprocess, sys
    if sys.platform == "win32":
        result = subprocess.run(
            f"for /f \"tokens=5\" %a in ('netstat -aon ^| find \":{port}\"') do taskkill /F /PID %a",
            shell=True, capture_output=True
        )
    else:
        # Linux / macOS
        result = subprocess.run(
            f"fuser -k {port}/tcp",
            shell=True, capture_output=True
        )
    # Give the OS a moment to release the port
    import time
    time.sleep(0.5)