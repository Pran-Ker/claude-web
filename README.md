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

Enjoy seamless, powerful web automation with minimal setup.