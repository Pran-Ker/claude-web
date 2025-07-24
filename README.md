# Web Agent Automation Tool

A Python-based web automation framework using Chrome DevTools Protocol for browser control and task automation.

## Overview

This tool provides automated web browser control for testing websites, performing tasks, and creating website clones. It connects to Chrome via DevTools Protocol to perform actions like clicking, typing, navigation, and screenshot capture.

## Prerequisites

- Python 3.x
- Chrome browser
- Chrome running with remote debugging enabled

## Setup

1. **Start Chrome with remote debugging:**
   ```bash
   # macOS/Linux
   google-chrome --remote-debugging-port=9222 --no-first-run --no-default-browser-check --user-data-dir=/tmp/chrome-debug

   # Windows
   chrome.exe --remote-debugging-port=9222 --no-first-run --no-default-browser-check --user-data-dir=%TEMP%\chrome-debug
   ```

2. **Install dependencies:**
   ```bash
   pip install requests websocket-client
   ```

## Core Features

### Web Actions
- **Navigate** to URLs
- **Click** elements using CSS selectors
- **Type** text into form fields
- **Take screenshots** for debugging
- **Execute JavaScript** for complex interactions
- **Coordinate clicking** as fallback method

### Task Management
- Automated task execution with verification
- Screenshot-based debugging
- Form interaction protocols
- Success/failure detection

### Website Cloning
- Create pixel-perfect website replicas
- Download all assets locally
- Maintain responsive design

## Basic Usage

### Quick Start
```python
from tools.web_tool import WebTool

# Connect to browser
web = WebTool(port=9222)
web.connect()

# Navigate to a website
web.go('https://example.com')

# Take a screenshot
web.screenshot('./screenshots/example.png')

# Click an element
web.click('button')

# Type in a form field
web.fill('input[name="email"]', 'user@example.com')

# Execute JavaScript
result = web.js('document.title')
print(result)

# Close connection
web.close()
```

### Command Line Usage
```bash
# Navigate to a page
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.go('https://example.com'); web.close()"

# Take a screenshot
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.screenshot('./screenshots/debug.png'); web.close()"

# Click an element
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.click('button'); web.close()"
```

## Project Structure

```
claude-web/
├── README.md                    # This file
├── CLAUDE.md                   # Detailed automation guide
├── tools/
│   └── web_tool.py            # Core WebTool class
├── clone/
│   ├── CLONING_PROTOCOL.md    # Website cloning guide
│   └── VIDEO_WEBSITE_GUIDE.md # Video website creation
├── docs/                      # Advanced debugging guides
├── example/
│   ├── tasks.py              # Task management
│   └── evaluate_task.py      # Task evaluation
├── screenshots/              # Debug screenshots
└── caching/                 # Website-specific notes
```

## Advanced Features

### Task Automation
Execute complex multi-step tasks with automatic verification:
```bash
# Run task evaluation
python3 example/evaluate_task.py TASK_ID "ANSWER"

# Search tasks by ID
echo "task-id" | python example/tasks.py
```

### Website Cloning
Create perfect replicas of websites:
```bash
python3 clone/website_cloner.py
```
