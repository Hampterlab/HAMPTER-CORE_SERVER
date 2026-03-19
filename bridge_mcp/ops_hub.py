import json
from typing import Any, Callable, Dict, Optional

from .utils import now_iso


class HampterOpsHub:
    """Single-entry operation hub used by hampter_ops/hampter_debug MCP tools."""

    def __init__(
        self,
        device_store,
        projection_store,
        routing_matrix,
        virtual_tool_store,
        virtual_tool_executor,
        port_store,
        port_router,
        execute_device_tool: Callable[[str, str, Dict[str, Any]], tuple[bool, Dict[str, Any]]],
        sync_dynamic_tools: Callable[[], None],
        sync_virtual_tools: Callable[[], None],
        reload_all: Callable[[], None],
    ):
        self.device_store = device_store
        self.projection_store = projection_store
        self.routing_matrix = routing_matrix
        self.virtual_tool_store = virtual_tool_store
        self.virtual_tool_executor = virtual_tool_executor
        self.port_store = port_store
        self.port_router = port_router
        self.execute_device_tool = execute_device_tool
        self.sync_dynamic_tools = sync_dynamic_tools
        self.sync_virtual_tools = sync_virtual_tools
        self.reload_all = reload_all

    def discover(self, intent: str, domain: Optional[str] = None) -> Dict[str, Any]:
        selected = domain or self._infer_domain(intent)
        return {
            "ok": True,
            "mode": "discover",
            "domain": selected,
            "intent": intent,
            "spec_markdown": self._domain_spec(selected),
            "action_template": self._action_template(selected),
            "timestamp": now_iso(),
        }

    def get_flow_guide(self, flow: str) -> Dict[str, Any]:
        canonical_flow = (flow or "").strip()
        spec = self._flow_spec(canonical_flow)
        if not spec:
            return self._err("invalid_flow", f"unsupported flow '{flow}'")
        return {
            "ok": True,
            "flow": canonical_flow,
            "step": "fill_payload",
            "description": spec["description"],
            "required_fields": spec["required_fields"],
            "payload_template": spec["payload_template"],
            "example_payload": spec["example_payload"],
            "timestamp": now_iso(),
        }

    def execute_flow(self, flow: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        canonical_flow = (flow or "").strip()
        spec = self._flow_spec(canonical_flow)
        if not spec:
            return self._err("invalid_flow", f"unsupported flow '{flow}'")

        missing = [k for k in spec["required_fields"] if payload.get(k) in (None, "")]
        if missing:
            return {
                "ok": False,
                "error": {
                    "code": "missing_fields",
                    "message": "payload is missing required fields",
                    "missing_fields": missing,
                },
                "flow": canonical_flow,
                "required_fields": spec["required_fields"],
                "payload_template": spec["payload_template"],
                "example_payload": spec["example_payload"],
                "timestamp": now_iso(),
            }

        action = spec["to_action"](payload)
        result = self.execute(action)
        result["flow"] = canonical_flow
        return result

    def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        action_type = str(action.get("type", "")).strip()
        if not action_type:
            return self._err("invalid_action", "action.type is required")

        try:
            if action_type == "invoke.device":
                device_id = action.get("device_id")
                tool = action.get("tool")
                args = action.get("args") or {}
                if not device_id or not tool:
                    return self._err("invalid_action", "device_id and tool are required")
                ok, resp = self.execute_device_tool(device_id, tool, args)
                return self._ok(action_type, changed=False, result={"invoke_ok": ok, "response": resp})

            if action_type == "invoke.virtual":
                name = action.get("name")
                args = action.get("args") or {}
                if not name:
                    return self._err("invalid_action", "name is required")
                result = self.virtual_tool_executor.execute_sync(name, args)
                return self._ok(action_type, changed=False, result=result)

            if action_type == "routing.connect":
                source = action.get("source")
                target = action.get("target")
                if not source or not target:
                    return self._err("invalid_action", "source and target are required")
                conn = self.routing_matrix.connect(
                    source,
                    target,
                    action.get("transform"),
                    bool(action.get("enabled", True)),
                    action.get("description", ""),
                )
                return self._ok(action_type, changed=True, result={"connection": conn})

            if action_type == "routing.disconnect":
                connection_id = action.get("connection_id")
                if connection_id:
                    ok = self.routing_matrix.disconnect_by_id(connection_id)
                else:
                    source = action.get("source")
                    target = action.get("target")
                    if not source or not target:
                        return self._err("invalid_action", "connection_id or source+target is required")
                    ok = self.routing_matrix.disconnect(source, target)
                return self._ok(action_type, changed=ok, result={"disconnected": ok})

            if action_type == "routing.update":
                connection_id = action.get("connection_id")
                updates = action.get("updates") or {}
                if not connection_id:
                    return self._err("invalid_action", "connection_id is required")
                conn = self.routing_matrix.update_connection(connection_id, updates)
                if not conn:
                    return self._err("not_found", "connection not found")
                return self._ok(action_type, changed=True, result={"connection": conn})

            if action_type == "virtual.upsert":
                name = action.get("name")
                if not name:
                    return self._err("invalid_action", "name is required")
                tool_def = {
                    "description": action.get("description", ""),
                    "bindings": action.get("bindings", []),
                }
                exists = self.virtual_tool_store.get_virtual_tool(name) is not None
                ok = (
                    self.virtual_tool_store.update_virtual_tool(name, tool_def)
                    if exists
                    else self.virtual_tool_store.create_virtual_tool(name, tool_def)
                )
                if not ok:
                    return self._err("persist_failed", "failed to save virtual tool config")
                self.sync_virtual_tools()
                return self._ok(
                    action_type,
                    changed=True,
                    result={"name": name, "operation": "updated" if exists else "created"},
                )

            if action_type == "virtual.delete":
                name = action.get("name")
                if not name:
                    return self._err("invalid_action", "name is required")
                ok = self.virtual_tool_store.delete_virtual_tool(name)
                if not ok:
                    return self._err("not_found", "virtual tool not found")
                self.sync_virtual_tools()
                return self._ok(action_type, changed=True, result={"deleted": name})

            if action_type == "projection.set_device":
                device_id = action.get("device_id")
                if not device_id:
                    return self._err("invalid_action", "device_id is required")
                updated = self.projection_store.update_device_projection(
                    device_id,
                    {
                        "enabled": action.get("enabled"),
                        "device_alias": action.get("device_alias"),
                    },
                )
                self.sync_dynamic_tools()
                return self._ok(action_type, changed=True, result={"device_projection": updated})

            if action_type == "projection.set_tool":
                device_id = action.get("device_id")
                tool_name = action.get("tool_name")
                if not device_id or not tool_name:
                    return self._err("invalid_action", "device_id and tool_name are required")
                updated = self.projection_store.update_tool_projection(
                    device_id,
                    tool_name,
                    {
                        "enabled": action.get("enabled"),
                        "alias": action.get("alias"),
                        "description": action.get("description"),
                    },
                )
                self.sync_dynamic_tools()
                return self._ok(action_type, changed=True, result={"tool_projection": updated})

            if action_type == "system.reload":
                self.reload_all()
                return self._ok(action_type, changed=True, result={"reloaded": True})

            return self._err("unsupported_action", f"unsupported action.type '{action_type}'")
        except ValueError as e:
            return self._err("invalid_action", str(e))
        except Exception as e:
            return self._err("execution_error", str(e))

    def debug(self, section: str = "summary", include_details: bool = False) -> Dict[str, Any]:
        devices = self.device_store.list()
        routing_connections = self.routing_matrix.get_all_connections()
        virtual_tools = self.virtual_tool_store.get_all_virtual_tools()
        projected_tools = self.projection_store.config.get("devices", {})

        if section == "summary":
            return {
                "ok": True,
                "section": section,
                "stats": {
                    "devices_total": len(devices),
                    "devices_online": len([d for d in devices if d.get("online", False)]),
                    "routing_connections": len(routing_connections),
                    "virtual_tools": len(virtual_tools),
                    "projection_devices": len(projected_tools),
                },
                "timestamp": now_iso(),
            }

        if section == "validate":
            warnings = []
            for vt_name, vt_def in virtual_tools.items():
                for binding in vt_def.get("bindings", []):
                    device_id = binding.get("device_id")
                    tool_name = binding.get("tool")
                    dev = self.device_store.get(device_id) if device_id else None
                    if not dev:
                        warnings.append(f"virtual:{vt_name} -> missing device '{device_id}'")
                        continue
                    tools = dev.get("tools", [])
                    if not any(t.get("name") == tool_name for t in tools):
                        warnings.append(f"virtual:{vt_name} -> missing tool '{tool_name}' on '{device_id}'")

            all_outports = {p["port_id"] for p in self.port_store.get_all_outports()}
            all_inports = {p["port_id"] for p in self.port_store.get_all_inports()}
            for conn in routing_connections:
                src = conn.get("source")
                tgt = conn.get("target")
                if src not in all_outports:
                    warnings.append(f"routing:{conn.get('id')} -> missing source '{src}'")
                if tgt not in all_inports:
                    warnings.append(f"routing:{conn.get('id')} -> missing target '{tgt}'")

            return {
                "ok": True,
                "section": section,
                "warnings": warnings,
                "warning_count": len(warnings),
                "timestamp": now_iso(),
            }

        if section == "state":
            payload = {
                "ok": True,
                "section": section,
                "devices": devices,
                "routing": self.routing_matrix.get_matrix_view(self.port_store),
                "virtual_tools": virtual_tools,
                "projection_config": self.projection_store.config,
                "timestamp": now_iso(),
            }
            if not include_details:
                payload["devices"] = [{"device_id": d.get("device_id"), "online": d.get("online", False)} for d in devices]
            return payload

        if section == "ports":
            return {
                "ok": True,
                "section": section,
                "router_stats": self.port_router.get_stats() if self.port_router else {},
                "snapshot": self.port_store.get_debug_snapshot(limit=200 if include_details else 50),
                "timestamp": now_iso(),
            }

        return self._err("invalid_section", f"unsupported debug section '{section}'")

    def _infer_domain(self, intent: str) -> str:
        t = (intent or "").lower()
        if any(k in t for k in ["route", "routing", "port", "inport", "outport"]):
            return "routing"
        if any(k in t for k in ["virtual", "workflow", "binding"]):
            return "virtual"
        if any(k in t for k in ["projection", "alias", "enabled", "tool name"]):
            return "projection"
        if any(k in t for k in ["invoke", "execute", "run"]):
            return "invoke"
        return "general"

    def _domain_spec(self, domain: str) -> str:
        specs = {
            "routing": (
                "# Routing Ops\n"
                "- `routing.connect`: source/target required\n"
                "- `routing.disconnect`: connection_id or source+target\n"
                "- `routing.update`: connection_id + updates\n"
                "Transform keys: scale, offset, threshold, threshold_mode, min, max, invert, map_from, map_to"
            ),
            "virtual": (
                "# Virtual Tool Ops\n"
                "- `virtual.upsert`: name, description, bindings[]\n"
                "- `virtual.delete`: name\n"
                "- `invoke.virtual`: name + args"
            ),
            "projection": (
                "# Projection Ops\n"
                "- `projection.set_device`: device_id, enabled, device_alias\n"
                "- `projection.set_tool`: device_id, tool_name, enabled, alias, description"
            ),
            "invoke": (
                "# Invoke Ops\n"
                "- `invoke.device`: device_id, tool, args\n"
                "- `invoke.virtual`: name, args"
            ),
            "general": (
                "# General Ops\n"
                "- Choose one action type and send JSON to `hampter_ops(mode='execute')`.\n"
                "- Validate current graph via `hampter_debug(section='validate')`."
            ),
        }
        return specs.get(domain, specs["general"])

    def _flow_spec(self, flow: str) -> Optional[Dict[str, Any]]:
        flows: Dict[str, Dict[str, Any]] = {
            "run_device_tool": {
                "description": "Invoke a concrete device tool",
                "required_fields": ["device_id", "tool"],
                "payload_template": {"device_id": "", "tool": "", "args": {}},
                "example_payload": {"device_id": "dev-A", "tool": "ping", "args": {}},
                "to_action": lambda p: {
                    "type": "invoke.device",
                    "device_id": p.get("device_id"),
                    "tool": p.get("tool"),
                    "args": p.get("args", {}),
                },
            },
            "run_tool_batch": {
                "description": "Run a tool batch (multiple bound tools in parallel)",
                "required_fields": ["name"],
                "payload_template": {"name": "", "args": {}},
                "example_payload": {"name": "morning_routine", "args": {}},
                "to_action": lambda p: {
                    "type": "invoke.virtual",
                    "name": p.get("name"),
                    "args": p.get("args", {}),
                },
            },
            "add_port_route": {
                "description": "Create a port routing connection",
                "required_fields": ["source", "target"],
                "payload_template": {
                    "source": "",
                    "target": "",
                    "transform": {},
                    "enabled": True,
                    "description": "",
                },
                "example_payload": {
                    "source": "dev-A/impact",
                    "target": "dev-B/motor",
                    "transform": {"scale": 2.0},
                    "enabled": True,
                    "description": "impact to motor",
                },
                "to_action": lambda p: {
                    "type": "routing.connect",
                    "source": p.get("source"),
                    "target": p.get("target"),
                    "transform": p.get("transform", {}),
                    "enabled": bool(p.get("enabled", True)),
                    "description": p.get("description", ""),
                },
            },
            "remove_port_route": {
                "description": "Disconnect routing by connection_id or source+target",
                "required_fields": [],
                "payload_template": {"connection_id": "", "source": "", "target": ""},
                "example_payload": {"connection_id": "dev-A/out→dev-B/in"},
                "to_action": lambda p: {
                    "type": "routing.disconnect",
                    "connection_id": p.get("connection_id"),
                    "source": p.get("source"),
                    "target": p.get("target"),
                },
            },
            "edit_port_route": {
                "description": "Update routing connection settings",
                "required_fields": ["connection_id"],
                "payload_template": {
                    "connection_id": "",
                    "updates": {"transform": {}, "enabled": True, "description": ""},
                },
                "example_payload": {
                    "connection_id": "dev-A/out→dev-B/in",
                    "updates": {"enabled": False},
                },
                "to_action": lambda p: {
                    "type": "routing.update",
                    "connection_id": p.get("connection_id"),
                    "updates": p.get("updates", {}),
                },
            },
            "save_tool_batch": {
                "description": "Create or update a tool batch",
                "required_fields": ["name"],
                "payload_template": {"name": "", "description": "", "bindings": []},
                "example_payload": {
                    "name": "morning_routine",
                    "description": "Run morning tasks",
                    "bindings": [{"device_id": "dev-light", "tool": "turn_on"}],
                },
                "to_action": lambda p: {
                    "type": "virtual.upsert",
                    "name": p.get("name"),
                    "description": p.get("description", ""),
                    "bindings": p.get("bindings", []),
                },
            },
            "delete_tool_batch": {
                "description": "Delete a tool batch",
                "required_fields": ["name"],
                "payload_template": {"name": ""},
                "example_payload": {"name": "morning_routine"},
                "to_action": lambda p: {
                    "type": "virtual.delete",
                    "name": p.get("name"),
                },
            },
            "set_device_projection": {
                "description": "Update device-level projection",
                "required_fields": ["device_id"],
                "payload_template": {"device_id": "", "enabled": True, "device_alias": ""},
                "example_payload": {"device_id": "dev-A", "enabled": True, "device_alias": "window_motor"},
                "to_action": lambda p: {
                    "type": "projection.set_device",
                    "device_id": p.get("device_id"),
                    "enabled": p.get("enabled"),
                    "device_alias": p.get("device_alias"),
                },
            },
            "set_tool_projection": {
                "description": "Update tool-level projection",
                "required_fields": ["device_id", "tool_name"],
                "payload_template": {
                    "device_id": "",
                    "tool_name": "",
                    "enabled": True,
                    "alias": "",
                    "description": "",
                },
                "example_payload": {
                    "device_id": "dev-A",
                    "tool_name": "rotate_motor",
                    "enabled": True,
                    "alias": "open_window",
                    "description": "Open bedroom window",
                },
                "to_action": lambda p: {
                    "type": "projection.set_tool",
                    "device_id": p.get("device_id"),
                    "tool_name": p.get("tool_name"),
                    "enabled": p.get("enabled"),
                    "alias": p.get("alias"),
                    "description": p.get("description"),
                },
            },
            "refresh_runtime": {
                "description": "Reload projection/virtual config and re-sync tools",
                "required_fields": [],
                "payload_template": {},
                "example_payload": {},
                "to_action": lambda p: {"type": "system.reload"},
            },
        }
        return flows.get(flow)


    def _action_template(self, domain: str) -> Dict[str, Any]:
        templates = {
            "routing": {
                "type": "routing.connect",
                "source": "dev-A/out",
                "target": "dev-B/in",
                "transform": {"scale": 1.0},
                "enabled": True,
                "description": "",
            },
            "virtual": {
                "type": "virtual.upsert",
                "name": "morning_routine",
                "description": "Turn on stuff",
                "bindings": [{"device_id": "dev-light", "tool": "turn_on"}],
            },
            "projection": {
                "type": "projection.set_tool",
                "device_id": "dev-A",
                "tool_name": "rotate_motor",
                "enabled": True,
                "alias": "open_window",
                "description": "Open window",
            },
            "invoke": {"type": "invoke.device", "device_id": "dev-A", "tool": "ping", "args": {}},
            "general": {"type": "system.reload"},
        }
        return templates.get(domain, templates["general"])

    def _ok(self, action_type: str, changed: bool, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ok": True,
            "action": action_type,
            "changed": changed,
            "result": result,
            "timestamp": now_iso(),
        }

    def _err(self, code: str, message: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": {"code": code, "message": message},
            "timestamp": now_iso(),
        }
