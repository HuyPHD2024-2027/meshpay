import time
import queue
import subprocess
from typing import Optional, Any

from meshpay.logger.utils import (
    format_message,
    format_transfer_message,
    format_network_message,
    format_balance_info,
)

class BridgeLogger:
    """Logger for authority message processing with terminal output."""
    
    def __init__(self, name: str, log_file: Optional[str] = None) -> None:
        """Initialize the authority logger.
        
        Args:
            authority_name: Name of the authority
            log_file: Optional log file path (defaults to /tmp/{authority_name}_log.txt)
        """
        self.name = name
        self.log_file = log_file or f"/tmp/{name}_bridge.log"
        self.log_queue = queue.Queue()
        self.running = True
        self.terminal_process: Optional[subprocess.Popen] = None
        
        # Color mapping for different authorities
        self.colors = {
            'bridge': '\033[95m',    
        }
        self.reset_color = '\033[0m'
        
        # Create log file
        try:
            with open(self.log_file, 'w') as f:
                f.write(f"=== {self.name} Bridge Log ===\n")
                f.write(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*50 + "\n\n")
        except IOError as e:
            print(f"Warning: Could not create log file {self.log_file}: {e}")
    
    def start_xterm(self) -> None:
        """Start an xterm terminal to display the log file."""
        if self.terminal_process:
            self.terminal_process.terminate()
            self.terminal_process = None
        
        # Create xterm command with custom title and colors
        xterm_cmd = [
            'xterm',
            '-title', f'{self.name} Bridge Log',
            '-geometry', '100x30',
            '-bg', 'black',
            '-fg', 'green',
            '-e', f'tail -f {self.log_file}'
        ]
        
        try:
            self.terminal_process = subprocess.Popen(xterm_cmd)
            print(f"Opened xterm for {self.name} (PID: {self.terminal_process.pid})")
        except Exception as e:
            print(f"Failed to open xterm for {self.name}: {e}")
    
    def close_xterm(self) -> None:
        """Close the xterm terminal."""
        if self.terminal_process:
            try:
                self.terminal_process.terminate()
                self.terminal_process.wait(timeout=2)
            except Exception:
                try:
                    self.terminal_process.kill()
                except Exception:
                    pass
            self.terminal_process = None

    def log(self, message: str) -> None:
        """Log a message with timestamp.
        
        Args:
            message: Message to log
        """
        timestamp = time.strftime('%H:%M:%S.%f')[:-3]
        log_entry = f"[{timestamp}] {self.name}: {message}"
        
        # Write to file
        try:
            with open(self.log_file, 'a') as f:
                f.write(log_entry + "\n")
                f.flush()  # Force write to disk
        except IOError:
            # If file writing fails, continue with console output
            pass
        
        # Also print to console with authority color coding
        color = self.colors.get(self.name, '')
        print(f"{color}{log_entry}{self.reset_color}")
    
    def error(self, message: str) -> None:
        """Log an error message."""
        self.log(f"❌ ERROR: {message}")
    
    def info(self, message: str) -> None:
        """Log an info message."""
        self.log(f"ℹ️  INFO: {message}")
    
    def warning(self, message: str) -> None:
        """Log a warning message."""
        self.log(f"⚠️  WARNING: {message}")
    
    def debug(self, message: Any) -> None:
        """Log a debug message with intelligent formatting."""
        formatted = format_message(message, max_length=150)
        self.log(f"🐛 DEBUG: {formatted}")
    
    def success(self, message: str) -> None:
        """Log a success message."""
        self.log(f"✅ SUCCESS: {message}")
    
    def processing(self, message: str) -> None:
        """Log a processing message."""
        self.log(f"⚙️  PROCESSING: {message}")
    
    def received(self, message: str) -> None:
        """Log a received message."""
        self.log(f"📨 RECEIVED: {message}")
    
    def sent(self, message: str) -> None:
        """Log a sent message."""
        self.log(f"📤 SENT: {message}")
    
    def validation(self, message: str) -> None:
        """Log a validation message."""
        self.log(f"🔍 VALIDATION: {message}")
    
    def balance(self, message: str) -> None:
        """Log a balance-related message."""
        self.log(f"💰 BALANCE: {message}")
    
    def transfer(self, message: str) -> None:
        """Log a transfer-related message."""
        self.log(f"🔄 TRANSFER: {message}")
    
    def ping(self, message: str) -> None:
        """Log a ping-related message."""
        self.log(f"🏓 PING: {message}")
    
    def close(self) -> None:
        """Close the logger."""
        self.running = False
        if self.terminal_process:
            try:
                self.terminal_process.terminate()
            except Exception:
                pass