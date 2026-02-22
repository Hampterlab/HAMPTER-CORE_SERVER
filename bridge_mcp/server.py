import json
import threading
from typing import List, Union, Any, Dict, Set
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent, Resource

from .utils import log, convert_response_to_content_list, json_schema_to_pydantic_model
from .device_store import DeviceStore
from .tool_projection import ToolProjectionStore
from .tool_registry import DynamicToolRegistry
from .command import CommandWaiter, publish_cmd
from .mqtt import get_mqtt_pub_client, publish_to_inport
from .ops_hub import HampterOpsHub
from port_routing import PortStore, RoutingMatrix

class BridgeServer:
    def __init__(self, 
                 device_store: DeviceStore, 
                 projection_store: ToolProjectionStore, 
                 tool_registry: DynamicToolRegistry,
                 cmd_waiter: CommandWaiter,
                 port_store: PortStore,
                 routing_matrix: RoutingMatrix,
                 port_router,
                 command_service=None,
                 ipc_agent=None,
                 virtual_tool_store=None,
                 virtual_tool_executor=None):
        self.mcp = FastMCP("bridge-mcp")
        self.device_store = device_store
        self.projection_store = projection_store
        self.tool_registry = tool_registry
        self.cmd_waiter = cmd_waiter
        self.port_store = port_store
        self.routing_matrix = routing_matrix
        self.port_router = port_router
        self.command_service = command_service
        self.ipc_agent = ipc_agent
        self.virtual_tool_store = virtual_tool_store
        self.virtual_tool_executor = virtual_tool_executor
        self._registered_virtual_tools = set()  # Track registered virtual tool names
        self._dynamic_tool_names_by_device: Dict[str, Set[str]] = {}
        self._sync_lock = threading.Lock()
        self.ops_hub = HampterOpsHub(
            device_store=self.device_store,
            projection_store=self.projection_store,
            routing_matrix=self.routing_matrix,
            virtual_tool_store=self.virtual_tool_store,
            virtual_tool_executor=self.virtual_tool_executor,
            port_store=self.port_store,
            execute_device_tool=self._execute_device_tool_raw,
            sync_dynamic_tools=self.register_all_announced_devices,
            sync_virtual_tools=self.register_virtual_tools,
            reload_all=self.reload_all_tools,
        )
        
        self.setup_resources()
        self.setup_tools()
        
        # Register callbacks for device lifecycle changes.
        self.device_store.register_on_announce_callback(self.on_device_announced)
        self.device_store.register_on_status_callback(self.on_device_status_updated)

    def _get_mcp_tools_dict(self):
        if hasattr(self.mcp, "_tools") and isinstance(self.mcp._tools, dict):
            return self.mcp._tools
        if hasattr(self.mcp, "_tool_manager"):
            tm = self.mcp._tool_manager
            if hasattr(tm, "_tools") and isinstance(tm._tools, dict):
                return tm._tools
        return None

    def _unregister_device_dynamic_tools(self, device_id: str):
        old_names = self._dynamic_tool_names_by_device.get(device_id, set())
        if not old_names:
            return

        tools_dict = self._get_mcp_tools_dict()
        if tools_dict is None:
            return

        removed = 0
        for name in old_names:
            if name in tools_dict:
                tools_dict.pop(name, None)
                removed += 1
        if removed:
            log(f"[MCP] Unregistered {removed} old dynamic tools for {device_id}")

    def _resolve_registered_name(self, desired_name: str, device_id: str, fallback_key: str) -> str:
        tools_dict = self._get_mcp_tools_dict() or {}
        if desired_name not in tools_dict:
            return desired_name

        candidate = f"{desired_name}__{device_id}"
        if candidate not in tools_dict:
            log(f"[MCP] Tool name collision for '{desired_name}', using '{candidate}'")
            return candidate

        candidate = f"{desired_name}__{device_id}_{fallback_key}"
        i = 2
        while candidate in tools_dict:
            candidate = f"{desired_name}__{device_id}_{fallback_key}_{i}"
            i += 1
        log(f"[MCP] Tool name collision for '{desired_name}', using '{candidate}'")
        return candidate

    def _unregister_tool_name(self, tool_name: str):
        tools_dict = self._get_mcp_tools_dict()
        if tools_dict is None:
            return
        if tool_name in tools_dict:
            tools_dict.pop(tool_name, None)

    def _execute_device_tool_raw(self, device_id: str, tool: str, args: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
        if self.command_service:
            return self.command_service.execute(device_id, tool, args)
        return publish_cmd(
            self.device_store,
            self.cmd_waiter,
            get_mqtt_pub_client(),
            device_id,
            tool,
            args,
            ipc_agent=self.ipc_agent,
        )

    def reload_all_tools(self):
        self.reset_tools()
        self.register_all_announced_devices()
        self.register_virtual_tools()

    def on_device_announced(self, device_id: str):
        self.register_dynamic_tools_for_device(device_id)

    def on_device_status_updated(self, device_id: str, prev_online: Any, new_online: bool, msg: dict):
        if new_online and not prev_online:
            self.register_dynamic_tools_for_device(device_id)
        if (prev_online is True) and (not new_online):
            with self._sync_lock:
                self._unregister_device_dynamic_tools(device_id)
                self._dynamic_tool_names_by_device.pop(device_id, None)

    def setup_resources(self):
        @self.mcp.resource("bridge://devices")
        def res_devices() -> Resource:
            # Filter offline devices by default for cleaner view
            all_devices = self.device_store.list()
            online_devices = [d for d in all_devices if d.get("online", False)]
            
            return Resource(
                uri="bridge://devices",
                name="devices",
                description="Known online devices (all: see bridge://devices/all)",
                mimeType="application/json",
                text=json.dumps(online_devices, indent=2)
            )

        @self.mcp.resource("bridge://devices/all")
        def res_devices_all() -> Resource:
            return Resource(
                uri="bridge://devices/all",
                name="devices-all",
                description="All known devices (including offline)",
                mimeType="application/json",
                text=json.dumps(self.device_store.list(), indent=2)
            )

        @self.mcp.resource("bridge://device/{device_id}")
        def res_device(device_id: str) -> Resource:
            d = self.device_store.get(device_id)
            if not d:
                return Resource(
                    uri=f"bridge://device/{device_id}",
                    name="device",
                    description="Device not found",
                    mimeType="application/json",
                    text=json.dumps({"error":"not found"})
                )
            return Resource(
                uri=f"bridge://device/{device_id}",
                name="device",
                description=f"Device {device_id} details (raw data)",
                mimeType="application/json",
                text=json.dumps(d, indent=2)
            )

        @self.mcp.resource("bridge://projections")
        def res_projections() -> Resource:
            projected_tools = self.tool_registry.list_all_tools()
            projection_summary = {
                "config": self.projection_store.config,
                "projected_tools": projected_tools,
                "stats": {
                    "total_projected_tools": len(projected_tools),
                    "devices_in_config": len(self.projection_store.config.get("devices", {}))
                }
            }
            return Resource(
                uri="bridge://projections",
                name="projections",
                description="Current projection configuration and projected tools",
                mimeType="application/json",
                text=json.dumps(projection_summary, indent=2)
            )

        @self.mcp.resource("bridge://ports")
        def res_ports() -> Resource:
            """모든 디바이스의 포트 정보"""
            return Resource(
                uri="bridge://ports",
                name="ports",
                description="All device ports (outports and inports)",
                mimeType="application/json",
                text=json.dumps({
                    "devices": self.port_store.list_devices(),
                    "outports": self.port_store.get_all_outports(),
                    "inports": self.port_store.get_all_inports()
                }, indent=2)
            )

        @self.mcp.resource("bridge://routing-matrix")
        def res_routing_matrix() -> Resource:
            """라우팅 매트릭스 뷰"""
            matrix_view = self.routing_matrix.get_matrix_view(self.port_store)
            return Resource(
                uri="bridge://routing-matrix",
                name="routing-matrix",
                description="OutPort to InPort routing matrix",
                mimeType="application/json",
                text=json.dumps(matrix_view, indent=2)
            )

    def setup_tools(self):
        @self.mcp.tool()
        def invoke(device_id: str, tool: str, args: dict | None = None) -> List[Union[ImageContent, TextContent]]:
            """Generic tool invoker (fallback for any device tool) - uses original tool names"""
            args = args or {}
            
            d = self.device_store.get(device_id)
            if d and not d.get("online", False):
                return [TextContent(type="text", text=f"Error: Device {device_id} is offline")]

            ok, resp = self._execute_device_tool_raw(device_id, tool, args)
            if not ok:
                error_msg = resp.get("error", {}).get("message", "Unknown error")
                return [TextContent(type="text", text=f"Error: {error_msg}")]
            
            return convert_response_to_content_list(resp)

        @self.mcp.tool()
        def hampter_ops(
            flow: str,
            payload: dict | None = None
        ) -> List[TextContent]:
            """
            Step1: flow only -> template. Step2: flow+payload -> execute.
            flows: run_device_tool, run_tool_batch, add_port_route, remove_port_route, edit_port_route, save_tool_batch, delete_tool_batch, set_device_projection, set_tool_projection, refresh_runtime
            """
            if not payload:
                result = self.ops_hub.get_flow_guide(flow)
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            result = self.ops_hub.execute_flow(flow, payload)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        @self.mcp.tool()
        def hampter_debug(section: str = "summary", include_details: bool = False) -> List[TextContent]:
            """
            Runtime diagnostics. section: summary|validate|state
            """
            payload = self.ops_hub.debug(section=section, include_details=include_details)
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]

        @self.mcp.tool()
        def list_devices(show_offline: bool = False) -> List[TextContent]:
            """List devices. By default, only online devices are shown. Set show_offline=True to see all."""
            devices = self.device_store.list()
            device_summary = []
            visible_count = 0
            
            for device in devices:
                device_id = device['device_id']
                is_online = device.get("online", False)
                status = "online" if is_online else "offline"
                
                if not show_offline and not is_online:
                    continue
                
                visible_count += 1
                tools_count = len(device.get("tools", []))
                
                device_alias = self.projection_store.get_device_alias(device_id, device.get('name'))
                is_enabled = self.projection_store.is_device_enabled(device_id)
                
                projected_tools = [t for t in self.tool_registry.list_all_tools() if t['device_id'] == device_id]
                projected_count = len(projected_tools)
                
                device_summary.append(
                    f"• {device_id} → '{device_alias}' ({status}, {projected_count}/{tools_count} tools projected, {'enabled' if is_enabled else 'disabled'})"
                )
            
            summary_text = f"Found {visible_count} devices (total known: {len(devices)}):\n" + "\n".join(device_summary)
            return [TextContent(type="text", text=summary_text)]

        @self.mcp.tool()
        def get_tools(device_id: str) -> List[TextContent]:
            """List a device's announced tools with projection status."""
            d = self.device_store.get(device_id)
            if not d:
                return [TextContent(type="text", text=f"Error: device_id '{device_id}' not found")]
            
            tools = d.get("tools", [])
            if not tools:
                return [TextContent(type="text", text=f"Device {device_id} has no announced tools")]
            
            tool_summary = []
            for tool in tools:
                name = tool.get("name", "unknown")
                desc = tool.get("description", "")
                
                is_enabled = self.projection_store.is_tool_enabled(device_id, name)
                if is_enabled:
                    projected_tool = self.projection_store.get_tool_projection(device_id, name, tool)
                    projected_name = projected_tool["name"]
                    projected_desc = projected_tool["description"]
                    tool_summary.append(f"• {name} → '{projected_name}' (enabled): {projected_desc}")
                else:
                    tool_summary.append(f"• {name} (disabled): {desc}")
            
            device_alias = self.projection_store.get_device_alias(device_id, d.get('name'))
            summary_text = f"Device {device_id} → '{device_alias}' tools ({len(tools)} total):\n" + "\n".join(tool_summary)
            return [TextContent(type="text", text=summary_text)]

        @self.mcp.tool()
        def list_ports() -> List[TextContent]:
            """List all device ports (outports and inports) with routing info."""
            outports = self.port_store.get_all_outports()
            inports = self.port_store.get_all_inports()
            connections = self.routing_matrix.get_all_connections()
            
            lines = [f"=== Ports Overview ==="]
            lines.append(f"OutPorts: {len(outports)}, InPorts: {len(inports)}, Connections: {len(connections)}")
            lines.append("")
            
            lines.append("--- OutPorts (Sources) ---")
            for p in outports:
                port_id = p['port_id']
                targets = self.routing_matrix.get_targets_for_source(port_id)
                target_count = len(targets)
                lines.append(f"• {port_id} ({p.get('data_type', '?')}) → {target_count} connections")
            
            lines.append("")
            lines.append("--- InPorts (Sinks) ---")
            for p in inports:
                lines.append(f"• {p['port_id']} ({p.get('data_type', '?')})")
            
            return [TextContent(type="text", text="\n".join(lines))]

        @self.mcp.tool()
        def connect_ports(
            source: str, 
            target: str, 
            scale: float | None = None,
            offset: float | None = None,
            threshold: float | None = None,
            description: str = ""
        ) -> List[TextContent]:
            """
            Connect an OutPort to an InPort with optional transform.
            
            Args:
                source: Source OutPort ID (format: "device_id/port_name")
                target: Target InPort ID (format: "device_id/port_name")
                scale: Multiply value by this factor
                offset: Add this value after scaling
                threshold: Convert to 0/1 based on threshold (1 if value > threshold)
                description: Human-readable description of this connection
            
            Example:
                connect_ports("dev-A/impact", "dev-B/motor", scale=2.0, threshold=10.0)
            """
            # Transform 설정 생성
            transform = {}
            if scale is not None:
                transform["scale"] = scale
            if offset is not None:
                transform["offset"] = offset
            if threshold is not None:
                transform["threshold"] = threshold
                transform["threshold_mode"] = "above"
            
            # 포트 존재 확인
            outports = self.port_store.get_all_outports()
            inports = self.port_store.get_all_inports()
            
            source_exists = any(p['port_id'] == source for p in outports)
            target_exists = any(p['port_id'] == target for p in inports)
            
            warnings = []
            if not source_exists:
                warnings.append(f"Warning: Source '{source}' not found in announced outports")
            if not target_exists:
                warnings.append(f"Warning: Target '{target}' not found in announced inports")
            
            # 연결 생성
            try:
                conn = self.routing_matrix.connect(source, target, transform, enabled=True, description=description)
            except ValueError as e:
                return [TextContent(type="text", text=f"✗ Invalid connection: {e}")]
            
            result_lines = [f"✓ Connected: {source} → {target}"]
            if transform:
                result_lines.append(f"  Transform: {json.dumps(transform)}")
            if description:
                result_lines.append(f"  Description: {description}")
            result_lines.extend(warnings)
            
            return [TextContent(type="text", text="\n".join(result_lines))]

        @self.mcp.tool()
        def disconnect_ports(source: str, target: str) -> List[TextContent]:
            """
            Disconnect an OutPort from an InPort.
            
            Args:
                source: Source OutPort ID (format: "device_id/port_name")
                target: Target InPort ID (format: "device_id/port_name")
            """
            success = self.routing_matrix.disconnect(source, target)
            if success:
                return [TextContent(type="text", text=f"✓ Disconnected: {source} → {target}")]
            else:
                return [TextContent(type="text", text=f"✗ Connection not found: {source} → {target}")]

        @self.mcp.tool()
        def get_routing_matrix() -> List[TextContent]:
            """Get the full routing matrix showing all OutPort to InPort connections."""
            matrix_view = self.routing_matrix.get_matrix_view(self.port_store)
            
            lines = ["=== Routing Matrix ==="]
            lines.append(f"Total connections: {matrix_view['connection_count']}")
            lines.append("")
            
            connections = self.routing_matrix.get_all_connections()
            if not connections:
                lines.append("No connections configured.")
            else:
                for conn in connections:
                    status = "✓" if conn.get("enabled", True) else "✗"
                    transform_str = json.dumps(conn.get("transform", {})) if conn.get("transform") else "none"
                    lines.append(f"{status} {conn['source']} → {conn['target']}")
                    lines.append(f"    Transform: {transform_str}")
                    if conn.get("description"):
                        lines.append(f"    Description: {conn['description']}")
            
            return [TextContent(type="text", text="\n".join(lines))]

        @self.mcp.tool()
        def set_inport_value(device_id: str, port_name: str, value: float) -> List[TextContent]:
            """
            Directly set an InPort value on a device.
            
            Args:
                device_id: Target device ID
                port_name: InPort name
                value: Value to set
            """
            success = publish_to_inport(device_id, port_name, value)
            
            if success:
                return [TextContent(type="text", text=f"✓ Set {device_id}/{port_name} = {value}")]
            else:
                return [TextContent(type="text", text=f"✗ Failed to set {device_id}/{port_name}")]

        @self.mcp.tool()
        def get_routing_stats() -> List[TextContent]:
            """Get routing statistics."""
            stats = self.port_router.get_stats()
            
            lines = ["=== Routing Statistics ==="]
            lines.append(f"Total routed: {stats.get('total_routed', 0)}")
            lines.append(f"Total dropped: {stats.get('total_dropped', 0)}")
            lines.append(f"Last routed at: {stats.get('last_routed_at', 'never')}")
            
            return [TextContent(type="text", text="\n".join(lines))]

    def register_dynamic_tools_for_device(self, device_id: str):
        """Register dynamic projected tools for a specific device with FastMCP using proper schemas"""
        device = self.device_store.get(device_id)
        with self._sync_lock:
            self._unregister_device_dynamic_tools(device_id)
            self._dynamic_tool_names_by_device[device_id] = set()

            if not device or not device.get("tools"):
                return
            
            # Skip offline devices - their tools should not be registered
            if not device.get("online", False):
                log(f"[MCP] Skipping tool registration for offline device: {device_id}")
                return

            self.tool_registry.register_device_tools(device_id, device["tools"], device.get("name"))
            
            # log(f"[MCP] Registering dynamic projected tools for device {device_id}")
            for tool_info in device["tools"]:
                tool_name = tool_info.get("name", "")
                if not tool_name:
                    continue
                
                if not self.projection_store.is_tool_enabled(device_id, tool_name):
                    log(f"[MCP] Skipping disabled tool: {tool_name} for device {device_id}")
                    continue
                
                projected_tool = self.projection_store.get_tool_projection(device_id, tool_name, tool_info)
                projected_name = projected_tool["name"]
                registered_name = self._resolve_registered_name(projected_name, device_id, tool_name)
                
                tool_key = f"{registered_name}_{device_id}"
                
                try:
                    schema = tool_info.get("parameters", {})
                    if not schema or schema.get("type") != "object":
                        log(f"[MCP] Skipping tool {tool_key}: invalid or missing schema")
                        continue
                    
                    ParamModel = json_schema_to_pydantic_model(f"{tool_key}_params", schema)
                    
                    # Capture variables in closure
                    def create_tool_func(device_id_copy, original_tool_name_copy, projected_tool_copy, param_model, registered_name_copy):
                        def tool_func(params: param_model) -> List[Union[ImageContent, TextContent]]:
                            """Dynamically generated projected device tool function with proper schema"""
                            args = params.dict()
                            
                            # Check online status before invoking
                            d = self.device_store.get(device_id_copy)
                            if d and not d.get("online", False):
                                 return [TextContent(type="text", text=f"Error: Device {device_id_copy} is offline")]

                            # Sanitize args
                            for k, v in args.items():
                                if isinstance(v, str) and v.strip().startswith('{'):
                                    try:
                                        loaded = json.loads(v)
                                        if isinstance(loaded, dict) and k in loaded:
                                            args[k] = loaded[k]
                                            log(f"[MCP] Auto-unwrapped nested JSON for arg '{k}'")
                                    except:
                                        pass

                            log(f"[PROJECTED_TOOL] {projected_tool_copy['name']} ({original_tool_name_copy}) called with args: {json.dumps(args, indent=2)}")
                            
                            ok, resp = self._execute_device_tool_raw(device_id_copy, original_tool_name_copy, args)
                            
                            if not ok:
                                error_msg = resp.get("error", {}).get("message", "Unknown error")
                                return [TextContent(type="text", text=f"Error: {error_msg}")]
                            
                            return convert_response_to_content_list(resp)
                        
                        tool_func.__name__ = registered_name_copy
                        tool_func.__doc__ = projected_tool_copy["description"]
                        
                        return tool_func
                    
                    dynamic_func = create_tool_func(device_id, tool_name, projected_tool, ParamModel, registered_name)
                    decorated_func = self.mcp.tool()(dynamic_func)
                    self.tool_registry.set_registered_function(tool_key, decorated_func)
                    self._dynamic_tool_names_by_device.setdefault(device_id, set()).add(registered_name)
                    
                    # log(f"[MCP] Successfully registered projected tool: {tool_key}")
                except Exception as e:
                    log(f"[MCP] Failed to register projected tool {tool_key}: {e}")

    def reset_tools(self):
        """Clear all registered tools from both internal registry and FastMCP"""
        # 1. Clear our internal registries
        self.tool_registry.clear_tools()
        self._registered_virtual_tools.clear()  # Clear virtual tools tracking
        self._dynamic_tool_names_by_device.clear()
        
        # 2. Clear FastMCP internal registry
        # FastMCP implementation details: it likely stores tools in _tool_manager or has a list.
        # We will try a few common patterns since we can't inspect the library easily.
        try:
            # Check for _tools dict
            if hasattr(self.mcp, "_tools") and isinstance(self.mcp._tools, dict):
                 self.mcp._tools.clear()
                 log("[MCP] Cleared self.mcp._tools")
            
            # Check if it has a tool manager
            elif hasattr(self.mcp, "_tool_manager"):
                tm = self.mcp._tool_manager
                if hasattr(tm, "_tools") and isinstance(tm._tools, dict):
                    tm._tools.clear()
                    log("[MCP] Cleared self.mcp._tool_manager._tools")
            
            # Re-register static tools (list_devices, etc.) because we just wiped them!
            self.setup_tools()
            # log("[MCP] Re-registered static tools")
            
        except Exception as e:
            log(f"[MCP] Warning: Failed to clear FastMCP tools: {e}")

    def register_all_announced_devices(self):
        """Register tools for all devices that were announced before FastMCP initialization"""
        devices = self.device_store.list()
        # log(f"[MCP] Registering tools for {len(devices)} announced devices")
        for device in devices:
            device_id = device.get("device_id")
            if device_id:
                self.register_dynamic_tools_for_device(device_id)

    def register_virtual_tools(self):
        """Register all virtual tools from the virtual tool store"""
        if not self.virtual_tool_store or not self.virtual_tool_executor:
            log("[MCP] Virtual tool store/executor not configured, skipping virtual tool registration")
            return
        
        virtual_tools = self.virtual_tool_store.get_all_virtual_tools()
        log(f"[MCP] Registering {len(virtual_tools)} virtual tools")

        current_names = set(virtual_tools.keys())
        stale_names = set(self._registered_virtual_tools) - current_names
        for stale_name in stale_names:
            self._unregister_tool_name(stale_name)
            self._registered_virtual_tools.discard(stale_name)
        
        for vt_name, vt_def in virtual_tools.items():
            # Always refresh to ensure updates are reflected immediately.
            if vt_name in self._registered_virtual_tools:
                self._unregister_tool_name(vt_name)
                self._registered_virtual_tools.discard(vt_name)
            
            try:
                self._register_single_virtual_tool(vt_name, vt_def)
                self._registered_virtual_tools.add(vt_name)
                # log(f"[MCP] Registered virtual tool: {vt_name}")
            except Exception as e:
                log(f"[MCP] Failed to register virtual tool {vt_name}: {e}")
    
    def _register_single_virtual_tool(self, name: str, vt_def: dict):
        """Register a single virtual tool with FastMCP"""
        # log(f"[MCP] _register_single_virtual_tool called for: {name}")
        description = vt_def.get("description", f"Virtual tool: {name}")
        bindings = vt_def.get("bindings", [])
        # log(f"[MCP] Virtual tool {name} has {len(bindings)} bindings")
        
        # Build parameter schema from bindings
        schema = self.virtual_tool_store.build_virtual_tool_schema(name, self.device_store)
        # log(f"[MCP] Built schema for {name}: {schema}")
        if not schema:
            schema = {"type": "object", "properties": {}, "required": []}
        
        # If schema has no properties, create a simple version
        if not schema.get("properties"):
            log(f"[MCP] Schema has no properties, using kwargs fallback for {name}")
            # Fallback: create kwargs parameter
            schema = {
                "type": "object",
                "properties": {
                    "kwargs": {
                        "type": "object",
                        "description": "Arguments to pass to all bound tools",
                        "additionalProperties": True
                    }
                },
                "required": []
            }
        
        try:
            ParamModel = json_schema_to_pydantic_model(f"vt_{name}_params", schema)
            # log(f"[MCP] Created ParamModel for {name}: {ParamModel}")
        except Exception as e:
            log(f"[MCP] Could not create param model for {name}: {e}, using simple dict")
            # Fallback to simple kwargs
            from pydantic import BaseModel
            class SimpleParams(BaseModel):
                kwargs: dict = {}
            ParamModel = SimpleParams
        
        # Create the tool function
        def create_vt_func(vt_name_copy, vt_desc_copy, executor_ref, param_model):
            def virtual_tool_func(params: param_model) -> List[Union[ImageContent, TextContent]]:
                """Virtual tool that executes multiple tools in parallel"""
                args = params.dict() if hasattr(params, 'dict') else {}
                
                # If using kwargs wrapper, unwrap it
                if 'kwargs' in args and len(args) == 1:
                    args = args.get('kwargs', {})
                
                log(f"[VIRTUAL_TOOL] Executing {vt_name_copy} with args: {json.dumps(args, indent=2)}")
                
                # Execute synchronously (wrapper handles async internally)
                result = executor_ref.execute_sync(vt_name_copy, args)
                
                # Format result
                if result.get("ok"):
                    summary = f"✓ Virtual tool '{vt_name_copy}' completed: {result['success']}/{result['total']} succeeded"
                else:
                    summary = f"✗ Virtual tool '{vt_name_copy}' had failures: {result['success']}/{result['total']} succeeded"
                
                detail_lines = [summary, ""]
                for r in result.get("results", []):
                    status = "✓" if r.get("ok") else "✗"
                    detail_lines.append(f"  {status} {r['device_id']}/{r['tool']}")
                    if not r.get("ok") and r.get("error"):
                        detail_lines.append(f"      Error: {r['error']}")
                
                return [TextContent(type="text", text="\n".join(detail_lines))]
            
            virtual_tool_func.__name__ = vt_name_copy
            virtual_tool_func.__doc__ = vt_desc_copy
            return virtual_tool_func
        
        dynamic_func = create_vt_func(name, description, self.virtual_tool_executor, ParamModel)
        # log(f"[MCP] Created dynamic function for {name}: {dynamic_func.__name__}")
        
        # Register with FastMCP
        try:
            self.mcp.tool()(dynamic_func)
            # log(f"[MCP] Successfully registered virtual tool with FastMCP: {name}")
        except Exception as e:
            log(f"[MCP] FAILED to register virtual tool {name} with FastMCP: {e}")
            raise
