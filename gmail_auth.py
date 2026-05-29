import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

CREDENTIALS_FILE = "google_credentials.json"


async def get_gmail_token() -> str:
    """Get Gmail token"""
    
    token_file = "gmail_token.json"
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    elif not creds or not creds.valid:
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(f"❌ {CREDENTIALS_FILE} not found!")

        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        
        # Use run_local_server with a fixed port to avoid conflicts
        creds = flow.run_local_server(
            port=8080,           # Using 8080 instead of random port
            prompt='consent'
        )

    with open(token_file, "w") as f:
        f.write(creds.to_json())

    return creds.token