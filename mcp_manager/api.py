from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from http import HTTPStatus
import asyncio
import hashlib
import json
import os

from .config import projection_config, routing_config, log, now_iso
from .bridge_client import BridgeAPIClient
from .docker_client import DockerManager
from .config import BRIDGE_API_URL

app = FastAPI(title="HAMPTER Manager")
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
    return {"ok": True, "ts": now_iso(), "service": "hampter-manager", "port": API_PORT}


def _stable_hash(payload: object) -> str:
    dumped = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(dumped.encode("utf-8")).hexdigest()


def _snapshot() -> dict:
    devices = bridge_api.get_devices()
    routing = bridge_api.get_routing()
    connections = bridge_api.get_connections()
    virtual_tools = bridge_api.get_virtual_tools()
    docker_status = docker_manager.get_bridge_status()
    bridge_healthy = bridge_api.health_check()

    online_count = len([d for d in devices if d.get("online")])

    return {
        "ts": now_iso(),
        "bridge_healthy": bridge_healthy,
        "docker_running": docker_status.get("running", False),
        "counts": {
            "devices_total": len(devices),
            "devices_online": online_count,
            "connections": len(connections),
            "virtual_tools": len(virtual_tools.keys()),
            "outports": len(routing.get("outports", [])),
            "inports": len(routing.get("inports", [])),
        },
        "revisions": {
            "devices": _stable_hash(devices),
            "routing": _stable_hash(routing),
            "connections": _stable_hash(connections),
            "virtual_tools": _stable_hash(virtual_tools),
            "status": _stable_hash({"bridge": bridge_healthy, "docker": docker_status}),
        },
    }


@app.get("/api/stream")
async def stream_updates():
    async def event_generator():
        last_rev = None
        while True:
            try:
                snap = await asyncio.to_thread(_snapshot)
                current_rev = _stable_hash(snap["revisions"])
                if current_rev != last_rev:
                    yield "event: snapshot\n"
                    yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                    last_rev = current_rev
                else:
                    # Keep-alive to prevent proxy/browser idle timeout.
                    yield f"event: ping\ndata: {json.dumps({'ts': now_iso()})}\n\n"
            except Exception as e:
                log(f"[STREAM] snapshot error: {e}")
                yield f"event: error\ndata: {json.dumps({'error': str(e), 'ts': now_iso()})}\n\n"

            await asyncio.sleep(1)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


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
        import requests

        resp = requests.post(f"{BRIDGE_API_URL}/management/reload", timeout=5)
        if resp.status_code == 200:
            return resp.json()
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


@app.get("/api/port-debug")
def get_port_debug(limit: int = 50):
    return bridge_api.get_port_debug(limit=limit)


@app.get("/api/routing")
def get_routing():
    return bridge_api.get_routing()


@app.get("/api/routing/connections")
def get_connections():
    return bridge_api.get_connections()


@app.post("/api/routing/connect")
def connect_ports(data: dict):
    return bridge_api.connect_ports(
        data.get("source"),
        data.get("target"),
        data.get("transform"),
        data.get("description"),
        data.get("enabled", True),
    )


@app.post("/api/routing/disconnect")
def disconnect_ports(data: dict):
    return bridge_api.disconnect_ports(
        data.get("source"), data.get("target"), data.get("connection_id")
    )


@app.put("/api/routing/connection/{connection_id}")
def update_connection(connection_id: str, data: dict):
    return bridge_api.update_connection(connection_id, data)


# ---- Virtual Tools (Proxy to Bridge) ----
@app.get("/api/virtual-tools")
def get_virtual_tools():
    return bridge_api.get_virtual_tools()


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
