#!/usr/bin/env python3
"""
Minimal Chrome CDP launcher with multi‑browser management and pretty logging.
"""

import logging
import os
import subprocess
import socket
import time
import signal
import atexit
from typing import Dict, List, Optional

# ────────────────── configuration ──────────────────
DEFAULT_PORT = 9222
DEFAULT_HEADLESS = True
CHROME_PATHS = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
    'google-chrome',
    'chromium'
]
USER_DATA_DIR_TEMPLATE = '/tmp/chrome-cdp-session-{port}'
STARTUP_TIMEOUT = 15  # seconds
LOG_LEVEL = logging.INFO

# Auto-port selection
PORT_RANGE_START = 9222
PORT_RANGE_END   = 9400
AUTO_REDIRECT_IF_BUSY = True  # if requested port is taken, pick the next slot

# PID files so this script can be used as a standalone tool across invocations
PIDFILE_TEMPLATE = '/tmp/chrome-cdp-{port}.pid'
# ───────────────────────────────────────────────────

USE_RICH_LOGGING = True

try:
    from rich.logging import RichHandler
    _RICH_OK = True
except Exception:
    _RICH_OK = False

def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("browser_cdp")
    logger.setLevel(LOG_LEVEL)
    logger.propagate = False
    logger.handlers.clear()

    if USE_RICH_LOGGING and _RICH_OK:
        handler = RichHandler(rich_tracebacks=False, markup=False, show_time=False, show_path=False)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
    else:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(levelname)s] %(message)s")

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

log = _setup_logger()


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0


def _which_chrome() -> str:
    for path in CHROME_PATHS:
        try:
            subprocess.run([path, '--version'], capture_output=True, check=True)
            return path
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise FileNotFoundError(
        "Chrome/Chromium executable not found. Install Chrome or adjust CHROME_PATHS.")



def find_free_port(preferred: Optional[int] = None, start: int = PORT_RANGE_START, end: int = PORT_RANGE_END) -> int:
    """Return a free port. If preferred is busy, scan upward then wrap once."""
    if preferred is not None and _port_is_free(preferred):
        return preferred
    if preferred is None:
        preferred = start
    for p in range(max(preferred, start), end + 1):
        if _port_is_free(p):
            return p
    for p in range(start, min(preferred, end + 1)):
        if _port_is_free(p):
            return p
    raise RuntimeError(f"No free port available in range {start}-{end}")

# ───── pidfile helpers (tool-friendly) ─────

def _pidfile_path(port: int) -> str:
    return PIDFILE_TEMPLATE.format(port=port)

def _write_pidfile(port: int, pid: int) -> None:
    try:
        with open(_pidfile_path(port), 'w') as f:
            f.write(str(pid))
    except Exception:
        pass

def _read_pidfile(port: int) -> Optional[int]:
    try:
        with open(_pidfile_path(port), 'r') as f:
            return int(f.read().strip())
    except Exception:
        return None

def _remove_pidfile(port: int) -> None:
    try:
        os.remove(_pidfile_path(port))
    except Exception:
        pass

def stop_port_external(port: int) -> bool:
    """Stop a browser started earlier (even by another process) using pidfile."""
    pid = _read_pidfile(port)
    if not pid:
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
        time.sleep(0.2)
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        _remove_pidfile(port)
    return True

def list_pidfile_ports() -> List[int]:
    """List ports that have pidfiles present (may include stale entries)."""
    try:
        import glob
        ports = []
        for path in glob.glob(PIDFILE_TEMPLATE.replace('{port}', '*')):
            try:
                ports.append(int(path.split('-')[-1].split('.pid')[0]))
            except Exception:
                continue
        return sorted(ports)
    except Exception:
        return []

class BrowserCDP:
    """Single Chrome instance controlled via CDP."""

    def __init__(self, port: int = DEFAULT_PORT, headless: bool = DEFAULT_HEADLESS):
        self.port = port
        self.headless = headless
        self.process: Optional[subprocess.Popen] = None

    # ────────── public api ──────────
    def start(self) -> str:
        if not _port_is_free(self.port):
            raise RuntimeError(f"Port {self.port} is busy.")

        chrome = _which_chrome()
        log.debug(f"Using Chrome at {chrome}")

        args = [
            chrome,
            f'--remote-debugging-port={self.port}',
            '--no-first-run',
            '--no-default-browser-check',
            '--user-data-dir=' + USER_DATA_DIR_TEMPLATE.format(port=self.port),
        ]
        if self.headless:
            args.append('--headless=new')

        log.info(f"Launching Chrome on port {self.port} (headless={self.headless})")
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid  # so we can kill entire pg
        )

        # wait for listening
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            if not _port_is_free(self.port):
                _write_pidfile(self.port, self.process.pid)
                log.info(f"Chrome ready at http://localhost:{self.port}")
                return f"http://localhost:{self.port}"
            if self.process.poll() is not None:
                raise RuntimeError("Chrome exited prematurely.")
            time.sleep(0.2)

        self.stop()
        raise TimeoutError(f"Chrome did not start within {STARTUP_TIMEOUT}s.")

    def stop(self):
        if not self.process:
            return
        log.info(f"Stopping Chrome on port {self.port}")
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(3)
        except subprocess.TimeoutExpired:
            log.warning("Graceful shutdown failed, killing…")
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
        finally:
            _remove_pidfile(self.port)
            self.process = None


