from typing import Any, Dict, Tuple
from ..contracts import CommandBus


class CommandService:
    """
    V2 command use-case service.
    """

    def __init__(self, bus: CommandBus):
        self._bus = bus

    def execute(
        self,
        device_id: str,
        tool: str,
        args: Any,
        timeout_ms: int | None = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        return self._bus.execute(device_id, tool, args, timeout_ms=timeout_ms)
