from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import httpx
import os
import json
from typing import Dict, Any, Optional
from enum import Enum
from pathlib import Path
from db import load_config_from_db, save_config_to_db, configs, FilterConfigs
import uvicorn

app = FastAPI(title="API Router with Configurable Filters")
app.version = "1.0.0"
app.description = """
This service routes incoming API requests to different target APIs based on configurable filter conditions.
# Configurable target endpoints and filter conditions
"""
 
class TargetAPI(str, Enum):
    API_A = "http://api-a.example.com/endpoint"
    API_B = "http://api-b.example.com/endpoint"

# Example filter condition configuration (can be loaded from env/config file)
DEFAULT_CONFIG = [
{"field": "user",
            "op": "eq",
            "value": 1,
            "location": None,
            "api_endpoint": "http://localhost:83/data"
        },
{"field": "user",
            "op": "eq",
            "value": 2,
            "location": None,
            "api_endpoint": "http://localhost:83/data"
        },
{"field": "branch",
            "op": "eq",
            "value": 'kingston',
            "location": None,
            "api_endpoint": "http://localhost:83/data"
        }
]


 
FILTER_CONFIG = load_config_from_db("default") or DEFAULT_CONFIG

save_config_to_db(FILTER_CONFIG, "default")

# Normalize current config into a list of rule dicts for fast evaluation
def _normalize_filters(raw_cfg):
    """Normalize `raw_cfg` into a list of rule dicts with keys: field, op, value, api_endpoint, location.

    Accepts:
      - list of rule dicts (returned as-is)
      - list of `FilterConfigs` objects (converted via `.to_dict()`)
      - legacy mapping: {field: {value: endpoint_or_meta, ...}, ...}
    """
    try:
        rules = []
        if isinstance(raw_cfg, list):
            for item in raw_cfg:
                if isinstance(item, dict):
                    rules.append(item)
                elif hasattr(item, "to_dict"):
                    rules.append(item.to_dict())
            return rules

        # raw mapping style: {field: {match_value: endpoint_or_meta}}
        if isinstance(raw_cfg, dict):
            for field, mapping in raw_cfg.items():
                if not isinstance(mapping, dict):
                    continue
                for match_value, dest in mapping.items():
                    if isinstance(dest, str):
                        rules.append({
                            "field": field,
                            "op": "eq",
                            "value": match_value,
                            "api_endpoint": dest,
                            "location": None,
                        })
                    elif isinstance(dest, dict):
                        rules.append({
                            "field": field,
                            "op": (dest.get("op") or "eq").lower(),
                            "value": match_value,
                            "api_endpoint": dest.get("api_endpoint") or dest.get("endpoint") or dest.get("target"),
                            "location": (dest.get("location") or None),
                        })
            return rules

        return []
    except Exception as e:
        print("Error normalizing filter config:", e)
        return []

NORMALIZED_RULES = _normalize_filters(FILTER_CONFIG)
print("Loaded normalized rules:")
for r in NORMALIZED_RULES:
    print(r)
# Persist normalized rules to DB to ensure DB uses list-of-rule format
# Canonicalize api_endpoint separators (replace backslashes with forward slashes) before saving
for r in NORMALIZED_RULES:
    if r.get("api_endpoint") and "\\" in r.get("api_endpoint"):
        r["api_endpoint"] = r["api_endpoint"].replace('\\', '/')

save_config_to_db(NORMALIZED_RULES, "default")
class ForwardPayload(BaseModel):
    user_type: str
    data: Dict[str, Any]
    headers: Optional[Dict[str, str]] = None


def _eval_rule(rule: FilterConfigs, data: Dict[str, Any]) -> bool:
    """Evaluate a single rule against a JSON payload (data).

    - rule: {"field": str, "op": "eq|neq|in|nin", "value": Any, ...}
    - data: JSON object to test (typically `payload.data`)

    Returns True if the rule matches the data.
    """
    # support both dict and object shapes
    if isinstance(rule, dict):
        field = rule.get("field")
        op = (rule.get("op") or "").lower()
        expected = rule.get("value")
    else:
        # object-like rule (e.g., FilterConfigs instance)
        field = getattr(rule, "field", None)
        op = getattr(rule, "op", None)
        try:
            # op may be an Enum
            op = op.value if hasattr(op, "value") else str(op)
        except Exception:
            op = str(op)
        op = (op or "").lower()
        expected = getattr(rule, "value", None)

    # Support simple dot-notation for nested fields (e.g. "user.id")
    def _get_field(d: Dict[str, Any], fld: str):
        if fld is None:
            return None
        parts = fld.split(".")
        cur = d
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
        return cur

    actual = _get_field(data, field)

    # Comparison semantics:
    # eq  -> actual == expected
    # neq -> actual != expected
    # in  -> expected in actual  (actual must be iterable)
    # nin -> expected not in actual
    try:
        if op == "eq":
            return actual == expected
        if op == "neq":
            return actual != expected
        if op == "in":
            # if actual is a string or iterable, check membership
            return actual is not None and (expected in actual)
        if op == "nin":
            return actual is None or (expected not in actual)
    except Exception:
        # Any error during comparison -> treat as non-match
        return False

    # Unknown op -> no match
    return False


def find_matching_filter(data: Dict[str, Any], filters: list[FilterConfigs] = None) -> Optional[Dict[str, Any]]:
    """Return the first rule from `filters` that matches `data`, or None.

    - `filters` defaults to `DEFAULT_CONFIG`.
    - Each rule is expected to be a dict with `field`, `op`, `value`, and `api_endpoint`.
    """
    filters = filters if filters is not None else DEFAULT_CONFIG
    for rule in filters:
        if _eval_rule(rule, data):
            return rule
    return None

@app.post("/forward")
async def forward_request(payload: ForwardPayload):
    """
    Forward payload to target API based on configurable filter condition.
    
    Filter logic: Routes based on user_type (premium -> API_A, standard -> API_B)
    """
    # First try rule-based matching against payload.data
    matched = find_matching_filter(payload.data, NORMALIZED_RULES)
    target_url = None
    if matched:
        target_url = matched.api_endpoint

    if not target_url:
        print("No rule matched")
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
    targets = [r.get("api_endpoint") for r in (NORMALIZED_RULES or []) if r.get("api_endpoint")]
    return {"status": "healthy", "targets": targets}

# Dynamic config endpoint (for runtime updates)
@app.post("/config/filter")
async def update_filter_config(config: Dict[str, Dict[str, str]]):
    """
    Update filter configuration at runtime.
    Example: {"user_type": {"premium": "http://new-api.com", "standard": "http://old-api.com"}}
    """
    global FILTER_CONFIG, NORMALIZED_RULES
    # Merge updates into existing config (preserve other keys)
    if isinstance(FILTER_CONFIG, dict):
        FILTER_CONFIG.update(config)
    else:
        # if current filter is a list, switch to dict merge semantics
        FILTER_CONFIG = config

    save_config_to_db(FILTER_CONFIG, "default")
    NORMALIZED_RULES = _normalize_filters(FILTER_CONFIG)
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
        NORMALIZED_RULES = _normalize_filters(FILTER_CONFIG)
        return {"message": "Filter config reloaded from database", "config": FILTER_CONFIG}
    else:
        return {"message": "No config found in database, using default", "config": FILTER_CONFIG}

if __name__ == "__main__":

    uvicorn.run(app, host="0.0.0.0", port=5000)
    
