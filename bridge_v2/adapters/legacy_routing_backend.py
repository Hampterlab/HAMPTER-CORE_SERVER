from typing import Any, Dict


class LegacyRoutingBackend:
    """
    Adapter from V2 routing contract to legacy matrix/router implementation.
    """

    def __init__(self, routing_matrix, port_store, port_router):
        self._routing_matrix = routing_matrix
        self._port_store = port_store
        self._port_router = port_router

    def connect(
        self,
        source: str,
        target: str,
        transform: Dict[str, Any] | None,
        enabled: bool,
        description: str,
    ) -> Dict[str, Any]:
        return self._routing_matrix.connect(source, target, transform, enabled, description)

    def disconnect(self, source: str, target: str) -> bool:
        return self._routing_matrix.disconnect(source, target)

    def disconnect_by_id(self, connection_id: str) -> bool:
        return self._routing_matrix.disconnect_by_id(connection_id)

    def update_connection(self, connection_id: str, data: Dict[str, Any]) -> Dict[str, Any] | None:
        return self._routing_matrix.update_connection(connection_id, data)

    def get_matrix(self) -> Dict[str, Any]:
        return self._routing_matrix.get_matrix_view(self._port_store)

    def get_connections(self) -> list[Dict[str, Any]]:
        return self._routing_matrix.get_all_connections()

    def get_stats(self) -> Dict[str, Any]:
        return self._port_router.get_stats()
