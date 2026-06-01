"""WiFi Interface module for FastPay simulation with real TCP/UDP support."""

from __future__ import annotations

import os
import tempfile
import json
import logging
import socket
import threading
import time
from queue import Queue, Empty
from typing import TYPE_CHECKING, Optional
from uuid import UUID

if TYPE_CHECKING:
    pass

from meshpay.types import Address, NodeType
from meshpay.messages import Message, MessageType



class TCPTransport:
    """TCP network interface for communication using mininet-wifi with real TCP sockets."""
    
    def __init__(self, node, address: Address) -> None:
        """Initialize TCPTransport with given address.
        
        Args:
            node: The authority node this interface belongs to
            address: Network address for this interface
        """
        self.node = node
        self.address = address
        self.is_connected = False
        self.connection_quality = 1.0
        
        # TCP server for receiving messages
        self.monitor_thread: Optional[threading.Thread] = None
        self.running = False
        
    def connect(self) -> bool:
        """Launch a TCP server **inside** the authority namespace and
        start a monitor thread that feeds authority.message_queue."""
        try:
            if not self._start_tcp_server_in_node():
                return False

            # monitor *.log and inject into queue
            self.running = True
            self.monitor_thread = threading.Thread(
                target=self._monitor_messages, daemon=True
            )
            self.monitor_thread.start()

            self.is_connected = True
            self.node.logger.info(
                f"WiFi interface connected on {self.address.ip_address}:{self.address.port}"
            )
            return True
        except Exception as exc:
            self.node.logger.error(f"TCPTransport.connect failed: {exc}")
            return False
    
    def disconnect(self) -> None:
        """Stop monitor thread; authority kills server with SIGTERM automatically
        when node stops."""
        self.running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=2.0)
        uds_path = f"/tmp/{self.address.node_id}_control.sock"
        if os.path.exists(uds_path):
            try:
                os.remove(uds_path)
            except Exception:
                pass
        self.node.logger.info("TCPTransport disconnected")
    
    def _start_tcp_server_in_node(self) -> bool:
        """Run the tiny TCP server inside the station namespace using popen."""
        server_script = self._create_tcp_server_script()
        if not server_script:
            return False

        # run in the node's namespace asynchronously via popen
        import subprocess
        cmd = ["python3", server_script, "0.0.0.0", str(self.address.port), self.address.node_id]
        # Store process reference to avoid it being GCed too early
        self._server_proc = self.node.popen(cmd)
        return True

    def _monitor_messages(self) -> None:
        """Tail the log file produced by the in-namespace server and push
        JSON payloads into authority.message_queue."""
        log_path = f"/tmp/{self.address.node_id}_messages.log"
        processed = 0
        while self.running:
            try:
                if not os.path.exists(log_path):
                    time.sleep(0.2)
                    continue

                with open(log_path) as fh:
                    lines = fh.readlines()

                for line in lines[processed:]:
                    ix = line.find('{')
                    if ix == -1:
                        continue
                    data = json.loads(line[ix:])
                    msg = self._parse_message(data)
                    if msg:
                        self.node.message_queue.put(msg)

                processed = len(lines)
                time.sleep(0.1)
            except Exception as exc:
                self.node.logger.error(f"Monitor error: {exc}")
                time.sleep(1)

    def _create_tcp_server_script(self) -> Optional[str]:
        """Write a tiny server that:
           - binds to 0.0.0.0:<port> (works in the namespace),
           - binds to a Unix Domain Socket at /tmp/<node_id>_control.sock to proxy outgoing messages,
           - reads length-prefixed JSON,
           - appends JSON lines to /tmp/<node_id>_messages.log,
           - ACKs the client."""
        try:
            script = f"""#!/usr/bin/env python3
import json, socket, struct, sys, time, threading, os

LOG = f'/tmp/{{sys.argv[3]}}_messages.log'
UDS_PATH = f'/tmp/{{sys.argv[3]}}_control.sock'

def handle_tcp_client(c, nid):
    try:
        with c:
            c.settimeout(5.0)
            ln = c.recv(4, socket.MSG_WAITALL)
            if len(ln) != 4:
                return
            size = struct.unpack('>I', ln)[0]
            raw = c.recv(size, socket.MSG_WAITALL)
            if len(raw) != size:
                return
            with open(LOG, 'a') as f:
                f.write(f'{{time.time()}}: '+raw.decode()+'\\n')
            ack = json.dumps({{'status':'received','node_id':nid}}).encode()
            c.sendall(struct.pack('>I', len(ack))+ack)
    except Exception:
        pass

def tcp_server_loop(ip, port, nid):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((ip, int(port)))          # ip will be 0.0.0.0
    srv.listen(64)
    while True:
        try:
            c, _ = srv.accept()
            threading.Thread(target=handle_tcp_client, args=(c, nid), daemon=True).start()
        except Exception:
            time.sleep(0.1)

def handle_uds_client(c):
    try:
        with c:
            c.settimeout(5.0)
            ln = c.recv(4, socket.MSG_WAITALL)
            if len(ln) != 4:
                return
            size = struct.unpack('>I', ln)[0]
            raw = c.recv(size, socket.MSG_WAITALL).decode('utf-8')
            if len(raw) != size:
                return
            cmd = json.loads(raw)
            
            target_ip = cmd['target_ip']
            target_port = cmd['target_port']
            msg_bytes = json.dumps(cmd['message_data']).encode('utf-8')
            
            success = False
            err_msg = ""
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)  # short timeout: out-of-range is normal in DTN
                sock.connect((target_ip, target_port))
                sock.sendall(struct.pack('>I', len(msg_bytes)) + msg_bytes)
                
                hdr = sock.recv(4, socket.MSG_WAITALL)
                if len(hdr) == 4:
                    size = struct.unpack('>I', hdr)[0]
                    sock.recv(size, socket.MSG_WAITALL)
                    success = True
                sock.close()
            except Exception as exc:
                err_msg = str(exc)
            
            resp = json.dumps({{"success": success, "error": err_msg}}).encode('utf-8')
            c.sendall(struct.pack('>I', len(resp)) + resp)
    except Exception:
        pass

def uds_server_loop():
    if os.path.exists(UDS_PATH):
        try: os.remove(UDS_PATH)
        except Exception: pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(UDS_PATH)
    srv.listen(64)
    while True:
        try:
            c, _ = srv.accept()
            threading.Thread(target=handle_uds_client, args=(c,), daemon=True).start()
        except Exception:
            time.sleep(0.1)

def main(ip, port, nid):
    with open(LOG, 'a') as f:
        f.write(f'{{time.time()}}: server up\\n')
    
    t_tcp = threading.Thread(target=tcp_server_loop, args=(ip, port, nid), daemon=True)
    t_tcp.start()
    
    uds_server_loop()

if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
"""
            import tempfile, textwrap, os
            fd, path = tempfile.mkstemp(suffix=".py")
            with os.fdopen(fd, "w") as f:
                f.write(textwrap.dedent(script))
            os.chmod(path, 0o755)
            return path
        except Exception as exc:   # pragma: no cover
            self.node.logger.error(f"Create-script failed: {exc}")
            return None
        
    def _parse_message(self, message_data: dict) -> Optional[Message]:
        """Parse message data into Message object.
        
        Args:
            message_data: Raw message data
            
        Returns:
            Parsed message or None if invalid
        """
        try:
            # check if message type is valid
            if message_data.get('message_type') not in [m.value for m in MessageType]:
                return None
            
            sender_data = message_data.get('sender', {})
            sender = Address(
                node_id=sender_data.get('node_id', ''),
                ip_address=sender_data.get('ip_address', ''),
                port=sender_data.get('port', 0),
                node_type=NodeType(sender_data.get('node_type', 'UNKNOWN'))
            )
            
            message = Message(
                message_id=UUID(message_data['message_id']),
                message_type=MessageType(message_data['message_type']),
                sender=sender,
                recipient=self.address,
                timestamp=message_data['timestamp'],
                payload=message_data['payload']
            )
            self.node.logger.debug(f"Received message from {message.sender.ip_address}:{message.sender.port}: {message}")
            return message
        except Exception as e:
            self.node.logger.error(f"Failed to parse message: {e}")
            return None
    
    def send_message(self, message: Message, target: Address) -> bool:
        """Send *message* to *target* using persistent UDS proxy in station's namespace."""
        import socket
        import struct
        import json

        message_data = {
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
        }

        def json_serial(obj):
            from uuid import UUID
            from enum import Enum
            if isinstance(obj, UUID):
                return str(obj)
            if isinstance(obj, Enum):
                return obj.value
            raise TypeError(f"Type {type(obj)} not serializable")

        uds_path = f"/tmp/{self.address.node_id}_control.sock"
        cmd = {
            "target_ip": target.ip_address,
            "target_port": target.port,
            "message_data": message_data
        }
        cmd_bytes = json.dumps(cmd, default=json_serial).encode('utf-8')

        # Retry connecting to UDS proxy if it is starting up
        for attempt in range(3):
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect(uds_path)
                
                # Send length-prefixed command
                sock.sendall(struct.pack('>I', len(cmd_bytes)) + cmd_bytes)
                
                # Read response
                hdr = sock.recv(4, socket.MSG_WAITALL)
                if len(hdr) == 4:
                    size = struct.unpack('>I', hdr)[0]
                    resp_bytes = sock.recv(size, socket.MSG_WAITALL)
                    resp = json.loads(resp_bytes.decode('utf-8'))
                    sock.close()
                    if resp.get("success"):
                        return True
                    else:
                        self.node.logger.debug(
                            f"Send failed via UDS proxy to {target.ip_address}:{target.port}: {resp.get('error')} (node out of range)"
                        )
                        return False
                sock.close()
                return False
            except Exception as exc:
                if attempt == 2:
                    if self.running:
                        self.node.logger.error(f"Failed to send message via UDS proxy: {exc}")
                    else:
                        self.node.logger.debug(f"Failed to send message via UDS proxy during shutdown: {exc}")
                time.sleep(0.1)
        return False
    
    def receive_message(self, timeout: float = 1.0) -> Optional[Message]:
        """Receive message from network queue.
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            Received message or None if timeout
        """
        if not self.is_connected:
            return None
        
        try:
            message = self.node.message_queue.get(timeout=timeout)
            return message
        except Empty:
            return None
        except Exception as e:
            self.node.logger.error(f"Failed to receive message: {e}")
            return None 