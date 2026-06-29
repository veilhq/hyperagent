"""ACP Bridge — relays between a TCP socket and kiro-cli's stdio.

Launched as a subprocess by hyperagent.py. Communicates with the parent
over a local TCP connection so PyWebView can't interfere with pipes.

Usage: python acp_bridge.py <port>
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

PORTAL_ROOT = Path(__file__).parent.parent.parent.resolve()
LOG = Path(__file__).parent / "bridge.log"

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.time():.3f} {msg}\n")


def find_kiro():
    found = shutil.which("kiro-cli")
    if found:
        return found
    fallback = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "bin" / "kiro-cli.exe"
    return str(fallback) if fallback.exists() else None


def main():
    port = int(sys.argv[1])
    kiro = find_kiro()
    if not kiro:
        sys.exit(1)

    # Spawn kiro-cli
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    proc = subprocess.Popen(
        [kiro, "acp", "--trust-all-tools"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(PORTAL_ROOT),
        startupinfo=si,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    # Connect to parent
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    sf = sock.makefile("rwb")

    # Relay: kiro stdout → socket
    def relay_stdout():
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    log("relay_stdout: EOF from kiro")
                    break
                if line.strip():
                    sf.write(line)
                    sf.flush()
                    log(f"relay_stdout: forwarded {len(line)}b")
        except Exception as e:
            log(f"relay_stdout error: {e}")
        sock.close()
        log("relay_stdout: socket closed")

    # Relay: socket → kiro stdin
    def relay_stdin():
        try:
            while True:
                line = sf.readline()
                if not line:
                    log("relay_stdin: EOF from socket")
                    break
                proc.stdin.write(line)
                proc.stdin.flush()
                log(f"relay_stdin: forwarded {len(line)}b")
        except Exception as e:
            log(f"relay_stdin error: {e}")
        proc.terminate()
        log("relay_stdin: process terminated")

    # Forward stderr to parent as JSON-RPC notifications
    def relay_stderr():
        try:
            while True:
                line = proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log(f"stderr: {text}")
                    msg = json.dumps({"jsonrpc": "2.0", "method": "_bridge/stderr", "params": {"text": text}}) + "\n"
                    try:
                        sf.write(msg.encode())
                        sf.flush()
                    except Exception:
                        pass
        except Exception as e:
            log(f"relay_stderr error: {e}")

    threading.Thread(target=relay_stdout, daemon=True).start()
    threading.Thread(target=relay_stderr, daemon=True).start()
    relay_stdin()  # blocks until socket closes


if __name__ == "__main__":
    main()
