import asyncio
import hashlib, secrets, base64, time, webbrowser
import httpx
from urllib.parse import urlencode
import os

CLIENT_ID    = os.getenv("SWIGGY_CLIENT_ID")
REDIRECT_URI = "http://localhost:8000/callback"
_oauth_code_future = None

_cache = {"token": None, "expires_at": 0}

async def get_valid_token() -> str:
    """Returns a cached token, or re-runs OAuth if expired."""
    if _cache["token"] and time.time() < _cache["expires_at"] - 60:
        return _cache["token"]

    # PKCE
    verifier  = secrets.token_urlsafe(32)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    # Open browser for phone + OTP
    params = urlencode({
        "response_type":         "code",
        "client_id":             CLIENT_ID,
        "redirect_uri":          REDIRECT_URI,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "state":                 secrets.token_urlsafe(16),
        "scope":                 "mcp:tools",
    })
    webbrowser.open(f"https://mcp.swiggy.com/auth/authorize?{params}")

    global _oauth_code_future

    loop = asyncio.get_running_loop()
    _oauth_code_future = loop.create_future()
    print("⏳ Waiting for OAuth redirect on http://localhost:8000/callback ...", flush=True)
    code = await _oauth_code_future

    async with httpx.AsyncClient() as client:
        resp = await client.post("https://mcp.swiggy.com/auth/token", json={
            "grant_type":    "authorization_code",
            "code":          code,
            "code_verifier": verifier,
            "client_id":     CLIENT_ID,
            "redirect_uri":  REDIRECT_URI,
        })
    
    token = resp.json()["access_token"]
    _cache["token"]      = token
    _cache["expires_at"] = time.time() + 432000  # 5 days
    return token
