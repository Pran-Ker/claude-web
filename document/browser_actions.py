from web_tool import WebTool


class BrowserActions:
    def __init__(self):
        self.web = WebTool(port=9222)
        self.web.connect()
    
    def page_text(self):
        """Get all page text for analysis"""
        return self.web.js("document.body.innerText")
    
    def page_info(self):
        """Get basic page info"""
        return {
            'title': self.web.js("document.title"),
            'url': self.web.js("window.location.href"),
            'ready': self.web.js("document.readyState === 'complete'")
        }
    
    def has_text(self, text):
        return text.lower() in self.page_text().lower()
    
    def find_buttons(self):
        return self.web.js("Array.from(document.querySelectorAll('button, input[type=submit], a[href]')).map(el => el.textContent || el.value || el.href).filter(t => t.trim()).slice(0, 10)")
    
    def find_inputs(self):
        return self.web.js("Array.from(document.querySelectorAll('input, textarea')).map(el => ({name: el.name || el.id, type: el.type, placeholder: el.placeholder})).slice(0, 10)")
    
    # Core actions from web_tool
    def go(self, url):
        return self.web.go(url)
    
    def click(self, selector):
        return self.web.click(selector)
    
    def fill(self, selector, text):
        return self.web.fill(selector, text)
    
    def key(self, key):
        return self.web.key(key)
    
    def screenshot(self, filename=None):
        return self.web.screenshot(filename)
    
    def wait(self, selector, timeout=10):
        return self.web.wait(selector, timeout)
    
    def close(self):
        return self.web.close()