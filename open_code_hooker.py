import asyncio
import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Initialize FastAPI App
app = FastAPI(
    title="OpenCode-to-LlamaCPP-Proxy",
    description="Production-ready middleware tunneling OpenCode requests to a headless llama.cpp server."
)

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
async def proxy_chat_completions(request: Request):
    """
    Intercepts OpenAI-compatible completions payloads from OpenCode 
    and pipes them downstream to the llama.cpp instance while preserving streaming chunks.
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
async def catch_all_falback(request: Request, path: str):
    """
    Catch-all route that dynamically forwards any other unmapped endpoints 
    (e.g., /v1/models) straight to the llama.cpp engine.
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
    uvicorn.run("proxy:app", host="0.0.0.0", port=8082, reload=False)

