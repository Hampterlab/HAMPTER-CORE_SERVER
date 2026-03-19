from typing import Any, Dict, Optional


class DeviceSessionManager:
    """
    V2 session facade.
    Keeps all device lifecycle/state access behind one service boundary.
    """

    def __init__(self, device_store):
        self._device_store = device_store

    def list_devices(self) -> list[Dict[str, Any]]:
        return self._device_store.list()

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._device_store.get(device_id)

    def is_online(self, device_id: str) -> bool:
        d = self._device_store.get(device_id)
        return bool(d and d.get("online", False))
