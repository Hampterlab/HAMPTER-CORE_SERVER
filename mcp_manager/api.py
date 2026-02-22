from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from http import HTTPStatus
import os

from .config import projection_config, routing_config, log, now_iso
from .bridge_client import BridgeAPIClient
from .docker_client import DockerManager
from .config import BRIDGE_API_URL

app = FastAPI(title="Project Saba MCP Manager")
bridge_api = BridgeAPIClient(BRIDGE_API_URL)
docker_manager = DockerManager()

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/healthz")
def healthz():
    from .config import API_PORT
    return {"ok": True, "ts": now_iso(), "service": "mcp-manager", "port": API_PORT}

# ========= API Endpoints =========

# ---- Bridge Proxy & Health ----
@app.get("/api/bridge/health")
def get_bridge_health():
    healthy = bridge_api.health_check()
    return {"healthy": healthy}

@app.get("/api/docker/status")
def get_docker_status():
    return docker_manager.get_bridge_status()

@app.post("/api/docker/restart")
def restart_bridge():
    success = docker_manager.restart_bridge_container()
    if success:
        return {"ok": True}
    return {"ok": False, "error": "Failed to restart container"}

@app.post("/api/bridge/reload")
def reload_bridge_config():
    """Proxy to reload bridge config"""
    try:
        # Utilize BridgeAPIClient or requests. 
        # Since BridgeAPIClient doesn't have it yet, let's use requests directly for simplicity
        # or extend BridgeAPIClient. Let's use requests for now as it's cleaner than editing two files for a simple pass-through.
        import requests
        resp = requests.post(f"{BRIDGE_API_URL}/management/reload", timeout=5)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"ok": False, "error": f"Bridge returned {resp.status_code}"}
    except Exception as e:
        log(f"[API] Error reloading bridge: {e}")
        return {"ok": False, "error": str(e)}

# ---- Projection Config ----
@app.get("/api/projection/config")
def get_projection_config():
    return projection_config.load_config()

@app.post("/api/projection/config")
def save_projection_config(config: dict):
    success = projection_config.save_config(config)
    if success:
        return {"ok": True}
    raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to save config")

# ---- Devices (Proxy to Bridge) ----
@app.get("/api/devices")
def get_devices():
    return bridge_api.get_devices()

# ---- Ports & Routing (Proxy to Bridge) ----
@app.get("/api/ports")
def get_ports():
    return bridge_api.get_ports()

@app.get("/api/routing")
def get_routing():
    return bridge_api.get_routing()

@app.get("/api/routing/connections")
def get_connections():
    routing = bridge_api.get_routing()
    # Bridge API doesn't have a direct "get connections list" endpoint that matches our UI needs exactly if we want IDs,
    # but bridge_mcp.py has /routing/connections. Let's use that if available, or parse matrix.
    # Actually bridge_mcp.py has /routing/connections.
    try:
        # We need to add this method to BridgeAPIClient if not exists, but let's use direct request here or add to client.
        # Client has get_routing which returns matrix view.
        # Let's assume we can fetch connections from bridge.
        # Wait, bridge_mcp.py has @app.get("/routing/connections").
        # Let's add a method to BridgeAPIClient or use raw request.
        # I added get_routing() to client, but not get_connections().
        # Let's use requests directly or update client.
        # For now, let's just use requests here since I can't easily update client file in this step without another tool call.
        # Actually I can just add it to the client file in previous step or just do it here.
        # Let's do it cleanly: I'll use requests here.
        import requests
        resp = requests.get(f"{BRIDGE_API_URL}/routing/connections", timeout=5)
        return resp.json()
    except Exception as e:
        log(f"[API] Error getting connections: {e}")
        return []

@app.post("/api/routing/connect")
def connect_ports(data: dict):
    return bridge_api.connect_ports(
        data.get("source"), 
        data.get("target"), 
        data.get("transform"), 
        data.get("description"),
        data.get("enabled", True)
    )

@app.post("/api/routing/disconnect")
def disconnect_ports(data: dict):
    return bridge_api.disconnect_ports(
        data.get("source"), 
        data.get("target"), 
        data.get("connection_id")
    )

@app.put("/api/routing/connection/{connection_id}")
def update_connection(connection_id: str, data: dict):
    return bridge_api.update_connection(connection_id, data)

# ---- Virtual Tools (Proxy to Bridge) ----
@app.get("/api/virtual-tools")
def get_virtual_tools():
    try:
        import requests
        resp = requests.get(f"{BRIDGE_API_URL}/virtual-tools", timeout=5)
        return resp.json()
    except Exception as e:
        log(f"[API] Error getting virtual tools: {e}")
        return {}

@app.get("/api/virtual-tools/{name}")
def get_virtual_tool(name: str):
    try:
        import requests
        resp = requests.get(f"{BRIDGE_API_URL}/virtual-tools/{name}", timeout=5)
        if resp.status_code == 404:
            raise HTTPException(HTTPStatus.NOT_FOUND, "Virtual tool not found")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        log(f"[API] Error getting virtual tool: {e}")
        raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

@app.post("/api/virtual-tools")
def create_virtual_tool(data: dict):
    try:
        import requests
        resp = requests.post(f"{BRIDGE_API_URL}/virtual-tools", json=data, timeout=10)
        return resp.json()
    except Exception as e:
        log(f"[API] Error creating virtual tool: {e}")
        raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

@app.put("/api/virtual-tools/{name}")
def update_virtual_tool(name: str, data: dict):
    try:
        import requests
        resp = requests.put(f"{BRIDGE_API_URL}/virtual-tools/{name}", json=data, timeout=10)
        if resp.status_code == 404:
            raise HTTPException(HTTPStatus.NOT_FOUND, "Virtual tool not found")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        log(f"[API] Error updating virtual tool: {e}")
        raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

@app.delete("/api/virtual-tools/{name}")
def delete_virtual_tool(name: str):
    try:
        import requests
        resp = requests.delete(f"{BRIDGE_API_URL}/virtual-tools/{name}", timeout=10)
        if resp.status_code == 404:
            raise HTTPException(HTTPStatus.NOT_FOUND, "Virtual tool not found")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        log(f"[API] Error deleting virtual tool: {e}")
        raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
