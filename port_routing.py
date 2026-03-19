#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Port Routing System - InPort ↔ OutPort 연결 관리
- PortStore: 디바이스별 포트 정보 저장
- RoutingMatrix: OutPort → InPort 연결 매트릭스
- Transform: 값 변환 (scale, offset, threshold, invert 등)
"""
import os
import json
import threading
import queue
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List, Callable
from pathlib import Path
import sys

def log(*a, **k): print(*a, file=sys.stderr, flush=True, **k)

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ========= Transform Functions =========
class Transform:
    """값 변환 클래스"""
    
    @staticmethod
    def apply(value: float, transform_config: Dict[str, Any]) -> float:
        """변환 설정에 따라 값 변환"""
        if not transform_config:
            return value
        
        result = value
        
        # 1. Scale (곱하기)
        if "scale" in transform_config:
            result *= transform_config["scale"]
        
        # 2. Offset (더하기)
        if "offset" in transform_config:
            result += transform_config["offset"]
        
        # 3. Clamp (범위 제한)
        if "min" in transform_config:
            result = max(result, transform_config["min"])
        if "max" in transform_config:
            result = min(result, transform_config["max"])
        
        # 4. Threshold (임계값 - bool 변환)
        if "threshold" in transform_config:
            threshold = transform_config["threshold"]
            mode = transform_config.get("threshold_mode", "above")  # above, below, equal
            if mode == "above":
                result = 1.0 if result > threshold else 0.0
            elif mode == "below":
                result = 1.0 if result < threshold else 0.0
            elif mode == "equal":
                result = 1.0 if abs(result - threshold) < 0.001 else 0.0
        
        # 5. Invert (반전)
        if transform_config.get("invert", False):
            result = -result
        
        # 6. Map range (범위 매핑)
        if "map_from" in transform_config and "map_to" in transform_config:
            from_min, from_max = transform_config["map_from"]
            to_min, to_max = transform_config["map_to"]
            if from_max != from_min:
                normalized = (result - from_min) / (from_max - from_min)
                result = to_min + normalized * (to_max - to_min)
        
        return result


# ========= Port Store =========
class PortStore:
    """디바이스별 포트 정보 저장소"""
    
    def __init__(self):
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._latest_outport_values: Dict[str, Dict[str, Any]] = {}
        self._latest_inport_values: Dict[str, Dict[str, Any]] = {}
        self._recent_events = deque(maxlen=max(50, int(os.getenv("PORT_DEBUG_BUFFER_SIZE", "500"))))
        self._lock = threading.Lock()

    def _append_event_unlocked(self, event_type: str, **data):
        entry = {
            "type": event_type,
            "timestamp": data.pop("timestamp", now_iso()),
            **data,
        }
        self._recent_events.append(entry)
        return entry
    
    def upsert_ports_announce(self, device_id: str, msg: Dict[str, Any]):
        """ports.announce 메시지 처리"""
        with self._lock:
            self._devices[device_id] = {
                "device_id": device_id,
                "outports": msg.get("outports", []),
                "inports": msg.get("inports", []),
                "timestamp": msg.get("timestamp", now_iso()),
                "last_seen": now_iso()
            }
            self._append_event_unlocked(
                "ports_announce",
                device_id=device_id,
                outports=len(msg.get("outports", [])),
                inports=len(msg.get("inports", [])),
            )
        # log(f"[PORT_STORE] Device {device_id}: {len(msg.get('outports', []))} outports, {len(msg.get('inports', []))} inports")

    def record_outport_value(self, device_id: str, port_name: str, value: float, protocol: str, timestamp: str | None = None):
        port_id = f"{device_id}/{port_name}"
        ts = timestamp or now_iso()
        with self._lock:
            self._latest_outport_values[port_id] = {
                "port_id": port_id,
                "device_id": device_id,
                "port_name": port_name,
                "value": value,
                "protocol": protocol,
                "last_seen": ts,
            }
            self._append_event_unlocked(
                "outport_value",
                device_id=device_id,
                port_id=port_id,
                port_name=port_name,
                value=value,
                protocol=protocol,
                timestamp=ts,
            )

    def record_inport_dispatch(
        self,
        device_id: str,
        port_name: str,
        value: float,
        transport: str,
        success: bool,
        source_port_id: str | None = None,
    ):
        port_id = f"{device_id}/{port_name}"
        ts = now_iso()
        with self._lock:
            current = self._latest_inport_values.get(port_id, {})
            current.update(
                {
                    "port_id": port_id,
                    "device_id": device_id,
                    "port_name": port_name,
                    "last_bridge_value": value,
                    "last_bridge_transport": transport,
                    "last_bridge_success": success,
                    "last_bridge_at": ts,
                }
            )
            if source_port_id:
                current["last_source_port_id"] = source_port_id
            self._latest_inport_values[port_id] = current
            self._append_event_unlocked(
                "inport_dispatch",
                device_id=device_id,
                port_id=port_id,
                port_name=port_name,
                value=value,
                transport=transport,
                success=success,
                source_port_id=source_port_id,
                timestamp=ts,
            )

    def record_inport_ack(
        self,
        device_id: str,
        port_name: str,
        value: float,
        accepted: bool,
        protocol: str,
        source: str | None = None,
        timestamp: str | None = None,
    ):
        port_id = f"{device_id}/{port_name}"
        ts = timestamp or now_iso()
        with self._lock:
            current = self._latest_inport_values.get(port_id, {})
            current.update(
                {
                    "port_id": port_id,
                    "device_id": device_id,
                    "port_name": port_name,
                    "last_device_value": value,
                    "last_device_accepted": accepted,
                    "last_device_protocol": protocol,
                    "last_device_source": source or "ports.state",
                    "last_device_at": ts,
                }
            )
            self._latest_inport_values[port_id] = current
            self._append_event_unlocked(
                "inport_ack",
                device_id=device_id,
                port_id=port_id,
                port_name=port_name,
                value=value,
                accepted=accepted,
                protocol=protocol,
                source=source or "ports.state",
                timestamp=ts,
            )

    def record_route_result(
        self,
        source_port_id: str,
        target_port_id: str,
        input_value: float,
        output_value: float,
        success: bool,
        transform: Dict[str, Any] | None = None,
    ):
        with self._lock:
            self._append_event_unlocked(
                "route_result",
                source_port_id=source_port_id,
                target_port_id=target_port_id,
                input_value=input_value,
                output_value=output_value,
                success=success,
                transform=transform or {},
            )

    def record_router_queue(self, source_port_id: str, value: float, enqueued: bool, queue_size: int):
        with self._lock:
            self._append_event_unlocked(
                "route_queue",
                source_port_id=source_port_id,
                value=value,
                enqueued=enqueued,
                queue_size=queue_size,
            )
    
    def get_device_ports(self, device_id: str) -> Optional[Dict[str, Any]]:
        """특정 디바이스의 포트 정보 조회"""
        with self._lock:
            return self._devices.get(device_id)
    
    def get_all_outports(self) -> List[Dict[str, Any]]:
        """모든 OutPort 목록 (device_id 포함)"""
        result = []
        with self._lock:
            for device_id, data in self._devices.items():
                for port in data.get("outports", []):
                    result.append({
                        "device_id": device_id,
                        "port_id": f"{device_id}/{port['name']}",
                        **port
                    })
        return result
    
    def get_all_inports(self) -> List[Dict[str, Any]]:
        """모든 InPort 목록 (device_id 포함)"""
        result = []
        with self._lock:
            for device_id, data in self._devices.items():
                for port in data.get("inports", []):
                    result.append({
                        "device_id": device_id,
                        "port_id": f"{device_id}/{port['name']}",
                        **port
                    })
        return result
    
    def list_devices(self) -> List[Dict[str, Any]]:
        """포트가 등록된 모든 디바이스 목록"""
        with self._lock:
            return [
                {
                    "device_id": device_id,
                    "outport_count": len(data.get("outports", [])),
                    "inport_count": len(data.get("inports", [])),
                    "last_seen": data.get("last_seen")
                }
                for device_id, data in self._devices.items()
            ]
    
    def to_dict(self) -> Dict[str, Any]:
        """전체 데이터 dict 반환"""
        with self._lock:
            return json.loads(json.dumps(self._devices))

    def get_debug_snapshot(self, limit: int = 50) -> Dict[str, Any]:
        with self._lock:
            outports = sorted(
                self._latest_outport_values.values(),
                key=lambda item: item.get("last_seen", ""),
                reverse=True,
            )
            inports = sorted(
                self._latest_inport_values.values(),
                key=lambda item: max(item.get("last_device_at", ""), item.get("last_bridge_at", "")),
                reverse=True,
            )
            recent_events = list(self._recent_events)[-max(1, limit):]
            devices = list(self._devices.keys())

        return {
            "devices": devices,
            "latest_outports": outports,
            "latest_inports": inports,
            "recent_events": recent_events,
            "event_buffer_size": len(recent_events),
        }


# ========= Routing Matrix =========
class RoutingMatrix:
    """OutPort → InPort 라우팅 매트릭스"""
    
    def __init__(self, config_path: str):
        self.config_path = config_path
        self._connections: List[Dict[str, Any]] = []
        self._targets_by_source: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.load_config()

    def _rebuild_index(self):
        idx: Dict[str, List[Dict[str, Any]]] = {}
        for conn in self._connections:
            source = conn.get("source")
            if not source:
                continue
            idx.setdefault(source, []).append({
                "target": conn.get("target"),
                "transform": conn.get("transform", {}),
                "enabled": conn.get("enabled", True)
            })
        self._targets_by_source = idx

    def _has_path(self, start: str, goal: str, visited: Optional[set] = None) -> bool:
        """Simple DFS on current routing graph to detect cycles."""
        if start == goal:
            return True
        if visited is None:
            visited = set()
        if start in visited:
            return False
        visited.add(start)

        for edge in self._targets_by_source.get(start, []):
            nxt = edge.get("target")
            if not nxt:
                continue
            if self._has_path(nxt, goal, visited):
                return True
        return False
    
    def load_config(self):
        """설정 파일에서 라우팅 매트릭스 로드"""
        try:
            if Path(self.config_path).exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._connections = data.get("connections", [])
                self._rebuild_index()
                log(f"[ROUTING] Loaded {len(self._connections)} connections from {self.config_path}")
            else:
                self._connections = []
                self._rebuild_index()
                self.save_config()
                log(f"[ROUTING] Created empty routing config at {self.config_path}")
        except Exception as e:
            log(f"[ROUTING] Error loading config: {e}")
            self._connections = []
            self._rebuild_index()
    
    def save_config(self):
        """라우팅 매트릭스를 파일에 저장"""
        try:
            os.makedirs(os.path.dirname(self.config_path) if os.path.dirname(self.config_path) else ".", exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "connections": self._connections,
                    "updated_at": now_iso()
                }, f, indent=2, ensure_ascii=False)
            log(f"[ROUTING] Saved {len(self._connections)} connections to {self.config_path}")
            return True
        except Exception as e:
            log(f"[ROUTING] Error saving config: {e}")
            return False
    
    def connect(self, source_port_id: str, target_port_id: str, 
                transform: Optional[Dict[str, Any]] = None,
                enabled: bool = True,
                description: str = "") -> Dict[str, Any]:
        """
        OutPort → InPort 연결 추가
        
        Args:
            source_port_id: "device_id/port_name" 형식의 OutPort ID
            target_port_id: "device_id/port_name" 형식의 InPort ID
            transform: 변환 설정 (scale, offset, threshold 등)
            enabled: 연결 활성화 여부
            description: 연결 설명
        
        Returns:
            생성된 연결 정보
        """
        with self._lock:
            if "/" not in source_port_id or "/" not in target_port_id:
                raise ValueError("source/target must be in 'device_id/port_name' format")
            if source_port_id == target_port_id:
                raise ValueError("source and target cannot be identical")
            if self._has_path(target_port_id, source_port_id):
                raise ValueError("connection would create a routing cycle")

            # 중복 체크
            for conn in self._connections:
                if conn["source"] == source_port_id and conn["target"] == target_port_id:
                    # log(f"[ROUTING] Connection already exists: {source_port_id} → {target_port_id}")
                    return conn
            
            connection = {
                "id": f"{source_port_id}→{target_port_id}",
                "source": source_port_id,
                "target": target_port_id,
                "transform": transform or {},
                "enabled": enabled,
                "description": description,
                "created_at": now_iso()
            }
            
            self._connections.append(connection)
            self._rebuild_index()
            self.save_config()
            
            # log(f"[ROUTING] Connected: {source_port_id} → {target_port_id}")
            return connection
    
    def disconnect(self, source_port_id: str, target_port_id: str) -> bool:
        """연결 해제"""
        with self._lock:
            original_len = len(self._connections)
            self._connections = [
                c for c in self._connections 
                if not (c["source"] == source_port_id and c["target"] == target_port_id)
            ]
            
            if len(self._connections) < original_len:
                self._rebuild_index()
                self.save_config()
                # log(f"[ROUTING] Disconnected: {source_port_id} → {target_port_id}")
                return True
            
            # log(f"[ROUTING] Connection not found: {source_port_id} → {target_port_id}")
            return False
    
    def disconnect_by_id(self, connection_id: str) -> bool:
        """연결 ID로 해제"""
        with self._lock:
            original_len = len(self._connections)
            self._connections = [c for c in self._connections if c["id"] != connection_id]
            
            if len(self._connections) < original_len:
                self._rebuild_index()
                self.save_config()
                # log(f"[ROUTING] Disconnected by ID: {connection_id}")
                return True
            return False
    
    def update_connection(self, connection_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """연결 설정 업데이트"""
        with self._lock:
            for conn in self._connections:
                if conn["id"] == connection_id:
                    if "transform" in updates:
                        conn["transform"] = updates["transform"]
                    if "enabled" in updates:
                        conn["enabled"] = updates["enabled"]
                    if "description" in updates:
                        conn["description"] = updates["description"]
                    conn["updated_at"] = now_iso()
                    self._rebuild_index()
                    self.save_config()
                    return conn
            return None
    
    def get_targets_for_source(self, source_port_id: str) -> List[Dict[str, Any]]:
        """
        특정 OutPort에 연결된 모든 InPort와 transform 정보 반환
        (라우팅 시 사용)
        """
        with self._lock:
            targets = self._targets_by_source.get(source_port_id, [])
            return [t for t in targets if t.get("enabled", True)]
    
    def get_all_connections(self) -> List[Dict[str, Any]]:
        """모든 연결 목록"""
        with self._lock:
            return json.loads(json.dumps(self._connections))
    
    def get_connection(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """특정 연결 조회"""
        with self._lock:
            for conn in self._connections:
                if conn["id"] == connection_id:
                    return json.loads(json.dumps(conn))
            return None
    
    def get_matrix_view(self, port_store: PortStore) -> Dict[str, Any]:
        """
        Matrix 형태의 뷰 반환 (UI용)
        
        Returns:
            {
                "outports": [...],  # 행 (sources)
                "inports": [...],   # 열 (targets)
                "matrix": {
                    "source_id": {
                        "target_id": { "connected": true, "transform": {...}, ... }
                    }
                }
            }
        """
        outports = port_store.get_all_outports()
        inports = port_store.get_all_inports()
        
        matrix = {}
        for outport in outports:
            source_id = outport["port_id"]
            matrix[source_id] = {}
            for inport in inports:
                target_id = inport["port_id"]
                matrix[source_id][target_id] = {"connected": False}
        
        with self._lock:
            for conn in self._connections:
                source = conn["source"]
                target = conn["target"]
                if source in matrix and target in matrix.get(source, {}):
                    matrix[source][target] = {
                        "connected": True,
                        "enabled": conn.get("enabled", True),
                        "transform": conn.get("transform", {}),
                        "description": conn.get("description", ""),
                        "connection_id": conn["id"]
                    }
        
        return {
            "outports": outports,
            "inports": inports,
            "matrix": matrix,
            "connection_count": len(self._connections)
        }


# ========= Port Router (실제 라우팅 수행) =========
class PortRouter:
    """
    OutPort 데이터를 받아서 연결된 InPort로 라우팅
    """
    
    def __init__(
        self,
        routing_matrix: RoutingMatrix,
        publish_callback: Callable[[str, str, float], bool],
        debug_store: PortStore | None = None,
    ):
        """
        Args:
            routing_matrix: 라우팅 매트릭스
            publish_callback: InPort로 값을 발행하는 콜백 함수
                              (device_id, port_name, value) -> bool
        """
        self.routing_matrix = routing_matrix
        self.publish_callback = publish_callback
        self.debug_store = debug_store
        self._stats = {
            "total_routed": 0,
            "total_dropped": 0,
            "last_routed_at": None
        }
        self._lock = threading.Lock()
    
    def route(self, source_device_id: str, source_port_name: str, value: float) -> int:
        """
        OutPort 데이터를 연결된 InPort들로 라우팅
        
        Args:
            source_device_id: 소스 디바이스 ID
            source_port_name: 소스 포트 이름
            value: 원본 값
        
        Returns:
            라우팅된 타겟 수
        """
        source_port_id = f"{source_device_id}/{source_port_name}"
        targets = self.routing_matrix.get_targets_for_source(source_port_id)
        
        if not targets:
            return 0
        
        routed_count = 0
        
        for target_info in targets:
            if not target_info.get("enabled", True):
                continue
            
            target_port_id = target_info["target"]
            transform_config = target_info.get("transform", {})
            
            # 값 변환
            transformed_value = Transform.apply(value, transform_config)
            
            # 타겟 파싱
            try:
                target_device_id, target_port_name = target_port_id.split("/", 1)
            except ValueError:
                # log(f"[ROUTER] Invalid target port_id: {target_port_id}")
                continue
            
            # InPort로 발행
            success = self.publish_callback(target_device_id, target_port_name, transformed_value)
            if self.debug_store:
                self.debug_store.record_route_result(
                    source_port_id,
                    target_port_id,
                    value,
                    transformed_value,
                    success,
                    transform_config,
                )
            
            if success:
                routed_count += 1
                # log(f"[ROUTER] {source_port_id} ({value}) → {target_port_id} ({transformed_value})")
            else:
                with self._lock:
                    self._stats["total_dropped"] += 1
        
        with self._lock:
            self._stats["total_routed"] += routed_count
            self._stats["last_routed_at"] = now_iso()
        
        return routed_count
    
    def get_stats(self) -> Dict[str, Any]:
        """라우팅 통계"""
        with self._lock:
            return json.loads(json.dumps(self._stats))


class AsyncPortRouter:
    """
    Queue-based router wrapper.
    Keeps transport receive path fast by offloading routing work to worker threads.
    """

    def __init__(
        self,
        inner_router: PortRouter,
        workers: int = 2,
        queue_size: int = 5000
    ):
        self.inner_router = inner_router
        self._q: queue.Queue = queue.Queue(maxsize=max(1, queue_size))
        self._running = True
        self._workers: List[threading.Thread] = []
        self._stats = {
            "queued": 0,
            "processed": 0,
            "enqueue_dropped": 0,
            "queue_size": 0
        }
        self._lock = threading.Lock()

        for i in range(max(1, workers)):
            t = threading.Thread(target=self._worker_loop, name=f"route-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def _worker_loop(self):
        while self._running:
            try:
                source_device_id, source_port_name, value = self._q.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                self.inner_router.route(source_device_id, source_port_name, value)
            finally:
                with self._lock:
                    self._stats["processed"] += 1
                    self._stats["queue_size"] = self._q.qsize()

    def route(self, source_device_id: str, source_port_name: str, value: float) -> int:
        try:
            self._q.put_nowait((source_device_id, source_port_name, value))
            with self._lock:
                self._stats["queued"] += 1
                self._stats["queue_size"] = self._q.qsize()
                queue_size = self._stats["queue_size"]
            if self.inner_router.debug_store:
                self.inner_router.debug_store.record_router_queue(
                    f"{source_device_id}/{source_port_name}",
                    value,
                    enqueued=True,
                    queue_size=queue_size,
                )
            return 1
        except queue.Full:
            with self._lock:
                self._stats["enqueue_dropped"] += 1
                queue_size = self._q.qsize()
            if self.inner_router.debug_store:
                self.inner_router.debug_store.record_router_queue(
                    f"{source_device_id}/{source_port_name}",
                    value,
                    enqueued=False,
                    queue_size=queue_size,
                )
            return 0

    def get_stats(self) -> Dict[str, Any]:
        out = self.inner_router.get_stats()
        with self._lock:
            out.update(json.loads(json.dumps(self._stats)))
        return out


# ========= 테스트용 =========
if __name__ == "__main__":
    # 테스트
    store = PortStore()
    
    # 가상 announce
    store.upsert_ports_announce("dev-A", {
        "outports": [
            {"name": "impact_live", "type": "outport", "data_type": "float"}
        ],
        "inports": [
            {"name": "var_a", "type": "inport", "data_type": "float"},
            {"name": "var_b", "type": "inport", "data_type": "float"}
        ]
    })
    
    store.upsert_ports_announce("dev-B", {
        "outports": [
            {"name": "sensor", "type": "outport", "data_type": "float"}
        ],
        "inports": [
            {"name": "motor", "type": "inport", "data_type": "float"}
        ]
    })
    
    print("All OutPorts:", store.get_all_outports())
    print("All InPorts:", store.get_all_inports())
    
    # 라우팅 매트릭스
    matrix = RoutingMatrix("./test_routing.json")
    
    # 연결 생성
    matrix.connect("dev-A/impact_live", "dev-A/var_a", transform={"scale": 2.0})
    matrix.connect("dev-A/impact_live", "dev-B/motor", transform={"threshold": 10.0})
    
    print("\nMatrix View:", json.dumps(matrix.get_matrix_view(store), indent=2))
    
    # 라우팅 테스트
    def mock_publish(device_id, port_name, value):
        print(f"  [PUBLISH] {device_id}/{port_name} = {value}")
        return True
    
    router = PortRouter(matrix, mock_publish)
    
    print("\nRouting test (value=5.0):")
    router.route("dev-A", "impact_live", 5.0)
    
    print("\nRouting test (value=15.0):")
    router.route("dev-A", "impact_live", 15.0)
