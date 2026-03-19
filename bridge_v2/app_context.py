from dataclasses import dataclass
from typing import Any
from bridge_v2.services import DeviceSessionManager, CommandService, RoutingService


@dataclass
class RuntimeContext:
    # Legacy runtime objects (kept for compatibility)
    projection_store: Any
    tool_registry: Any
    device_store: Any
    cmd_waiter: Any
    port_store: Any
    routing_matrix: Any
    ipc_agent: Any
    virtual_tool_store: Any
    virtual_tool_executor: Any
    bridge_server: Any
    port_router: Any

    # V2 services
    device_sessions: DeviceSessionManager
    command_service: CommandService
    routing_service: RoutingService
