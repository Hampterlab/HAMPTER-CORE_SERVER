import json
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from .utils import log, now_iso
from .tool_registry import DynamicToolRegistry

class DeviceStore:
    def __init__(self, tool_registry: DynamicToolRegistry):
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.tool_registry = tool_registry
        self.on_announce_callbacks = []
        self.on_status_callbacks = []
        self.file_path = "config/devices.json"
        self._load()

    def register_on_announce_callback(self, callback):
        self.on_announce_callbacks.append(callback)

    def register_on_status_callback(self, callback):
        self.on_status_callbacks.append(callback)

    def upsert_announce(self, device_id: str, msg: Dict[str, Any], protocol: str = "mqtt"):
        with self._lock:
            d = self._by_id.setdefault(device_id, {"device_id": device_id})
            d["name"] = msg.get("name")
            d["version"] = msg.get("version")
            d["http_base"] = msg.get("http_base")
            d["tools"] = msg.get("tools", [])
            d["last_announce"] = msg
            d["last_seen"] = now_iso()
            d["protocol"] = protocol
        
        tools = msg.get("tools", [])
        device_name = msg.get("name")
        self.tool_registry.register_device_tools(device_id, tools, device_name)
        
        for callback in self.on_announce_callbacks:
            try:
                callback(device_id)
            except Exception as e:
                log(f"[DEVICE] Error in announce callback: {e}")

    def update_status(self, device_id: str, msg: Dict[str, Any]):
        prev_online = None
        new_online = bool(msg.get("online", True))
        with self._lock:
            d = self._by_id.setdefault(device_id, {"device_id": device_id})
            prev_online = d.get("online")
            d["online"] = new_online
            d["uptime_ms"] = msg.get("uptime_ms")
            d["rssi"] = msg.get("rssi")
            d["last_status"] = msg
            d["last_seen"] = now_iso()

        for callback in self.on_status_callbacks:
            try:
                callback(device_id, prev_online, new_online, msg)
            except Exception as e:
                log(f"[DEVICE] Error in status callback: {e}")

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if device_id not in self._by_id:
                return None
            return json.loads(json.dumps(self._by_id[device_id]))

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for d in self._by_id.values():
                dd = json.loads(json.dumps(d))
                last_status = dd.get("last_status", {})
                ts = last_status.get("ts")
                if ts:
                    try:
                        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        dd["online"] = (datetime.now(timezone.utc) - dt).total_seconds() < 90
                    except Exception:
                        pass
                out.append(dd)
            return out

    def get_token(self, device_id: str) -> Optional[str]:
        with self._lock:
            return self._by_id.get(device_id, {}).get("secret_token")

    def set_token(self, device_id: str, token: str):
        with self._lock:
            if device_id in self._by_id:
                self._by_id[device_id]["secret_token"] = token
                self._save()
                log(f"[DEVICE_STORE] Token saved for {device_id}")

    def _load(self):
        try:
            with open(self.file_path, 'r') as f:
                self._by_id = json.load(f)
            log(f"[DEVICE_STORE] Loaded {len(self._by_id)} devices from disk")
        except FileNotFoundError:
            log("[DEVICE_STORE] No existing device store found, starting fresh")
        except Exception as e:
            log(f"[DEVICE_STORE] Failed to load device store: {e}")

    def _save(self):
        try:
            with open(self.file_path, 'w') as f:
                json.dump(self._by_id, f, indent=2)
        except Exception as e:
            log(f"[DEVICE_STORE] Failed to save device store: {e}")
