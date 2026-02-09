"""
ABI (Application Binary Interface) definitions for smart contracts.
"""
import json
import os
from pathlib import Path
from typing import List, Dict, Any

# Try multiple methods to find the ABI directory
def _get_abi_dir() -> Path:
    """Get the ABI directory path, handling egg package installations."""
    current_file = Path(__file__)
    
    # Method 1: Standard resolution
    try:
        abi_dir = current_file.resolve().parent
        if abi_dir.exists() and abi_dir.is_dir():
            return abi_dir
    except (OSError, RuntimeError):
        pass
    
    # Method 2: Use __file__ directly without resolve()
    try:
        abi_dir = Path(__file__).parent
        if abi_dir.exists() and abi_dir.is_dir():
            return abi_dir
    except (OSError, RuntimeError):
        pass
    
    # Method 3: Use os.path for egg compatibility
    try:
        abi_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        if abi_dir.exists() and abi_dir.is_dir():
            return abi_dir
    except (OSError, RuntimeError):
        pass
    
    # Method 4: Fallback to current directory
    return Path.cwd()/"mn_wifi"/"services"/"abis"

_ABI_DIR: Path = _get_abi_dir()

def _load_abi(filename: str) -> List[Dict[str, Any]]:
    """Load ABI from JSON file with better error handling."""
    abi_file = _ABI_DIR / filename

    if not abi_file.exists():
        raise FileNotFoundError(f"ABI file not found: {abi_file}")
    
    if not abi_file.is_file():
        raise NotADirectoryError(f"Path is not a file: {abi_file}")
    
    try:
        with open(abi_file, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {abi_file}: {e}")
    except Exception as e:
        raise RuntimeError(f"Error reading {abi_file}: {e}")

    if isinstance(data, list):  # Hardhat style export
        return data

    # Object export: { "abi": [...] }
    if "abi" in data and isinstance(data["abi"], list):
        return data["abi"]

    raise ValueError(f"Unsupported ABI format in {abi_file}")

# Load and expose ABIs as module-level constants
def _load_all_abis():
    """Load all ABIs with comprehensive error handling."""
    abis = {}
    abi_files = {
        "MeshPayABI": "MeshPayMVP.json",
        "MeshPayAuthoritiesABI": "MeshPayAuthorities.json", 
        "ERC20ABI": "ERC20.json"
    }
    
    
    for abi_name, filename in abi_files.items():
        try:
            abis[abi_name] = _load_abi(filename)
        except Exception as e:
            print(f"Warning: Failed to load {abi_name} from {filename}: {e}")
            abis[abi_name] = []  # Provide empty ABI as fallback
    
    return abis

# Load ABIs
_loaded_abis = _load_all_abis()
MeshPayABI: List[Dict[str, Any]] = _loaded_abis["MeshPayABI"]
MeshPayAuthoritiesABI: List[Dict[str, Any]] = _loaded_abis["MeshPayAuthoritiesABI"]
ERC20ABI: List[Dict[str, Any]] = _loaded_abis["ERC20ABI"]

__all__ = ["MeshPayABI", "MeshPayAuthoritiesABI", "ERC20ABI"]