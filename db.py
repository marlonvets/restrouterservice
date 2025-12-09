from importlib.metadata import requires
import sqlite3
import json
from typing import Dict, Any, Optional, List, Required
from pathlib import Path
# Database initialization
DB_PATH = Path("filter_configs.db")
class Op(Enum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NIN = "nin"

class Location(Enum):
    BODY = "body"
    HEADERS = "headers"
    QUERY = "query"
    PATH = "path"


class filter_config:
    """
    Represents a filter configuration structure as used in the API.
    
    """
    field: Required[str]
    op: Required[Op]
    value: Required[Any]
    location: Optional[Location]
    api_endpoint: Required[str]

    def __init__(self, field: str = None, op: Op = None, value: Any = None, location: Location = None, api_endpoint: str = None):
        self.field = field
        self.op = op
        self.value = value
        self.location = location
        self.api_endpoint = api_endpoint
    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "op": self.op.value,
            "value": self.value,
            "location": self.location.value if self.location else None,
            "api_endpoint": self.api_endpoint
        }
    def from_dict(self, data: Dict[str, Any]):
        self.field = data.get("field")
        self.op = Op(data.get("op"))
        self.value = data.get("value")
        loc = data.get("location")
        self.location = Location(loc) if loc else None
        self.api_endpoint = data.get("api_endpoint")

 

def init_db():
    """Initialize SQLite database and create tables if they don't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Create table for storing filter configurations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS filter_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_name TEXT UNIQUE NOT NULL,
            config_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def load_config_from_db(config_name: str = "default") -> Dict[str, Any]:
    """Load filter configuration from database."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    if config_name.lower()=='all':
        query = "SELECT config_name, config_data FROM filter_configs"
        cursor.execute(query)
        rows = cursor.fetchall()
        configs = {}
        for row in rows:
            configs[row[0]] = json.loads(row[1])
    else:  
  
        cursor.execute("SELECT config_data FROM filter_configs WHERE config_name = ?", (config_name,))
        row = cursor.fetchone()
        configs = json.loads(row[0])
    conn.close()
    if configs:
        return configs
    return None

def save_config_to_db(config: Dict[str, Any], config_name: str = "default"):
    """Save filter configuration to database."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    config_data = json.dumps(config)
    
    # Upsert: insert or update
    cursor.execute("""
        INSERT INTO filter_configs (config_name, config_data)
        VALUES (?, ?)
        ON CONFLICT(config_name) DO UPDATE SET config_data=?, updated_at=CURRENT_TIMESTAMP
    """, (config_name, config_data, config_data))
    
    conn.commit()
    conn.close()
# Default filter configuration
init_db()
configs = load_config_from_db("all")