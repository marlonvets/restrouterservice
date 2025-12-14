from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import httpx
import os
import json
import logging
from dotenv import load_dotenv
from typing import Dict, Any, Optional
from enum import Enum
from pathlib import Path
from db import load_config_from_db, save_config_to_db, configs, FilterConfigs
import uvicorn
load_dotenv(override=True)  # Load .env file if present, override existing env vars
 
# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
            "api_endpoint": "http://localhost:83/data2"
        },
{"field": "branch",
            "op": "eq",

            "value": 'kingston',
            "location": None,
            "api_endpoint": "http://localhost:83/data3"
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

logger.info(f"Application started with {len(NORMALIZED_RULES)} routing rules")


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
    # Also support recursive search if field contains no dots
    def _get_field(d: Dict[str, Any], fld: str):
        if fld is None:
            return None
        
        # If field contains dots, use traditional dot notation traversal
        if "." in fld:
            parts = fld.split(".")
            cur = d
            for p in parts:
                if not isinstance(cur, dict) or p not in cur:
                    return None
                cur = cur[p]
            return cur
        
        # If no dots, recursively search for the field anywhere in the structure
        def _find_recursive(obj, target_key):
            if isinstance(obj, dict):
                if target_key in obj:
                    return obj[target_key]
                for value in obj.values():
                    result = _find_recursive(value, target_key)
                    if result is not None:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = _find_recursive(item, target_key)
                    if result is not None:
                        return result
            return None
        
        return _find_recursive(d, fld)

    actual = _get_field(data, field)

    logger.debug(f"Evaluating rule: field='{field}', op='{op}', expected={expected}, actual={actual}")

    # Comparison semantics:
    # eq  -> actual == expected
    # neq -> actual != expected
    # in  -> expected in actual  (actual must be iterable)
    # nin -> expected not in actual
    try:
        if op == "eq":
            result = actual == expected
        elif op == "neq":
            result = actual != expected
        elif op == "in":
            # if actual is a string or iterable, check membership
            result = actual is not None and (expected in actual)
        elif op == "nin":
            result = actual is None or (expected not in actual)
        else:
            logger.warning(f"Unknown operator '{op}' in rule evaluation")
            result = False

        logger.debug(f"Rule evaluation result: {result}")
        return result
    except Exception as e:
        logger.warning(f"Error during rule evaluation: {e}")
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
async def forward_request(payload: Request):
    """
    Forward payload to target API based on configurable filter condition.

    Filter logic: Routes based on user_type (premium -> API_A, standard -> API_B)
    """
    logger.info(f"Received forward request with payload: {payload.json()}")
    logger.debug(f"HTTP request headers: {dict(payload.headers)}")

    try:
        # First try rule-based matching against payload.data
        if payload.body is None:
            logger.error("Payload data is missing")
            raise HTTPException(status_code=400, detail="Payload data is required for routing")
        
        # Decode body from bytes to JSON
        body_data = await payload.json()
        print("Payload data:", body_data)
        
        logger.debug(f"Searching for matching rule in {len(NORMALIZED_RULES)} rules")
        matched = find_matching_filter(body_data, NORMALIZED_RULES)
        target_url = None
        if matched:
            target_url = matched.get("api_endpoint")
            logger.info(f"Matched rule: field={matched.get('field')}, op={matched.get('op')}, value={matched.get('value')} -> {target_url}")
         
        else:
            logger.warning(f"No rule matched for payload data: {body_data}")
        

        if not target_url:
            logger.error("No matching target API for filter condition")
            # raise HTTPException(status_code=400, detail="No matching target API for filter condition")

        # Prepare headers (merge HTTP request headers + payload headers)
        forward_headers = {
            "Content-Type": "application/json",
        }

        # Add HTTP request headers (excluding hop-by-hop headers)
        hop_by_hop_headers = {
            'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
            'te', 'trailers', 'transfer-encoding', 'upgrade', 'host'
        }

        for header_name, header_value in payload.headers.items():
            if header_name.lower() not in hop_by_hop_headers:
                forward_headers[header_name] = header_value

        # Override/add with payload headers if provided
        logger.info(f"Forwarding request to: {target_url}")
        logger.debug(f"Request headers: {forward_headers}")
        logger.debug(f"Request payload: {body_data}")
        if log_level == "DEBUG":
            logger.debug(f"Full payload body: {json.dumps(body_data, indent=2)}")
            return {
                "rule": matched,
                "data":body_data,
                "target_url": target_url
            }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    target_url,
                    json=payload.body,
                    headers=forward_headers
                )
                logger.info(f"Received response from {target_url}: status={response.status_code}")
                logger.debug(f"Response content: {response.text[:500]}...")  # Log first 500 chars

                # Return downstream response
                return {
                    "status_code": response.status_code,
                    "data": response.json() if response.content else None,
                    "target_url": target_url
                }
            except httpx.RequestError as e:
                logger.error(f"HTTP request failed to {target_url}: {e}")
                raise HTTPException(status_code=502, detail=f"Failed to reach target API: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error during HTTP request to {target_url}: {e}")
                raise HTTPException(status_code=502, detail=f"Error communicating with target API: {str(e)}")

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error in forward_request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Health check endpoint
@app.get("/health")
async def health_check():
    targets = [r.get("api_endpoint") for r in (NORMALIZED_RULES or []) if r.get("api_endpoint")]
    logger.debug(f"Health check requested. Available targets: {targets}")
    return {"status": "healthy", "targets": targets}

# Dynamic config endpoint (for runtime updates)
@app.post("/config/filter")
async def update_filter_config(config: Dict[str, Dict[str, str]]):
    """
    Update filter configuration at runtime.
    Example: {"user_type": {"premium": "http://new-api.com", "standard": "http://old-api.com"}}
    """
    logger.info(f"Updating filter config with: {config}")
    global FILTER_CONFIG, NORMALIZED_RULES
    # Merge updates into existing config (preserve other keys)
    if isinstance(FILTER_CONFIG, dict):
        FILTER_CONFIG.update(config)
    else:
        # if current filter is a list, switch to dict merge semantics
        FILTER_CONFIG = config

    save_config_to_db(FILTER_CONFIG, "default")
    NORMALIZED_RULES = _normalize_filters(FILTER_CONFIG)
    logger.info(f"Filter config updated. New normalized rules count: {len(NORMALIZED_RULES)}")
    return {"message": "Filter config updated", "config": FILTER_CONFIG}

@app.get("/config/getfilters")
async def get_filter_config():
    """
    Retrieve current filter configuration.
    """
    logger.debug("Retrieving current filter configuration")
    return {"config": FILTER_CONFIG}

# Reload config endpoint
@app.post("/config/reload")
async def reload_filter_config():
    """
    Reload filter configuration from database.
    Useful for refreshing configs after external DB updates.
    """
    logger.info("Reloading filter configuration from database")
    global FILTER_CONFIG
    loaded_config = load_config_from_db("default")

    if loaded_config:
        FILTER_CONFIG = loaded_config
        NORMALIZED_RULES = _normalize_filters(FILTER_CONFIG)
        logger.info(f"Filter config reloaded. New normalized rules count: {len(NORMALIZED_RULES)}")
        return {"message": "Filter config reloaded from database", "config": FILTER_CONFIG}
    else:
        logger.warning("No config found in database, using default")
        return {"message": "No config found in database, using default", "config": FILTER_CONFIG}

if __name__ == "__main__":

    uvicorn.run(app, host="0.0.0.0", port=5000)
    

