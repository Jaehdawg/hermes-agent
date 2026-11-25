"""Disabled-by-default approval service boundary; no live server or effect sender.

A gateway process sharing its Unix UID with a model terminal CANNOT authenticate
itself to a service using a Unix socket or a locally stored HMAC. The client
refuses to connect unless explicitly given a distinct model UID. The server
must independently check SO_PEERCRED for its configured gateway UID; a client
configuration is not evidence of that check. No gateway bootstrap registers this.
"""

import asyncio
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import socket
import stat
import struct
from typing import Protocol

from .approval_ingress import TelegramNotionDecision


class DecisionService(Protocol):
    async def decide(self, decision: TelegramNotionDecision) -> None:
        """Submit an ingress observation; service validates frozen proposal and replay."""


def encode_decision(decision: TelegramNotionDecision) -> bytes:
    """Versioned length-framed observation; never accept a model-supplied digest."""
    if not isinstance(decision, TelegramNotionDecision):
        raise TypeError("A parsed PTB decision is required")
    data = json.dumps({"version": 2, "decision": asdict(decision)},
                      sort_keys=True, separators=(",", ":")).encode()
    if len(data) > 2048:
        raise ValueError("oversize decision")
    return struct.pack("!I", len(data)) + data


@dataclass(frozen=True)
class UnixDecisionServiceClient:
    socket_path: Path
    model_uid: int
    service_uid: int
    timeout: float = 2.0

    async def decide(self, decision: TelegramNotionDecision) -> None:
        if not isinstance(decision, TelegramNotionDecision):
            raise TypeError("A parsed PTB decision is required")
        # Recheck immediately before connection: model and gateway sharing a UID
        # makes the supposed peer-credential boundary forgeable.
        gateway_uid = os.getuid()
        if (not all(type(uid) is int and uid >= 0 for uid in (self.model_uid, self.service_uid))
                or gateway_uid in (self.model_uid, self.service_uid)):
            raise PermissionError("approval requires distinct gateway, model and service UIDs")
        await asyncio.to_thread(self._send, decision)

    def _send(self, decision: TelegramNotionDecision) -> None:
        path = str(self.socket_path)
        if not path.startswith("/") or len(os.fsencode(path)) >= 104:
            raise ValueError("absolute, bounded Unix socket path required")
        parent = self.socket_path.parent
        parent_stat = parent.lstat()
        sock_stat = self.socket_path.lstat()
        if (not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != self.service_uid
                or parent_stat.st_mode & 0o022 or not stat.S_ISSOCK(sock_stat.st_mode)
                or sock_stat.st_uid != self.service_uid):
            raise PermissionError("decision socket and private parent must belong to service UID")
        frame = encode_decision(decision)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(self.timeout)
            conn.connect(path)
            # Also authenticate the server process, not merely its filesystem node.
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, peer_uid, _ = struct.unpack("3i", creds)
            if peer_uid != self.service_uid:
                raise PermissionError("unexpected decision service peer")
            conn.sendall(frame)
            header = self._recv_exact(conn, 4)
            size = struct.unpack("!I", header)[0]
            if size > 256:
                raise ValueError("oversize service response")
            response = json.loads(self._recv_exact(conn, size))
            if response != {"version": 2, "accepted": True}:
                raise RuntimeError("decision not accepted by service")

    @staticmethod
    def _recv_exact(conn: socket.socket, count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            chunk = conn.recv(count - len(result))
            if not chunk:
                raise ConnectionError("decision service disconnected")
            result.extend(chunk)
        return bytes(result)
