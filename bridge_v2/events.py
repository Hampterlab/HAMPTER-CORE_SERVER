from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class DeviceAnnounced:
    device_id: str
    payload: Dict[str, Any]
    protocol: str


@dataclass(frozen=True)
class PortDataReceived:
    device_id: str
    port_name: str
    value: float


@dataclass(frozen=True)
class CommandRequested:
    device_id: str
    tool: str
    args: Dict[str, Any]


@dataclass(frozen=True)
class CommandResultReceived:
    request_id: str
    payload: Dict[str, Any]
