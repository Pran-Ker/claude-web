import subprocess
import time
import socket
import signal
import atexit
from typing import Optional


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
            '--user-data-dir=/tmp/chrome-cdp-session'
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


# Simple usage example
if __name__ == "__main__":
    # Start browser and get CDP URL
    browser = BrowserCDP(port=9222, headless=False)
    cdp_url = browser.start()
    print(f"Browser started with CDP at: {cdp_url}")
    print(f"Use this URL in another Python file to connect via CDP")
    
    try:
        input("Press Enter to stop the browser...")
    finally:
        browser.stop()