class MultiBrowserManager:
    """Launch and track multiple BrowserCDP instances."""

    def __init__(self):
        self._browsers: Dict[int, BrowserCDP] = {}

    def start(self, port: Optional[int] = None, headless: bool = DEFAULT_HEADLESS) -> str:
        # Choose a port
        if port is None:
            port = find_free_port()
        elif not _port_is_free(port):
            if AUTO_REDIRECT_IF_BUSY:
                port = find_free_port(preferred=port)
            else:
                raise RuntimeError(f"Requested port {port} is busy")

        if port in self._browsers:
            raise RuntimeError(f"Browser already running on port {port}")

        browser = BrowserCDP(port, headless)
        url = browser.start()
        self._browsers[port] = browser
        return url

    def stop(self, port: int):
        browser = self._browsers.pop(port, None)
        if browser:
            browser.stop()

    def stop_all(self):
        for port in list(self._browsers):
            self.stop(port)

    # ────────── convenience helpers ──────────
    def running_ports(self) -> List[int]:
        return list(self._browsers)

    def is_running(self, port: int) -> bool:
        return port in self._browsers


# ─── quick demo & tool mode when executed as script ───
if __name__ == "__main__":
    import sys

    def _env(name: str, default: Optional[str] = None) -> Optional[str]:
        return os.getenv(name, default)

    def _env_bool(name: str, default: bool) -> bool:
        v = os.getenv(name)
        if v is None:
            return default
        return v.strip().lower() in {"1", "true", "yes", "y"}

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "start":
        # Env: CDP_PORT=auto|<int>, CDP_COUNT=N, CDP_HEADLESS=1/0, CDP_RANGE=9222-9400
        headless = _env_bool("CDP_HEADLESS", DEFAULT_HEADLESS)
        count = int(_env("CDP_COUNT", "1"))
        range_env = _env("CDP_RANGE")
        if range_env and '-' in range_env:
            try:
                a, b = range_env.split('-', 1)
                a, b = int(a), int(b)
                globals()["PORT_RANGE_START"], globals()["PORT_RANGE_END"] = a, b
            except Exception:
                pass

        urls: List[str] = []
        desired = _env("CDP_PORT", "auto")
        for i in range(count):
            if desired != "auto" and i == 0:
                try_port: Optional[int] = int(desired)
            else:
                try_port = None
            port = find_free_port(preferred=try_port)
            b = BrowserCDP(port=port, headless=headless)
            url = b.start()
            urls.append(url)
            log.info(f"STARTED {url}")
        # Print URLs and exit immediately; Chrome keeps running (no atexit)
        print("\n".join(urls))
        sys.exit(0)

    elif cmd == "stop":
        # Env: CDP_PORTS="9222,9223" (required)
        ports_csv = _env("CDP_PORTS", "")
        ports = [int(p) for p in ports_csv.split(',') if p.strip().isdigit()]
        if not ports:
            log.error("CDP_PORTS env required, e.g. '9222,9223'")
            sys.exit(1)
        for p in ports:
            ok = stop_port_external(p)
            log.info(f"STOP {p}: {'ok' if ok else 'not found'}")

    elif cmd == "stop-all":
        for p in list_pidfile_ports():
            stop_port_external(p)
            log.info(f"STOP {p}: ok")

    elif cmd == "list":
        ports = list_pidfile_ports()
        print(" ".join(map(str, ports)))

    else:
        print(
            "Usage:\n"
            "  python browser.py start    # start one or more browsers (env: CDP_PORT, CDP_COUNT, CDP_HEADLESS, CDP_RANGE)\n"
            "  python browser.py list     # list ports started by this tool\n"
            "  python browser.py stop     # stop specific ports (eg: CDP_PORTS=9222,9223 python browser.py stop)\n"
            "  python browser.py stop-all # stop all started by this tool\n"
        )