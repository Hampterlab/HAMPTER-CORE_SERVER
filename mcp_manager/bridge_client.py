import requests
from typing import Dict, Any, List
from .config import log

class BridgeAPIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
    
    def get_devices(self) -> List[Dict[str, Any]]:
        try:
            response = requests.get(f"{self.base_url}/devices", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error getting devices: {e}")
            return []
    
    def get_ports(self) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.base_url}/ports", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error getting ports: {e}")
            return {"devices": [], "outports": [], "inports": []}
    
    def get_routing(self) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.base_url}/routing", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error getting routing: {e}")
            return {"outports": [], "inports": [], "matrix": {}, "connection_count": 0}

    def get_connections(self) -> List[Dict[str, Any]]:
        try:
            response = requests.get(f"{self.base_url}/routing/connections", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error getting connections: {e}")
            return []

    def get_virtual_tools(self) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.base_url}/virtual-tools", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error getting virtual tools: {e}")
            return {}
    
    def connect_ports(self, source: str, target: str, transform: dict = None, description: str = "", enabled: bool = True) -> Dict[str, Any]:
        try:
            response = requests.post(f"{self.base_url}/routing/connect", json={
                "source": source,
                "target": target,
                "transform": transform or {},
                "description": description,
                "enabled": enabled
            }, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error connecting ports: {e}")
            return {"ok": False, "error": str(e)}
    
    def disconnect_ports(self, source: str = None, target: str = None, connection_id: str = None) -> Dict[str, Any]:
        try:
            data = {}
            if connection_id:
                data["connection_id"] = connection_id
            else:
                data["source"] = source
                data["target"] = target
            response = requests.post(f"{self.base_url}/routing/disconnect", json=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error disconnecting ports: {e}")
            return {"ok": False, "error": str(e)}
    
    def update_connection(self, connection_id: str, updates: dict) -> Dict[str, Any]:
        try:
            response = requests.put(f"{self.base_url}/routing/connection/{connection_id}", json=updates, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"[BRIDGE_API] Error updating connection: {e}")
            return {"ok": False, "error": str(e)}
    
    def health_check(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/healthz", timeout=5)
            return response.status_code == 200
        except Exception:
            return False
