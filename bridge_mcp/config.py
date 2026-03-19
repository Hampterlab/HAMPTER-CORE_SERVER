import os

# ========= Env (Docker defaults) =========
MQTT_HOST = os.getenv("MQTT_HOST", "mcp-broker")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
KEEPALIVE = int(os.getenv("KEEPALIVE", "60"))
API_PORT  = int(os.getenv("API_PORT", "8083"))       # MCP SSE 전용
CMD_TIMEOUT_MS = int(os.getenv("CMD_TIMEOUT_MS", "30000"))
SUB_ALL        = os.getenv("DEBUG_SUB_ALL", "0") == "1"
PROJECTION_CONFIG_PATH = os.getenv("PROJECTION_CONFIG_PATH", "./config/projection_config.json")
ROUTING_CONFIG_PATH = os.getenv("ROUTING_CONFIG_PATH", "./config/routing_config.json")
VIRTUAL_TOOLS_CONFIG_PATH = os.getenv("VIRTUAL_TOOLS_CONFIG_PATH", "./config/virtual_tools.json")

# MQTT Topics
TOPIC_ANN  = "mcp/dev/+/announce"
TOPIC_STAT = "mcp/dev/+/status"
TOPIC_EV   = "mcp/dev/+/events"
TOPIC_PORTS_ANN  = "mcp/dev/+/ports/announce"
TOPIC_PORTS_DATA = "mcp/dev/+/ports/data"
TOPIC_PORTS_STATE = "mcp/dev/+/ports/state"

IPC_PORT = int(os.getenv("IPC_PORT", "8085"))
