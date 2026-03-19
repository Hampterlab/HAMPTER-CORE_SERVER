import os
from bridge_mcp.config import (
    PROJECTION_CONFIG_PATH,
    ROUTING_CONFIG_PATH,
    VIRTUAL_TOOLS_CONFIG_PATH,
)
from bridge_mcp.tool_projection import ToolProjectionStore
from bridge_mcp.tool_registry import DynamicToolRegistry
from bridge_mcp.device_store import DeviceStore
from bridge_mcp.command import CommandWaiter
from bridge_mcp.mqtt import start_mqtt_listener, publish_to_inport, get_mqtt_pub_client
from bridge_mcp.ipc import IPCAgent
from bridge_mcp.server import BridgeServer
from bridge_mcp.virtual_tool import VirtualToolStore, VirtualToolExecutor
from port_routing import PortStore, RoutingMatrix, PortRouter, AsyncPortRouter

from .app_context import RuntimeContext
from .adapters import LegacyCommandBus, LegacyRoutingBackend
from .services import DeviceSessionManager, CommandService, RoutingService


def build_runtime_context() -> RuntimeContext:
    projection_store = ToolProjectionStore(PROJECTION_CONFIG_PATH)
    tool_registry = DynamicToolRegistry(projection_store)
    device_store = DeviceStore(tool_registry)
    cmd_waiter = CommandWaiter()
    port_store = PortStore()
    routing_matrix = RoutingMatrix(ROUTING_CONFIG_PATH)
    virtual_tool_store = VirtualToolStore(VIRTUAL_TOOLS_CONFIG_PATH)

    ipc_agent = IPCAgent(device_store, cmd_waiter, port_store, None)

    def hybrid_publish(device_id: str, port: str, value: float) -> bool:
        d = device_store.get(device_id)
        transport = "mqtt"
        if d and d.get("protocol") == "ipc":
            transport = "ipc"
            success = ipc_agent.send_port_set(device_id, port, value)
        else:
            success = publish_to_inport(device_id, port, value)
        port_store.record_inport_dispatch(device_id, port, value, transport=transport, success=success)
        return success

    base_router = PortRouter(routing_matrix, hybrid_publish, debug_store=port_store)
    route_workers = int(os.getenv("ROUTE_WORKERS", "2"))
    route_queue_size = int(os.getenv("ROUTE_QUEUE_SIZE", "5000"))
    port_router = AsyncPortRouter(base_router, workers=route_workers, queue_size=route_queue_size)

    ipc_agent.port_router = port_router
    ipc_agent.protocol.port_router = port_router
    ipc_agent.start()

    start_mqtt_listener(device_store, cmd_waiter, port_store, port_router)

    command_bus = LegacyCommandBus(device_store, cmd_waiter, get_mqtt_pub_client, ipc_agent)
    command_service = CommandService(command_bus)
    routing_backend = LegacyRoutingBackend(routing_matrix, port_store, port_router)
    routing_service = RoutingService(routing_backend)
    virtual_tool_executor = VirtualToolExecutor(
        virtual_tool_store,
        device_store,
        cmd_waiter,
        get_mqtt_pub_client,
        ipc_agent,
        command_service=command_service,
    )

    bridge_server = BridgeServer(
        device_store,
        projection_store,
        tool_registry,
        cmd_waiter,
        port_store,
        routing_matrix,
        port_router,
        command_service=command_service,
        ipc_agent=ipc_agent,
        virtual_tool_store=virtual_tool_store,
        virtual_tool_executor=virtual_tool_executor,
    )
    bridge_server.register_all_announced_devices()
    bridge_server.register_virtual_tools()

    return RuntimeContext(
        projection_store=projection_store,
        tool_registry=tool_registry,
        device_store=device_store,
        cmd_waiter=cmd_waiter,
        port_store=port_store,
        routing_matrix=routing_matrix,
        ipc_agent=ipc_agent,
        virtual_tool_store=virtual_tool_store,
        virtual_tool_executor=virtual_tool_executor,
        bridge_server=bridge_server,
        port_router=port_router,
        device_sessions=DeviceSessionManager(device_store),
        command_service=command_service,
        routing_service=routing_service,
    )
