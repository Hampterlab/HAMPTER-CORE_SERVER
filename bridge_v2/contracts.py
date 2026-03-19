from typing import Any, Dict, Protocol, Tuple


class CommandBus(Protocol):
    def execute(
        self,
        device_id: str,
        tool: str,
        args: Any,
        timeout_ms: int | None = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        ...


class RoutingBackend(Protocol):
    def connect(
        self,
        source: str,
        target: str,
        transform: Dict[str, Any] | None,
        enabled: bool,
        description: str,
    ) -> Dict[str, Any]:
        ...

    def disconnect(self, source: str, target: str) -> bool:
        ...

    def disconnect_by_id(self, connection_id: str) -> bool:
        ...

    def update_connection(self, connection_id: str, data: Dict[str, Any]) -> Dict[str, Any] | None:
        ...

    def get_matrix(self) -> Dict[str, Any]:
        ...

    def get_connections(self) -> list[Dict[str, Any]]:
        ...

    def get_stats(self) -> Dict[str, Any]:
        ...
