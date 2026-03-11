"""UDP transport for FastPay Wi-Fi simulation.

Thin wrapper extracted from the original *client.py* so that transports live in
stand-alone modules and can be reused by both client and authority code.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from queue import Queue, Empty
from typing import Optional, Dict, Union
from uuid import UUID
import tempfile
import os

from meshpay.types import Address, NodeType
from meshpay.messages import Message, MessageType


class UDPTransport:  # pylint: disable=too-few-public-methods
    """Connection-less transport implemented entirely inside the station namespace.

    A tiny UDP server is launched with *station.cmd()* so that all network I/O occurs in the
    correct namespace.  Incoming datagrams are appended to a logfile; a background thread tails
    the file and pushes fully-parsed :class:`mn_wifi.messages.Message` objects into an internal
    queue ready for `receive_message()`.
    """

    LOG_TMPL = "/tmp/{node}_udp_messages.log"

    def __init__(self, node, address: Address) -> None:  # noqa: D401
        self.node = node
        self.address = address
        self.logger = logging.getLogger(f"UDPTransport-{address.node_id}")

        self._queue: "Queue[Message]" = Queue()
        self.running = False
        self._monitor_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # NetworkTransport API
    # ------------------------------------------------------------------

    def connect(self) -> bool:  # type: ignore[override]
        try:
            if not self._start_udp_server_in_node():
                return False
            self.running = True
            self._monitor_thread = threading.Thread(target=self._monitor_log, daemon=True)
            self._monitor_thread.start()
            return True
        except Exception as exc:  # pragma: no cover
            self.logger.error(f"UDPTransport.connect failed: {exc}")
            return False

    def disconnect(self) -> None:  # type: ignore[override]
        self.running = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)

    def send_message(self, message: Message, target: Address) -> bool:  # type: ignore[override]
        """Emit *message* to *target* via a short-lived Python script executed in namespace."""
        try:
            payload = json.dumps({
                "message_id": str(message.message_id),
                "message_type": message.message_type.value,
                "sender": {
                    "node_id": message.sender.node_id,
                    "ip_address": message.sender.ip_address,
                    "port": message.sender.port,
                    "node_type": message.sender.node_type.value,
                },
                "timestamp": message.timestamp,
                "payload": message.payload,
            })

            script = (
                "import socket,sys,json;"
                "p=json.loads(sys.argv[3]);"
                "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
                "s.sendto(json.dumps(p).encode(),(sys.argv[1],int(sys.argv[2])));"
                "s.close()"
            )

            # Escape the payload properly
            escaped_payload = payload.replace('\\', '\\\\').replace('"', '\\"')
            
            # Build the command without using f-string for the escaped parts
            cmd = "python3 -c \"{}\" {} {} '{}'".format(
                script,
                target.ip_address,
                target.port,
                escaped_payload
            )
            
            if not hasattr(self.node, '_cmd_lock'):
                import threading
                self.node._cmd_lock = threading.Lock()
                
            with self.node._cmd_lock:
                self.node.cmd(cmd)
            return True
        except Exception as exc:  # pragma: no cover
            self.logger.error(f"UDP send failed: {exc}")
            return False

    def receive_message(self, timeout: float = 1.0) -> Optional[Message]:  # type: ignore[override]
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_udp_server_in_node(self) -> bool:
        server_script = self._create_server_script()
        if not server_script:
            return False
        self.node.cmd(
            f"python3 {server_script} 0.0.0.0 {self.address.port} {self.address.node_id} &"
        )
        return True

    def _create_server_script(self) -> Optional[str]:
        try:
            script = f"""#!/usr/bin/env python3
import json, socket, sys, time, os
LOG = '/tmp/{{}}_udp_messages.log'.format(sys.argv[3])
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((sys.argv[1], int(sys.argv[2])))
while True:
    data, _ = sock.recvfrom(65536)
    with open(LOG, 'a') as f:
        f.write(f'{{time.time()}}: '+data.decode()+'\n')
"""
            fd, path = tempfile.mkstemp(suffix=".py")
            with os.fdopen(fd, "w") as fh:
                fh.write(script)
            os.chmod(path, 0o755)
            return path
        except Exception as exc:  # pragma: no cover
            self.logger.error(f"create UDP server script failed: {exc}")
            return None

    def _monitor_log(self) -> None:
        log_path = self.LOG_TMPL.format(node=self.address.node_id)
        processed = 0
        while self.running:
            try:
                if not os.path.exists(log_path):
                    time.sleep(0.2)
                    continue
                with open(log_path) as fh:
                    lines = fh.readlines()
                for line in lines[processed:]:
                    idx = line.find('{')
                    if idx == -1:
                        continue
                    data = json.loads(line[idx:])
                    msg = self._deserialise(data)
                    if msg:
                        self._queue.put(msg)
                processed = len(lines)
                time.sleep(0.1)
            except Exception as exc:  # pragma: no cover
                self.logger.error(f"UDP monitor error: {exc}")
                time.sleep(1)

    def _deserialise(self, data: Dict[str, Union[str, Dict[str, str]]]) -> Optional[Message]:
        try:
            sender_raw = data.get("sender", {})  # type: ignore[arg-type]
            sender = Address(
                node_id=str(sender_raw.get("node_id", "")),
                ip_address=str(sender_raw.get("ip_address", "")),
                port=int(sender_raw.get("port", 0)),
                node_type=NodeType(sender_raw.get("node_type", NodeType.CLIENT.value)),
            )
            return Message(
                message_id=UUID(data["message_id"]),
                message_type=MessageType(data["message_type"]),
                sender=sender,
                recipient=self.address,
                timestamp=float(data["timestamp"]),
                payload=data.get("payload", {}),  # type: ignore[arg-type]
            )
        except Exception as exc:  # pragma: no cover
            self.logger.error(f"UDP deserialisation failed: {exc}")
            return None 