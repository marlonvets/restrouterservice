from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import httpx
import os
import json
from typing import Dict, Any, Optional
from enum import Enum
from pathlib import Path
from db import load_config_from_db, save_config_to_db, configs
import uvicorn

app = FastAPI(title="API Router with Configurable Filters")
# Configurable target endpoints and filter conditions
class TargetAPI(str, Enum):
    API_A = "http://api-a.example.com/endpoint"
    API_B = "http://api-b.example.com/endpoint"

# Example filter condition configuration (can be loaded from env/config file)
DEFAULT_CONFIG = {
    "user_type": {
        "premium": TargetAPI.API_A.value,
        "standard": TargetAPI.API_B.value
    }
}

 
FILTER_CONFIG = load_config_from_db("default") or DEFAULT_CONFIG
save_config_to_db(FILTER_CONFIG, "default")

class ForwardPayload(BaseModel):
    user_type: str
    data: Dict[str, Any]
    headers: Optional[Dict[str, str]] = None

@app.post("/forward")
async def forward_request(payload: ForwardPayload):
    """
    Forward payload to target API based on configurable filter condition.
    
    Filter logic: Routes based on user_type (premium -> API_A, standard -> API_B)
    """
    # Apply configurable filter condition
    target_url = FILTER_CONFIG.get("user_type", {}).get(payload.user_type)
    
    if not target_url:
        raise HTTPException(status_code=400, detail="No matching target API for filter condition")
    
    # Prepare headers (forward client headers + custom ones)
    forward_headers = {
        "Content-Type": "application/json",
        **(payload.headers or {})
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            target_url,
            json=payload.data,
            headers=forward_headers
        )
    
    # Return downstream response
    return {
        "status_code": response.status_code,
        "data": response.json() if response.content else None,
        "target_url": target_url
    }

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "targets": list(FILTER_CONFIG["user_type"].values())}

# Dynamic config endpoint (for runtime updates)
@app.post("/config/filter")
async def update_filter_config(config: Dict[str, Dict[str, str]]):
    """
    Update filter configuration at runtime.
    Example: {"user_type": {"premium": "http://new-api.com", "standard": "http://old-api.com"}}
    """
    global FILTER_CONFIG
    FILTER_CONFIG.update(config)
    save_config_to_db(FILTER_CONFIG, "default")
    return {"message": "Filter config updated", "config": FILTER_CONFIG}

@app.get("/config/getfilters")
async def get_filter_config():
    """
    Retrieve current filter configuration.
    """
    return {"config": FILTER_CONFIG}
# Reload config endpoint
@app.post("/config/reload")
async def reload_filter_config():
    """
    Reload filter configuration from database.
    Useful for refreshing configs after external DB updates.
    """
    global FILTER_CONFIG
    loaded_config = load_config_from_db("default")
    
    if loaded_config:
        FILTER_CONFIG = loaded_config
        return {"message": "Filter config reloaded from database", "config": FILTER_CONFIG}
    else:
        return {"message": "No config found in database, using default", "config": FILTER_CONFIG}

if __name__ == "__main__":

    uvicorn.run(app, host="0.0.0.0", port=8000)
