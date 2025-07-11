import json
import requests
import websocket
import time
import base64


class WebTool:
    def __init__(self, port=9222):
        self.port = port
        self.ws = None
        self.msg_id = 0
        
    def connect(self):
        """Connect to first tab"""
        tabs = requests.get(f"http://localhost:{self.port}/json").json()
        self.ws = websocket.create_connection(tabs[0]['webSocketDebuggerUrl'])
        self.cmd("Page.enable")
        self.cmd("DOM.enable")
        self.cmd("Runtime.enable")
        
    def cmd(self, method, params=None):
        """Send command"""
        self.msg_id += 1
        msg = {"id": self.msg_id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))
        
        while True:
            response = json.loads(self.ws.recv())
            if response.get("id") == self.msg_id:
                return response
    
    def go(self, url):
        """Go to URL"""
        self.cmd("Page.navigate", {"url": url})
        time.sleep(2)  # Wait for page load
        
    def click(self, selector):
        """Click element"""
        doc = self.cmd("DOM.getDocument")
        root = doc["result"]["root"]["nodeId"]
        element = self.cmd("DOM.querySelector", {"nodeId": root, "selector": selector})
        
        if not element["result"]["nodeId"]:
            return False
            
        node_id = element["result"]["nodeId"]
        box = self.cmd("DOM.getBoxModel", {"nodeId": node_id})
        
        if "error" in box:
            return False
            
        content = box["result"]["model"]["content"]
        x = (content[0] + content[4]) / 2
        y = (content[1] + content[5]) / 2
        
        self.cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        self.cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        return True
        
    def type(self, text):
        """Type text"""
        for char in text:
            self.cmd("Input.dispatchKeyEvent", {"type": "char", "text": char})
            
    def fill(self, selector, text):
        """Fill input"""
        self.click(selector)
        self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})  # Select all
        self.type(text)
        
    def key(self, key):
        """Press key"""
        self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
        
    def js(self, code):
        """Run JavaScript"""
        result = self.cmd("Runtime.evaluate", {"expression": code})
        return result["result"]["result"].get("value", None)
        
    def wait(self, selector, timeout=10):
        """Wait for element"""
        start = time.time()
        while time.time() - start < timeout:
            doc = self.cmd("DOM.getDocument")
            root = doc["result"]["root"]["nodeId"]
            element = self.cmd("DOM.querySelector", {"nodeId": root, "selector": selector})
            if element["result"]["nodeId"]:
                return True
            time.sleep(0.5)
        return False
        
    def screenshot(self, filename=None):
        """Take screenshot"""
        result = self.cmd("Page.captureScreenshot")
        data = result["result"]["data"]
        
        if filename:
            with open(filename, "wb") as f:
                f.write(base64.b64decode(data))
            return filename
        return data
        
    def text(self, selector):
        """Get element text"""
        code = f"document.querySelector('{selector}').textContent"
        return self.js(code)
        
    def attr(self, selector, attribute):
        """Get element attribute"""
        code = f"document.querySelector('{selector}').getAttribute('{attribute}')"
        return self.js(code)
        
    def close(self):
        """Close connection"""
        if self.ws:
            self.ws.close()


# Simple usage
if __name__ == "__main__":
    web = WebTool()
    web.connect()
    
    web.go("https://google.com")
    web.fill("input[name='q']", "hello world")
    web.key("Enter")
    
    time.sleep(3)
    web.screenshot("result.png")
    
    web.close()