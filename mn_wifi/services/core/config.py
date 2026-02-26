"""
Configuration management for MeshPay backend.
"""
import os
from pathlib import Path
from typing import List, Optional, Dict
from pydantic import field_validator, Field
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
env_path = Path(__file__).parent.parent.parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"Loaded environment from: {env_path}")
else:
    print(f"No .env file found at: {env_path}")
    # Try to load from current directory
    current_env = Path.cwd() / ".env"
    if current_env.exists():
        load_dotenv(current_env)
        print(f"Loaded environment from: {current_env}")
    else:
        print(f"No .env file found at: {current_env}")

class Settings():
    """Application settings loaded from environment variables."""
    
    # Application Configuration
    app_name: str = os.getenv("APP_NAME", "MeshPay Backend")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")
    debug: bool = os.getenv("DEBUG", False)
    environment: str = os.getenv("ENVIRONMENT", "development")
    
    # API Configuration
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")
    
    # CORS Configuration
    allowed_origins: List[str] = os.getenv("ALLOWED_ORIGINS", ["http://localhost:3000", "http://localhost:5173"])
    allowed_methods: List[str] = os.getenv("ALLOWED_METHODS", ["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    allowed_headers: List[str] = os.getenv("ALLOWED_HEADERS", ["*"])
    
    # Smart Contract Configuration
    meshpay_contract_address: Optional[str] = os.getenv("MESHPAY_CONTRACT_ADDRESS", None)
    meshpay_authority_contract_address: Optional[str] = os.getenv("MESHPAY_AUTHORITY_CONTRACT_ADDRESS", None)
    usdt_contract_address: Optional[str] = os.getenv("USDT_CONTRACT_ADDRESS", None)
    usdc_contract_address: Optional[str] = os.getenv("USDC_CONTRACT_ADDRESS", None)
    wtz_contract_address: Optional[str] = os.getenv("WTZ_CONTRACT_ADDRESS", None)
    
    # Blockchain Configuration
    rpc_url: str = os.getenv("RPC_URL", "https://node.shadownet.etherlink.com")
    chain_id: int = int(os.getenv("CHAIN_ID", "128123"))
    chain_name: str = os.getenv("CHAIN_NAME", "Etherlink Testnet")
    backend_private_key: Optional[str] = os.getenv("BACKEND_PRIVATE_KEY", None)
    blockchain_sync_interval: int = int(os.getenv("BLOCKCHAIN_SYNC_INTERVAL", "10")) # seconds

    # Mesh Network Configuration
    mesh_gateway_url: str = os.getenv("MESH_GATEWAY_URL", "http://10.0.0.254:8080")
    mesh_discovery_enabled: bool = os.getenv("MESH_DISCOVERY_ENABLED", True)
    mesh_authority_port: int = int(os.getenv("MESH_AUTHORITY_PORT", "8080"))
    mesh_scan_network: str = os.getenv("MESH_SCAN_NETWORK", "10.0.0.0/8")
    
    # Mesh Discovery & Relay Configuration
    default_relay_ttl: int = int(os.getenv("DEFAULT_RELAY_TTL", "10"))
    discovery_port: int = int(os.getenv("DISCOVERY_PORT", "9999"))
    discovery_interval: float = float(os.getenv("DISCOVERY_INTERVAL", "5.0")) # Faster updates
    neighbor_timeout: float = float(os.getenv("NEIGHBOR_TIMEOUT", "5.0"))   # Quick pruning
    
    # WebSocket Configuration
    ws_enable: bool = os.getenv("WS_ENABLE", True)
    ws_path: str = os.getenv("WS_PATH", "/ws")
    ws_heartbeat_interval: int = int(os.getenv("WS_HEARTBEAT_INTERVAL", "30"))
    ws_max_connections: int = int(os.getenv("WS_MAX_CONNECTIONS", "100"))
    
    # Token Configuration
    supported_tokens: List[str] = os.getenv("SUPPORTED_TOKENS", ["XTZ", "WTZ", "USDT", "USDC"])
    default_token: str = os.getenv("DEFAULT_TOKEN", "USDT")
    max_transaction_amount: int = int(os.getenv("MAX_TRANSACTION_AMOUNT", "10000000"))
    min_transaction_amount: int = int(os.getenv("MIN_TRANSACTION_AMOUNT", "1"))
    transaction_timeout: float = float(os.getenv("TRANSACTION_TIMEOUT", "30.0"))
    
    # Map Configuration
    default_map_center: List[float] = os.getenv("DEFAULT_MAP_CENTER", [37.7749, -122.4194])
    default_map_zoom: int = int(os.getenv("DEFAULT_MAP_ZOOM", "12"))
    map_update_interval: int = int(os.getenv("MAP_UPDATE_INTERVAL", "5"))
    authority_marker_colors: Dict[str, str] = {
        "online": os.getenv("AUTHORITY_MARKER_COLORS_ONLINE", "#22c55e"),
        "offline": os.getenv("AUTHORITY_MARKER_COLORS_OFFLINE", "#ef4444"), 
        "unknown": os.getenv("AUTHORITY_MARKER_COLORS_UNKNOWN", "#6b7280")
    }
    
    # Cache Configuration
    cache_ttl: int = int(os.getenv("CACHE_TTL", "300"))
    
    # Database Configuration
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./meshpay.db")
    
    # Logging Configuration
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")
    log_file_enabled: bool = os.getenv("LOG_FILE_ENABLED", False)
    log_file_path: str = os.getenv("LOG_FILE_PATH", "./logs/meshpay.log")
    
    # Security Configuration
    secret_key: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    
    # Rate Limiting
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", True)
    rate_limit_requests_per_minute: int = int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "60"))
    rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
    rate_limit_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
    
    # Monitoring
    metrics_enabled: bool = os.getenv("METRICS_ENABLED", True)
    health_check_enabled: bool = os.getenv("HEALTH_CHECK_ENABLED", True)
    
    # Authority Discovery Configuration
    authority_discovery_port: int = int(os.getenv("AUTHORITY_DISCOVERY_PORT", "8080"))
    authority_timeout: float = float(os.getenv("AUTHORITY_TIMEOUT", "10.0"))
    min_quorum_size: int = int(os.getenv("MIN_QUORUM_SIZE", "3"))
    max_authorities: int = int(os.getenv("MAX_AUTHORITIES", "10"))
    network_scan_range: str = os.getenv("NETWORK_SCAN_RANGE", "192.168.1.0/24")
    mesh_bridge_url: str = os.getenv("MESH_BRIDGE_URL", "http://192.168.1.142:8080")
    mesh_timeout: float = float(os.getenv("MESH_TIMEOUT", "10.0"))
    
    @field_validator('meshpay_contract_address', 'meshpay_authority_contract_address', 
             'usdt_contract_address', 'usdc_contract_address')
    @classmethod
    def validate_contract_address(cls, v):
        """Validate contract addresses are proper Ethereum addresses."""
        if v and not (isinstance(v, str) and len(v) == 42 and v.startswith('0x')):
            raise ValueError('Contract address must be a valid Ethereum address (0x...)')
        return v
    
    @field_validator('backend_private_key')
    @classmethod
    def validate_private_key(cls, v):
        """Validate private key format if provided."""
        if v and not (isinstance(v, str) and len(v) in [64, 66]):
            raise ValueError('Private key must be 64 or 66 characters long')
        return v
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "env_prefix": "",
        "extra": "allow",  # Allow extra fields from environment
    }

# Create global settings instance
settings = Settings()

# Supported tokens configuration that matches frontend
SUPPORTED_TOKENS = {
    'XTZ': {
        'address': '0x0000000000000000000000000000000000000000',  # Native token
        'decimals': 18,
        'symbol': 'XTZ',
        'name': 'Tezos',
        'is_native': True,
    },
    'WTZ': {
        'address': settings.wtz_contract_address,  
        'decimals': 18,
        'symbol': 'WTZ',
        'name': 'Wrapped Tezos',
        'is_native': False,
    },
    'USDT': {
        'address': settings.usdt_contract_address,  
        'decimals': 6,
        'symbol': 'USDT',
        'name': 'Tether USD',
        'is_native': False,
    },
    'USDC': {
        'address': settings.usdc_contract_address, 
        'decimals': 6,
        'symbol': 'USDC',
        'name': 'USD Coin',
        'is_native': False,
    },
}

# Mesh Discovery & Relay Constants
DEFAULT_RELAY_TTL = settings.default_relay_ttl
DISCOVERY_PORT = settings.discovery_port
DISCOVERY_INTERVAL = settings.discovery_interval
NEIGHBOR_TIMEOUT = settings.neighbor_timeout

def get_settings():
    return settings