import json
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List
from .utils import log

class ToolProjectionStore:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self.load_config()
    
    def load_config(self):
        """Load projection configuration from JSON file"""
        try:
            with self._lock:
                if Path(self.config_path).exists():
                    with open(self.config_path, 'r') as f:
                        self.config = json.load(f)
                    log(f"[PROJECTION] Loaded config from {self.config_path}")
                else:
                    self.config = {
                        "devices": {},
                        "global": {
                            "auto_enable_new_devices": True,
                            "auto_enable_new_tools": True
                        }
                    }
                    self.save_config()
                    log(f"[PROJECTION] Created default config at {self.config_path}")
        except Exception as e:
            log(f"[PROJECTION] Error loading config: {e}")
            with self._lock:
                self.config = {"devices": {}, "global": {"auto_enable_new_devices": True, "auto_enable_new_tools": True}}
    
    def save_config(self):
        """Save current configuration to file"""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            log(f"[PROJECTION] Error saving config: {e}")

    def reload_config(self):
        """Reload configuration from disk"""
        self.load_config()
    
    def get_device_projection(self, device_id: str) -> Dict[str, Any]:
        with self._lock:
            return self.config.get("devices", {}).get(device_id, {})
    
    def is_device_enabled(self, device_id: str) -> bool:
        projection = self.get_device_projection(device_id)
        if "enabled" in projection:
            return projection["enabled"]
        return self.config.get("global", {}).get("auto_enable_new_devices", True)
    
    def is_tool_enabled(self, device_id: str, tool_name: str) -> bool:
        if not self.is_device_enabled(device_id):
            return False

        projection = self.get_device_projection(device_id)
        tools = projection.get("tools", {})
        tool_config = tools.get(tool_name, {})
        
        if "enabled" in tool_config:
            return tool_config["enabled"]
        return self.config.get("global", {}).get("auto_enable_new_tools", True)
    
    def get_device_alias(self, device_id: str, device_name: Optional[str] = None) -> str:
        projection = self.get_device_projection(device_id)
        alias = projection.get("device_alias")
        if alias:
            return alias
        return device_name or device_id
    
    def get_tool_projection(self, device_id: str, tool_name: str, original_tool: Dict[str, Any]) -> Dict[str, Any]:
        projection = self.get_device_projection(device_id)
        tools = projection.get("tools", {})
        tool_config = tools.get(tool_name, {})
        
        alias = tool_config.get("alias")
        projected_name = alias if alias else tool_name
        
        projected_desc = tool_config.get("description")
        if projected_desc is None:
            projected_desc = original_tool.get("description", "")
        
        return {
            "name": projected_name,
            "description": projected_desc,
            "parameters": original_tool.get("parameters", {}),
            "original_name": tool_name,
            "device_id": device_id
        }
    
    def auto_add_device(self, device_id: str, device_name: Optional[str], tools: List[Dict[str, Any]]):
        with self._lock:
            changed = False
            devices = self.config.setdefault("devices", {})

            if device_id not in devices:
                device_config = {
                    "enabled": self.config.get("global", {}).get("auto_enable_new_devices", True),
                    "device_alias": None,
                    "tools": {}
                }
                devices[device_id] = device_config
                changed = True
            else:
                device_config = devices[device_id]
                if "tools" not in device_config or not isinstance(device_config.get("tools"), dict):
                    device_config["tools"] = {}
                    changed = True
                
            for tool in tools:
                tool_name = tool.get("name", "")
                if tool_name and tool_name not in device_config["tools"]:
                    device_config["tools"][tool_name] = {
                        "enabled": self.config.get("global", {}).get("auto_enable_new_tools", True),
                        "alias": None,
                        "description": None
                    }
                    changed = True

            if changed:
                self.save_config()
                log(f"[PROJECTION] Synced projection defaults for {device_id} with {len(tools)} tools")

    def update_device_projection(self, device_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update device-level projection fields and persist."""
        with self._lock:
            devices = self.config.setdefault("devices", {})
            device_cfg = devices.setdefault(device_id, {
                "enabled": self.config.get("global", {}).get("auto_enable_new_devices", True),
                "device_alias": None,
                "tools": {}
            })

            if "enabled" in updates:
                device_cfg["enabled"] = bool(updates["enabled"])
            if "device_alias" in updates:
                alias = updates["device_alias"]
                device_cfg["device_alias"] = alias if alias else None
            if "tools" not in device_cfg or not isinstance(device_cfg.get("tools"), dict):
                device_cfg["tools"] = {}

            self.save_config()
            return json.loads(json.dumps(device_cfg))

    def update_tool_projection(self, device_id: str, tool_name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update tool-level projection fields and persist."""
        with self._lock:
            devices = self.config.setdefault("devices", {})
            device_cfg = devices.setdefault(device_id, {
                "enabled": self.config.get("global", {}).get("auto_enable_new_devices", True),
                "device_alias": None,
                "tools": {}
            })
            tools = device_cfg.setdefault("tools", {})
            tool_cfg = tools.setdefault(tool_name, {
                "enabled": self.config.get("global", {}).get("auto_enable_new_tools", True),
                "alias": None,
                "description": None
            })

            if "enabled" in updates:
                tool_cfg["enabled"] = bool(updates["enabled"])
            if "alias" in updates:
                alias = updates["alias"]
                tool_cfg["alias"] = alias if alias else None
            if "description" in updates:
                desc = updates["description"]
                tool_cfg["description"] = desc if desc is not None else None

            self.save_config()
            return json.loads(json.dumps(tool_cfg))
