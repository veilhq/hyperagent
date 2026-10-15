"""ACP Bridge - relays between a TCP socket and an ACP agent's stdio.

Launched as a subprocess by ACPClient. Communicates with the parent over a
local TCP connection so PyWebView can't interfere with pipes.

Usage: python acp_bridge.py <port> [<argv-json>]

<argv-json> is a JSON array with the agent command line, resolved by the
parent's active backend. Environment overrides (e.g. credentials) arrive via
this process's own environment, never on the command line. Without
<argv-json> the bridge launches kiro-cli directly, so it still runs
standalone for manual debugging.
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
HYPERSPACE_ROOT = Path(__file__).parent.parent.resolve()

# Standalone fallback only: the parent normally passes the full agent argv.
# Mirrors KiroBackend.ACP_AGENT in backends.py, which documents why the
# workspace agent (with its document-write hook) is required.
ACP_AGENT = "hyperspace"

# ---------------------------------------------------------------------------
# Structured logging (shared ecosystem logger)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(HYPERSPACE_ROOT / ".hyperkit" / "python"))
from hyper_logging import setup_logger, set_output_level, TRACE  # noqa: E402

# Bridge log level is env-configurable; INFO default keeps the log to lifecycle events.
# Set HYPERAGENT_LOG_LEVEL=DEBUG or TRACE to enable relay byte counts.
import logging as _logging
import os as _os
_LEVEL_MAP = {"TRACE": TRACE, "DEBUG": _logging.DEBUG, "INFO": _logging.INFO,
              "WARNING": _logging.WARNING, "ERROR": _logging.ERROR}
_env_level = _os.environ.get("HYPERAGENT_LOG_LEVEL", "INFO").upper()
_log_level = _LEVEL_MAP.get(_env_level, _logging.INFO)
logger = setup_logger("bridge", level=_log_level)
# Re-assert via the helper rather than looping over logger.handlers: setting every
# handler directly would also raise the flight recorder's level and stop it
# capturing the TRACE/DEBUG context it replays when an error fires.
set_output_level(logger, _log_level)


def find_kiro():
    found = shutil.which("kiro-cli")
    if found:
        return found
    fallback = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "bin" / "kiro-cli.exe"
    return str(fallback) if fallback.exists() else None


def resolve_argv():
    """Agent argv from the parent, or the kiro-cli default when run standalone."""
    if len(sys.argv) > 2:
        try:
            argv = json.loads(sys.argv[2])
        except ValueError as e:
            logger.error("invalid argv-json: %s", e)
            return None
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            return argv
        logger.error("argv-json must be a non-empty list of strings")
        return None
    kiro = find_kiro()
    if not kiro:
        logger.error("kiro-cli not found")
        return None
    return [kiro, "acp", "--trust-all-tools", "--agent", ACP_AGENT]


def main():
    port = int(sys.argv[1])
    argv = resolve_argv()
    if not argv:
        sys.exit(1)

    logger.info("starting: port=%d agent=%s", port, argv[0])

    # RUST_BACKTRACE=1 asks a Rust agent (kiro-cli) for a panic backtrace on
    # stderr, which lands in bridge.log via the stderr relay instead of an
    # opaque STATUS_CONTROL_C_EXIT (0xC000013A). Zero cost when nothing panics
    # and ignored by non-Rust agents.
    child_env = os.environ.copy()
    child_env.setdefault("RUST_BACKTRACE", "1")

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(PORTAL_ROOT),
        env=child_env,
        startupinfo=si,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    logger.info("agent spawned: pid=%d", proc.pid)

    # Connect to parent
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    sf = sock.makefile("rwb")

    logger.info("connected to parent on port %d", port)

    # Serialize writes to the socket. Three relay threads (stdout, stderr,
    # watchdog) share `sf`; without this lock a large stdout payload can be
    # interleaved at the BufferedWriter's 8KB flush boundary by a concurrent
    # stderr or watchdog write, producing corrupt JSON on the parent side.
    sock_write_lock = threading.Lock()

    def sock_write(payload: bytes) -> bool:
        """Thread-safe write+flush to the parent socket. Returns False on error."""
        with sock_write_lock:
            try:
                sf.write(payload)
                sf.flush()
                return True
            except Exception as e:
                logger.debug("sock_write failed: %s", e)
                return False

    # Relay: kiro stdout → socket
    def relay_stdout():
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    logger.info("relay_stdout: EOF from kiro")
                    break
                if line.strip():
                    if not sock_write(line):
                        break
                    logger.trace("relay_stdout: forwarded %db", len(line))
        except Exception as e:
            logger.error("relay_stdout error: %s", e)
        sock.close()
        logger.info("relay_stdout: socket closed")

    # Relay: socket → kiro stdin
    def relay_stdin():
        try:
            while True:
                line = sf.readline()
                if not line:
                    logger.info("relay_stdin: EOF from socket")
                    break
                proc.stdin.write(line)
                proc.stdin.flush()
                logger.trace("relay_stdin: forwarded %db", len(line))
        except Exception as e:
            logger.error("relay_stdin error: %s", e)
        proc.terminate()
        logger.info("relay_stdin: process terminated")

    # Forward stderr to parent as JSON-RPC notifications
    def relay_stderr():
        try:
            while True:
                line = proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("stderr: %s", text)
                    msg = json.dumps({"jsonrpc": "2.0", "method": "_bridge/stderr", "params": {"text": text}}) + "\n"
                    sock_write(msg.encode())
        except Exception as e:
            logger.error("relay_stderr error: %s", e)

    # Watchdog: proc.wait() reliably fires on child exit even when stdout/stderr
    # pipes hang (Windows abnormal-exit case). Sends a notification to the parent
    # so it can transition state to crashed without waiting for a failed write.
    def watch_process():
        try:
            exit_code = proc.wait()
            logger.info("watchdog: kiro-cli exited code=%s", exit_code)
            msg = json.dumps({
                "jsonrpc": "2.0",
                "method": "_bridge/child_exited",
                "params": {"exitCode": exit_code},
            }) + "\n"
            if not sock_write(msg.encode()):
                logger.debug("watchdog: notify failed (socket already closed?)")
            # Force-close the socket to unwind any hanging readline calls
            # on either side (parent's reader, our relay_stdin).
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        except Exception as e:
            logger.error("watchdog error: %s", e)

    threading.Thread(target=watch_process, daemon=True).start()
    threading.Thread(target=relay_stdout, daemon=True).start()
    threading.Thread(target=relay_stderr, daemon=True).start()
    relay_stdin()  # blocks until socket closes


if __name__ == "__main__":
    main()
