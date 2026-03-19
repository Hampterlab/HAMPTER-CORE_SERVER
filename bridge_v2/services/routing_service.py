from typing import Any, Dict
from ..contracts import RoutingBackend


class RoutingService:
    """
    V2 routing use-case service.
    """

    def __init__(self, backend: RoutingBackend):
        self._backend = backend

    def get_matrix(self) -> Dict[str, Any]:
        return self._backend.get_matrix()

    def get_connections(self) -> list[Dict[str, Any]]:
        return self._backend.get_connections()

    def connect(
        self,
        source: str,
        target: str,
        transform: Dict[str, Any] | None = None,
        enabled: bool = True,
        description: str = "",
    ) -> Dict[str, Any]:
        return self._backend.connect(source, target, transform, enabled, description)

    def disconnect(
        self,
        source: str | None = None,
        target: str | None = None,
        connection_id: str | None = None,
    ) -> bool:
        if connection_id:
            return self._backend.disconnect_by_id(connection_id)
        if source and target:
            return self._backend.disconnect(source, target)
        raise ValueError("source/target or connection_id required")

    def update_connection(self, connection_id: str, data: Dict[str, Any]) -> Dict[str, Any] | None:
        return self._backend.update_connection(connection_id, data)

    def get_stats(self) -> Dict[str, Any]:
        return self._backend.get_stats()
