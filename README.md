# Web Agent Automation Tool

A powerful Python-based web automation framework leveraging Chrome DevTools Protocol (CDP) to automate web browsing, perform tasks, clone websites, and test web applications.

---

## Features
- **Automate Web Tasks:**
  - Navigate websites, interact with elements, fill forms, and execute custom JavaScript.
  - Automate routine web interactions such as clicking buttons, typing into inputs, and validating actions.
- **Website Cloning:**
  - Generate pixel-perfect clones of existing websites.
  - Automatically download and host all website assets locally.
  - Preserve responsive design for accurate replication.
- **Task Management and Verification:**
  - Execute complex multi-step tasks with built-in verification.
  - Capture screenshots for debugging and visual verification.
- **Parallel Task Execution:**
  - Support for multiple agents running concurrently.
  - Manage several automation tasks simultaneously for efficient testing and scraping.

---

## Prerequisites
- Python 3.x
- Chrome Browser
- Chrome running with remote debugging enabled
- Playwright (for browser.py automation): `pip install playwright`

### Setup Chrome with Debugging

**macOS/Linux:**

```bash
google-chrome --remote-debugging-port=9222 --no-first-run --no-default-browser-check --user-data-dir=/tmp/chrome-debug
```

**Windows:**

```bash
chrome.exe --remote-debugging-port=9222 --no-first-run --no-default-browser-check --user-data-dir=%TEMP%\chrome-debug
```

---

## Installation

Install Python dependencies:

```bash
pip install requests websocket-client
```

---

## Usage Examples

### Basic Automation

```python
from tools.web_tool import WebTool

web = WebTool(port=9222)
web.connect()

web.go('https://example.com')
web.screenshot('./screenshots/example.png')
web.click('button')
web.fill('input[name="email"]', 'user@example.com')

title = web.js('document.title')
print(title)

web.close()
```

### Command Line Usage

**Navigate:**

```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.go('https://example.com'); web.close()"
```

**Screenshot:**

```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.screenshot('./screenshots/debug.png'); web.close()"
```

---

## Advanced Features

### Browser Management (browser.py)

The `browser.py` module provides automated Chrome browser management with CDP support:

**Single Browser Instance:**
```python
from browser import BrowserCDP

# Start a single browser instance
browser = BrowserCDP(port=9222, headless=False)
cdp_url = browser.start()
print(f"Browser available at: {cdp_url}")

# Use context manager for automatic cleanup
with BrowserCDP(port=9222) as cdp_url:
    # Browser automatically starts and stops
    pass
```

**Multiple Browser Management:**
```python
from browser import MultiBrowserManager

manager = MultiBrowserManager()

# Start browsers on different ports
manager.start_browser(9222, headless=False)
manager.start_browser(9223, headless=True)

# Check running browsers
ports = manager.get_running_ports()  # [9222, 9223]

# Stop specific browser
manager.stop_browser(9222)

# Stop all browsers
manager.stop_all()
```

**Command Line Usage:**
```bash
# Start single browser
python browser.py

# Start multiple browsers
python browser.py multi

# Start browsers on available ports only
python browser.py free
```


### Automated Task Execution

Run and verify complex tasks automatically:

```bash
python3 example/evaluate_task.py TASK_ID "EXPECTED_ANSWER"
```

Search for tasks:

```bash
echo "task-id" | python example/tasks.py
```

### Website Cloning

Clone websites precisely:

```bash
python3 clone/website_cloner.py
```

---

## Project Structure

```
web-agent-automation/
├── README.md                    # This guide
├── CLAUDE.md                    # Detailed automation instructions
├── browser.py                   # Chrome browser management with CDP
├── tools/
│   └── web_tool.py              # Core automation library
├── clone/
│   ├── CLONING_PROTOCOL.md      # Website cloning instructions
│   └── VIDEO_WEBSITE_GUIDE.md   # Special cloning guide for video sites
├── docs/                        # Debugging and advanced guides
├── example/
│   ├── tasks.py                 # Task management
│   └── evaluate_task.py         # Task verification and evaluation
├── screenshots/                 # Debugging visuals
└── caching/                     # Site-specific data caching
```

---

## Use Cases
- **Automated Web Testing:** Efficiently validate website functionality.
- **Web Scraping:** Automate data extraction.
- **Website Cloning:** Quickly replicate sites for local hosting and testing.
- **Concurrent Task Handling:** Manage complex workflows with multiple agents running parallel tasks.

---