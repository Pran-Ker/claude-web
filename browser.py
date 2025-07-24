import subprocess
import time
import socket
import signal
import atexit
from typing import Optional, List, Dict


class BrowserCDP:
    def __init__(self, port: int = 9222, headless: bool = True):
        self.port = port
        self.headless = headless
        self.process = None
        # Register cleanup on exit
        atexit.register(self.stop)
        
    def _is_port_available(self, port: int) -> bool:
        """Check if port is available"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) != 0
    
    def _signal_handler(self, signum, frame):
        """Handle system signals for graceful shutdown"""
        print(f"Received signal {signum}, shutting down browser...")
        self.stop()
    
    def start(self) -> str:
        """Start Chrome with CDP and return the CDP URL"""
        if not self._is_port_available(self.port):
            raise Exception(f"Port {self.port} is already in use")
        
        chrome_args = [
            '--remote-debugging-port=%d' % self.port,
            '--remote-allow-origins=*',
            '--disable-web-security',
            '--disable-features=VizDisplayCompositor',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-background-timer-throttling',
            '--disable-background-networking',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-features=TranslateUI',
            '--disable-ipc-flooding-protection',
            '--enable-features=NetworkService,NetworkServiceLogging',
            f'--user-data-dir=/tmp/chrome-cdp-session-{self.port}'
        ]
        
        if self.headless:
            chrome_args.append('--headless')
        
        # Try different Chrome executable paths
        chrome_paths = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/usr/bin/google-chrome',
            '/usr/bin/chromium-browser',
            'google-chrome',
            'chromium'
        ]
        
        chrome_executable = None
        for path in chrome_paths:
            try:
                subprocess.run([path, '--version'], capture_output=True, check=True)
                chrome_executable = path
                break
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
        
        if not chrome_executable:
            raise Exception("Chrome/Chromium not found")
        
        # Start Chrome
        cmd = [chrome_executable] + chrome_args
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Wait for Chrome to start
        max_retries = 30
        for _ in range(max_retries):
            if not self._is_port_available(self.port):
                break
            time.sleep(0.5)
        else:
            self.stop()
            raise Exception(f"Chrome failed to start on port {self.port}")
        
        return f"http://localhost:{self.port}"
    
    def stop(self):
        """Stop the browser process gracefully"""
        if self.process:
            try:
                # Try graceful shutdown first
                self.process.terminate()
                print(f"Gracefully shutting down browser on port {self.port}...")
                self.process.wait(timeout=3)
                print("Browser stopped gracefully")
            except subprocess.TimeoutExpired:
                print("Browser didn't stop gracefully, force killing...")
                self.process.kill()
                try:
                    self.process.wait(timeout=2)
                    print("Browser force killed")
                except subprocess.TimeoutExpired:
                    print("Warning: Browser process may still be running")
            except Exception as e:
                print(f"Error stopping browser: {e}")
            finally:
                self.process = None
    
    def __enter__(self):
        return self.start()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


class MultiBrowserManager:
    """Manage multiple browser instances on different ports"""
    
    def __init__(self):
        self.browsers: Dict[int, BrowserCDP] = {}
        atexit.register(self.stop_all)
    
    def start_browser(self, port: int, headless: bool = True) -> str:
        """Start a browser on the specified port"""
        if port in self.browsers:
            raise Exception(f"Browser already running on port {port}")
        
        browser = BrowserCDP(port=port, headless=headless)
        cdp_url = browser.start()
        self.browsers[port] = browser
        print(f"Started browser on port {port}")
        return cdp_url
    
    def stop_browser(self, port: int):
        """Stop browser on specified port"""
        if port in self.browsers:
            self.browsers[port].stop()
            del self.browsers[port]
            print(f"Stopped browser on port {port}")
    
    def stop_all(self):
        """Stop all managed browsers"""
        for port in list(self.browsers.keys()):
            self.stop_browser(port)
    
    def get_running_ports(self) -> List[int]:
        """Get list of ports with running browsers"""
        return list(self.browsers.keys())
    
    def is_running(self, port: int) -> bool:
        """Check if browser is running on port"""
        return port in self.browsers


# Usage examples
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "multi":
        # Multi-browser example
        print("Starting multiple browsers...")
        manager = MultiBrowserManager()
        
        # Start browsers on different ports
        ports = [9222, 9223, 9224]
        for port in ports:
            try:
                cdp_url = manager.start_browser(port, headless=False)
                print(f"Browser {port}: {cdp_url}")
            except Exception as e:
                print(f"Failed to start browser on port {port}: {e}")
        
        print(f"Running browsers on ports: {manager.get_running_ports()}")
        
        try:
            import os
            if os.isatty(0):
                input("Press Enter to stop all browsers...")
            else:
                import threading
                stop_event = threading.Event()
                stop_event.wait()
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            manager.stop_all()
    
    elif len(sys.argv) > 1 and sys.argv[1] == "free":
        # Start browsers only on free ports
        print("Starting browsers on available ports...")
        
        def is_port_free(port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                return s.connect_ex(('localhost', port)) != 0
        
        ports_to_try = [9222, 9223, 9224, 9225]
        started_browsers = []
        
        for port in ports_to_try:
            if is_port_free(port):
                try:
                    browser = BrowserCDP(port=port, headless=False)
                    browser.start()
                    started_browsers.append(browser)
                    print(f'Started browser on port {port}')
                except Exception as e:
                    print(f'Failed to start on port {port}: {e}')
            else:
                print(f'Port {port} already in use, skipping')
        
        if started_browsers:
            print(f'Started {len(started_browsers)} browsers')
            try:
                import os
                if os.isatty(0):
                    input("Press Enter to stop all browsers...")
                else:
                    import threading
                    stop_event = threading.Event()
                    stop_event.wait()
            except (EOFError, KeyboardInterrupt):
                pass
            finally:
                for browser in started_browsers:
                    browser.stop()
        else:
            print("No free ports available")
    
    else:
        # Single browser example (original behavior)
        browser = BrowserCDP(port=9222, headless=False)
        cdp_url = browser.start()
        print(f"Browser started with CDP at: {cdp_url}")
        print(f"Use this URL in another Python file to connect via CDP")
        print("Run with 'python browser.py multi' for multi-browser example")
        print("Run with 'python browser.py free' to start browsers on available ports")
        
        try:
            import os
            if os.isatty(0):
                input("Press Enter to stop the browser...")
            else:
                import threading
                stop_event = threading.Event()
                stop_event.wait()
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            browser.stop()