
import threading
import uuid
import json
import paho.mqtt.client as mqtt
from typing import Optional, Callable
from .config import (
    MQTT_HOST,
    MQTT_PORT,
    KEEPALIVE,
    SUB_ALL,
    TOPIC_ANN,
    TOPIC_STAT,
    TOPIC_EV,
    TOPIC_PORTS_ANN,
    TOPIC_PORTS_DATA,
    TOPIC_PORTS_STATE,
)
from .utils import log
from .device_store import DeviceStore
from .command import CommandWaiter
from .protocol import ProtocolHandler
import secrets
import string

def generate_token(length=32):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

_mqtt_pub_client = None
_mqtt_pub_lock = threading.Lock()

def get_mqtt_pub_client():
    """Reusable MQTT publish client"""
    global _mqtt_pub_client
    
    with _mqtt_pub_lock:
        if _mqtt_pub_client is None or not _mqtt_pub_client.is_connected():
            _mqtt_pub_client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"bridge-pub-{uuid.uuid4().hex[:6]}",
                protocol=mqtt.MQTTv5
            )
            _mqtt_pub_client.connect(MQTT_HOST, MQTT_PORT, keepalive=KEEPALIVE)
            _mqtt_pub_client.loop_start()
        return _mqtt_pub_client

def publish_to_inport(device_id: str, port_name: str, value: float) -> bool:
    """InPort Publish (ports/set)"""
    topic = f"mcp/dev/{device_id}/ports/set"
    payload = {
        "port": port_name,
        "value": value
    }
    
    try:
        client = get_mqtt_pub_client()
        result = client.publish(topic, json.dumps(payload), qos=0, retain=False)
        return result.rc == 0
    except Exception as e:
        return False

def publish_claim_token(device_id: str, token: str) -> bool:
    claim_topic = f"mcp/dev/{device_id}/claim"
    claim_payload = {"token": token}
    try:
        pub = get_mqtt_pub_client()
        pub.publish(claim_topic, json.dumps(claim_payload), qos=1, retain=False)
        return True
    except Exception as e:
        log(f"[CLAIM] Failed to publish claim token for {device_id}: {e}")
        return False

def start_mqtt_listener(device_store: DeviceStore, cmd_waiter: CommandWaiter, port_store, port_router):
    
    # Unified Protocol Handler
    protocol = ProtocolHandler(device_store, cmd_waiter, port_store, port_router)
    
    def mqtt_thread():
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bridge-mcp-{uuid.uuid4().hex[:6]}",
            protocol=mqtt.MQTTv5
        )
        # client.enable_logger()

        def on_connect(c, userdata, flags, reason_code, properties=None):
            log(f"[mqtt] connected rc={reason_code} host={MQTT_HOST}:{MQTT_PORT}")
            if SUB_ALL:
                sub = ("mcp/#", 0)
                c.subscribe(sub)
                log(f"[mqtt] subscribe {sub}")
            else:
                c.subscribe(TOPIC_ANN)
                c.subscribe(TOPIC_STAT)
                c.subscribe(TOPIC_EV)
                c.subscribe(TOPIC_PORTS_ANN)
                c.subscribe(TOPIC_PORTS_DATA)
                c.subscribe(TOPIC_PORTS_STATE)

        def on_message(c, userdata, msg):
            # if "ports/data" not in msg.topic:
            #     # Reduce log noise
            #     if "status" not in msg.topic:
            #         # log(f"[mqtt] RX {msg.topic}")
            #         pass
                    
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except Exception:
                log("[mqtt] JSON parse error from broker")
                return

            try:
                # Delegate to Unified Protocol Handler
                action, dev_id = protocol.handle_message(msg.topic, payload, protocol="mqtt")
                
                # Extended Logic: Claiming (MQTT Specific)
                if action == "announce" and dev_id:
                    # Ensure every announce is paired with a claim token push.
                    # This makes reboot/reset recovery deterministic.
                    token = device_store.get_token(dev_id)
                    if not token:
                        token = generate_token()
                        device_store.set_token(dev_id, token)
                        log(f"[CLAIM] Generated new token for {dev_id}")
                    if publish_claim_token(dev_id, token):
                        log(f"[CLAIM] Sent claim token to {dev_id}")

                # If device reports wrong token, rotate and re-claim immediately.
                if action == "events" and dev_id:
                    err = payload.get("error") or {}
                    code = err.get("code")
                    if code == "wrong_token":
                        new_token = generate_token()
                        device_store.set_token(dev_id, new_token)
                        log(f"[CLAIM] Rotated token due to wrong_token for {dev_id}")
                        publish_claim_token(dev_id, new_token)

            except Exception as e:
                log(f"[mqtt] Error handling message: {e}")

        client.on_connect = on_connect
        client.on_message = on_message

        client.connect(MQTT_HOST, MQTT_PORT, keepalive=KEEPALIVE)
        client.loop_forever(retry_first_connection=True)

    threading.Thread(target=mqtt_thread, daemon=True).start()
