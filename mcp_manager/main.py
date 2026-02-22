import uvicorn
import sys
import os
import socket
from .config import API_PORT, PROJECTION_CONFIG_PATH, ROUTING_CONFIG_PATH, BRIDGE_API_URL, log
from .api import app

def pick_free_port(base: int, tries: int) -> int | None:
    for p in range(base, base + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", p))
            except OSError:
                continue
            return p
    return None

def main():
    active_port = API_PORT
    if os.getenv("AUTO_PORT_FALLBACK", "1") == "1":
        pf = pick_free_port(API_PORT, 10)
        if pf:
            active_port = pf
            
    log(f"[boot] python={sys.version}")
    log(f"[boot] HAMPTER Manager API_PORT={active_port}")
    log(f"[boot] PROJECTION_CONFIG_PATH={PROJECTION_CONFIG_PATH}")
    log(f"[boot] ROUTING_CONFIG_PATH={ROUTING_CONFIG_PATH}")
    log(f"[boot] BRIDGE_API_URL={BRIDGE_API_URL}")
    log(f"[boot] Web Interface: http://0.0.0.0:{active_port}")
    
    uvicorn.run(app, host="0.0.0.0", port=active_port, log_level="warning", access_log=False)

if __name__ == "__main__":
    main()
