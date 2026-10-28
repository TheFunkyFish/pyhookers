from fastapi import FastAPI, Request
import asyncio
import json
from typing import Optional, Union
import httpx
from pydantic import BaseModel
# Configuration
LLAMA_CPP_HOST = "localhost"
LLAMA_CPP_PORT = 9931

app = FastAPI(
    title="Local AI Gateway",
    version="v1.0-proxy",
)

def build_openai_payload(payload_data: dict) -> dict:
    """
    Normalizes incoming data into a format strictly compatible 
    with llama.cpp's OpenAI-style endpoints and the standard OpenAI spec.
    """
    
    base_url = f"http://{LLAMA_CPP_HOST}:{LLAMA_CPP_PORT}"
    
    # Determine the target path based on what the harness sends
    if "/completions/" in str(Request):
        pass
    
    request_body = {
        "model": payload_data.get("model", "llama-model"), # Fallback model name
        "messages": [] 
    }

    # Logic to handle both 'prompt' (raw) and 'message' arrays (chat context)
    # Many LLAMA servers accept either, but strictly mimicking OpenAI:
    
    messages_list = []

    # 1. If a flat list of objects is sent under key 'messages' or 'message'
    raw_messages = payload_data.get("messages")
    if isinstance(raw_messages, list) and len(raw_messages) > 0:
        for m in raw_messages:
            msg_obj = {
                "role": m.get("role", "user"),
                "content": str(m.get("content", "")) # Ensure content is string
            }
            # Handle text split edge cases common in some clients
            if hasattr(msg_obj["content"], "__iter__"): 
                pass 
            messages_list.append(msg_obj)

    elif payload_data.get("prompt"):
        # If it's a simple prompt string (e.g., from a completion endpoint)
        request_body["prompt"] = str(payload_data["prompt"])
        
    else:
        # Default to user message if nothing specific found
        request_body["messages"].append({"role": "user", "content": "Prompt context"})

    return {**request_body, "messages": messages_list}

class CompletionBody(BaseModel):
    system: str = 'You are helpful.'
    prompt: str = ''

@app.post("/v1/completions", tags=["Completions"])
async def create_completion(
    req: Request,
    completion: CompletionBody
):
    """
    Proxy for OpenAI-style Completions.
    
    Accepts JSON body with 'message' (list of roles) or just raw text.
    Expects the local coding harness to send something like:
    /completions/chat-model
    
    Body Example:
    [ {"role": "user", "content": "Hello"} ] OR { "prompt": "Hello" }
    """
    
    # Get raw payload from FastAPI request
    #async_body = json.loads(req)

    # Merge model name into payload so llama.cpp knows which model weights to use
    #final_payload = build_openai_payload(async_body)
    
    target_url = f"http://{LLAMA_CPP_HOST}:{LLAMA_CPP_PORT}/v1/completions"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                target_url, 
                json={
                    "system": completion.system,
                    "prompt": completion.prompt

                    #                    "model": "Qwen-3.5-9B",
#                    "messages": [
#                        {
#                            "role": "user",
#                            "content": "Hello! Give me a one-sentence fun fact."
#                        }
#                    ]

                },
                headers={"Content-Type": "application/json"}
            )
            print(f"{response}")
            if response.status_code == 200:
                data = response.json()
                
                # Standard OpenAI expects a 'choices' array in the root or nested inside choices[0]
                # depending on how you configured llama.cpp. We normalize it here.
                return json.dumps(data)                
                return {
                    "object": "text_completion",
                    "model": model_name,
                    "usage": data.get("usage", {}),
                    "choices": [data] if isinstance(data, dict) else data
                }
            
            elif response.status_code >= 400:
                # Re-throw with metadata so your harness knows exactly what failed
                return {
                    "error": True,
                    "detail": "Something went horribly wrong I am afraid. :(",
                    "status_code": response.status_code, 
                    }

    except httpx.TimeoutException:
        return {
            "error": True,
            "detail": "Something went horribly wrong I am afraid, we ran out of time! :(",
            "status_code": 500, 
        }
@app.get("/api/info")
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

@app.get("/v1/completionsx/{model:path}", tags=["Completions"])
async def get_completion(model_name: str):
    """GET version for lightweight status checks or specific server implementations."""
    # Reuse the same logic but point to a slightly different endpoint if your llama setup supports it
    target_url = f"http://{LLAMA_CPP_HOST}:{LLAMA_CPP_PORT}/completions/{model_name}"
    
    async_body = await Request().json() 

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(target_url, json={"prompt": "Check status"})
            
            return {
                "object": "text_completion",
                "choices": [{"index": 0, "text": str(response.text)}],
                "model": model_name
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Helper to define a reusable request class if you want strict typing later
class ChatRequest(BaseModel):
    message: list[dict]
    
from fastapi import BackgroundTasks # Import needed for background tasks if expanding
from pydantic import BaseModel

@app.post("/v1/chat/completions/{model:path}", tags=["Chat"])
async def chat_completion(
    model_name: str, 
    req: Request
):
    """
    Specific endpoint often used by VS Code / Cursor plugins.
    Automatically maps 'message' array to llama.cpp format.
    """
    
    async_body = await req.json()
    
    target_url = f"http://{LLAMA_CPP_HOST}:{LLAMA_CPP_PORT}/completions/{model_name}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Pass the body directly; our build function handles normalization
            response = await client.post(target_url, json=async_body, timeout=30.0)
            
            if response.is_error:
                raise HTTPException(status_code=response.status_code, detail=await req.text())

            return {
                "object": "chat.completion", 
                "choices": [response.json()], 
                "model": model_name
            }
            
    except Exception:
        raise HTTPException(status_code=502, detail="Gateway Timeout")


if __name__ == "__main__":
    import sys
    
    print(f"🚀 Starting AI Proxy V1...")
    print(f"   Gateway Port: 8000")
    print(f"   Target LLAMA: {LLAMA_CPP_HOST}:{LLAMA_CPP_PORT}")
    
    # Run with Uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

