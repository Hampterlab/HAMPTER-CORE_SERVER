from typing import Any, Dict, Tuple
from bridge_mcp.command import publish_cmd


class LegacyCommandBus:
    """
    Adapter from V2 command contract to legacy command implementation.
    """

    def __init__(self, device_store, cmd_waiter, mqtt_client_getter, ipc_agent):
        self._device_store = device_store
        self._cmd_waiter = cmd_waiter
        self._mqtt_client_getter = mqtt_client_getter
        self._ipc_agent = ipc_agent

    def execute(
        self,
        device_id: str,
        tool: str,
        args: Any,
        timeout_ms: int | None = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        kwargs: Dict[str, Any] = {
            "ipc_agent": self._ipc_agent,
        }
        if timeout_ms is not None:
            kwargs["timeout_ms"] = timeout_ms

        return publish_cmd(
            self._device_store,
            self._cmd_waiter,
            self._mqtt_client_getter(),
            device_id,
            tool,
            args,
            **kwargs,
        )
