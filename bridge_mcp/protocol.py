from typing import Dict, Any, Optional
import json
from .utils import log
from .device_store import DeviceStore
from .command import CommandWaiter

class ProtocolHandler:
    """
    Unified Message Processor for SABA.
    Handles business logic regardless of transport (IPC vs MQTT).
    """
    def __init__(self, device_store: DeviceStore, cmd_waiter: CommandWaiter, port_store, port_router):
        self.device_store = device_store
        self.cmd_waiter = cmd_waiter
        self.port_store = port_store
        self.port_router = port_router

    def parse_topic(self, topic: str):
        """
        Standard Topic Format: mcp/dev/{device_id}/{leaf...}
        """
        parts = topic.split("/")
        if len(parts) >= 4 and parts[0] == "mcp" and parts[1] == "dev":
            device_id = parts[2]
            leaf = "/".join(parts[3:])
            return device_id, leaf
        return None, None

    def handle_message(self, topic: str, payload: Dict[str, Any], protocol: str, device_id_hint: str = None):
        """
        Main entry point for all incoming messages.
        
        Args:
            topic: The topic string (e.g., mcp/dev/xyz/announce)
            payload: The parsed JSON payload
            protocol: "ipc" or "mqtt"
            device_id_hint: Optional override for deviceID (e.g. from socket connection)
        """
        # 1. Parse Topic
        dev_id, leaf = self.parse_topic(topic)
        if not dev_id:
            # If topic doesn't have ID but we have a hint (unlikely in this topic schema but possible)
            if device_id_hint:
                dev_id = device_id_hint
                # Try to guess leaf? Unsafe.
                # In current SABA, topic always contains ID.
            else:
                return ("invalid_topic", None)

        # 2. Add Protocol Metadata
        # We might want to track which protocol a device is using
        
        # 3. Route by Leaf (Action)
        
        if leaf == "announce":
            self.device_store.upsert_announce(dev_id, payload, protocol=protocol)
            # Note: MQTT might do "claiming" here. 
            # Ideally ProtocolHandler returns "actions" for the transport to take? 
            # Or we inject a 'ClaimService'. For now, we'll keep claim logic in MQTT transport or move it here later.
            return ("announce", dev_id)

        elif leaf == "status":
            self.device_store.update_status(dev_id, payload)
            return ("status", dev_id)

        elif leaf == "events":
            rid = payload.get("request_id")
            if rid:
                # Bind response resolution to announcing device to reduce cross-device mismatch.
                self.cmd_waiter.resolve(rid, payload, device_id=dev_id)
            return ("events", rid)

        elif leaf == "ports/announce":
            self.port_store.upsert_ports_announce(dev_id, payload)
            return ("ports_announce", dev_id)

        elif leaf == "ports/data":
            port_name = payload.get("port")
            value = payload.get("value")
            if port_name is not None:
                try:
                    val = float(value)
                    self.port_store.record_outport_value(
                        dev_id,
                        str(port_name),
                        val,
                        protocol=protocol,
                        timestamp=payload.get("timestamp"),
                    )
                    if not self.port_router:
                        return ("ports_data", None)
                    routed = self.port_router.route(dev_id, port_name, val)
                    # log(f"[PROTOCOL] Routed {dev_id}/{port_name} ({val}) -> {routed} targets")
                    return ("routed", routed)
                except:
                    pass
            return ("ports_data", None)

        elif leaf == "ports/state":
            port_name = payload.get("port")
            value = payload.get("value")
            if port_name is not None and value is not None:
                try:
                    self.port_store.record_inport_ack(
                        dev_id,
                        str(port_name),
                        float(value),
                        accepted=bool(payload.get("accepted", True)),
                        protocol=protocol,
                        source=payload.get("source"),
                        timestamp=payload.get("timestamp"),
                    )
                    return ("ports_state", dev_id)
                except Exception:
                    pass
            return ("ports_state", None)
            
        return ("unknown", None)
