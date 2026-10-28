import asyncio
import os
import uuid as _uuid_mod

import httpx

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from credential_manager import CredentialManager, constant_time_contains

# Initialize FastAPI App
app = FastAPI(
    title="OpenCode-to-LlamaCPP-Proxy",
    description="Production-ready middleware tunneling OpenCode requests to a headless llama.cpp server."
)

# ---------------------------------------------------------------------------
# Authentication configuration
# Credentials are UUIDs stored in an AES-GCM encrypted file (credentials.enc).
# Only the holder of the master key (key.key or OPENCODE_CRED_KEY env var) can
# decrypt it and check whether a presented credential exists.
# Set AUTH_ENABLED=0 to run without authentication (e.g., local dev only).
# ---------------------------------------------------------------------------
CREDENTIAL_FILE = os.environ.get("OPENCODE_CRED_FILE", "credentials.enc")
KEY_FILE = os.environ.get("OPENCODE_CRED_KEYFILE", "key.key")
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "1") != "0"

_credential_manager: CredentialManager | None = None


def get_credentials() -> list[str]:
    """Lazily load the decrypted UUID credential list."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = CredentialManager(CREDENTIAL_FILE, KEY_FILE)
    return _credential_manager.load()


# Credentials may be sent as `Authorization: Bearer <uuid>` (standard, works with
# OpenAI-compatible clients via apiKey) or the explicit `X-OpenCode-Credential` header.

def authenticate(request: Request) -> str:
    """Validate the request's UUID credential against the encrypted store."""
    if not AUTH_ENABLED:
        return "auth-disabled"

    raw_token = (request.headers.get("authorization") or request.headers.get("x-opencode-credential") or "").strip()
    # Normalize the optional `Bearer` prefix so both header styles work.
    if raw_token[:7].lower() == "bearer ":
        raw_token = raw_token[7:].strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credential",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        candidate_uuid = str(_uuid_mod.UUID(raw_token))  # canonicalize; raises on bad input
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credential format (expected a UUID)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        stored = get_credentials()
    except Exception as exc:  # missing/corrupt store or wrong key -> fail closed
        raise HTTPException(status_code=500, detail=f"Credential store unavailable: {exc}")

    if not constant_time_contains(stored, candidate_uuid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credential not recognized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return candidate_uuid

# Configure CORS to allow inbound connections from OpenCode clients/headless daemons
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Network Configuration
# Change '127.0.0.1' if llama.cpp is hosted on a different internal IP on your remote machine
LLAMA_SERVER_URL = "http://127.0.0.1:9931" 
TIMEOUT_SETTINGS = httpx.Timeout(300.0, connect=10.0) # Long timeout to allow for deep context processing

@app.post("/xxx/v1/chat/completions")
async def proxy_chat_completions(request: Request, credential: str = Depends(authenticate)):
    """
    Intercepts OpenAI-compatible completions payloads from OpenCode 
    and pipes them downstream to the llama.cpp instance while preserving streaming chunks.

    Requires a valid UUID credential (Authorization: Bearer <uuid>).
    """
    body = await request.json()
    
    # Enforce minimum healthy parameters for OpenCode context tracking
    if "messages" in body:
        # You can add custom prompt filtering or dynamic context-clipping mechanics here
        pass

    async def stream_wrapper():
        async with httpx.AsyncClient(timeout=TIMEOUT_SETTINGS) as client:
            try:
                async with client.stream(
                    "POST", 
                    f"{LLAMA_SERVER_URL}/v1/chat/completions", 
                    json=body,
                    headers={"Authorization": "Bearer sk-dummy", "Content-Type": "application/json"}
                ) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.RequestError as exc:
                yield f"data: {{\"error\": \"Proxy failed to reach llama.cpp downstream: {exc}\"}}\n\n".encode("utf-8")

    return StreamingResponse(stream_wrapper(), media_type="text/event-stream")

@app.get("/api/info")
@app.options("/api/info")
async def api_info():
    return {
        "version": 1,
        "pid": 0,
        "hostname": "",
        "urls": [],
        "paths": {
            "tmp": "/opt/pyhooker/tmp/"
        }
    }

@app.api_route("/api/event", methods=["OPTIONS", "POST", "GET"])
async def handle_opencode_telemetry(request: Request):
    """
    Catches and handles OpenCode's analytics/telemetry loops.
    This resolves the '404 Not Found' on OPTIONS/POST /api/event issues natively.
    """
    if request.method == "OPTIONS":
        return JSONResponse(
            content={"status": "CORS preflight OK"}, 
            status_code=status.HTTP_200_OK
        )
    
    try:
        # Silently absorb telemetry body to keep logs clean
        _ = await request.json()
    except Exception:
        pass
        
    return JSONResponse(
        content={"status": "ok", "message": "Telemetry received by proxy"}, 
        status_code=status.HTTP_200_OK
    )


@app.api_route("/chat/completions", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all_falback(request: Request, path: str, credential: str = Depends(authenticate)):
    """
    Catch-all route that dynamically forwards any other unmapped endpoints 
    (e.g., /v1/models) straight to the llama.cpp engine.

    Requires a valid UUID credential (Authorization: Bearer <uuid>).
    """
    if request.method == "OPTIONS":
        return JSONResponse(content={}, status_code=status.HTTP_200_OK)

    method = request.method
    url = f"{LLAMA_SERVER_URL}/{path}"
    
    # Extract request payload if present
    content = await request.body()
    headers = dict(request.headers)
    # Host header needs to match target server to avoid drops
    headers.pop("host", None) 

    async with httpx.AsyncClient(timeout=TIMEOUT_SETTINGS) as client:
        response = await client.request(
            method, url, content=content, headers=headers
        )
        return StreamingResponse(
            response.iter_bytes(), 
            status_code=response.status_code, 
            headers=dict(response.headers)
        )


if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so your local SSH tunnel can resolve traffic directed to 10.0.0.12
    uvicorn.run(app, host="0.0.0.0", port=8082, reload=False)

