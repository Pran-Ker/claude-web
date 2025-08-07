# Claude Code as a Web Agent

Connect your cluade code to web action tools, and browsers. Ask it to do what ever you want, and it will be behave like web agent which is better than most out there.

![Minesweeper Demo](Minesweeper-demo.gif)

---
## How to use:

Just ask Claude Code, to set it up. 

```bash
python browser.py&  #Spin up a browser
```

On another tab, ask Claude Code to go wild on the port 9222.

---
## Installation  (⏱ < 1 min)

```bash
# 1. Grab the code
git clone https://github.com/agi-inc/claude-web.git
cd claude-web

# 2. (Optional) isolate deps
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\activate

# 3. Install Python + Playwright deps
pip install -r requirements.txt
playwright install    # fetches browser binaries
```
---


### Search for tasks:

```bash
echo "task-id" | python RealEval/tasks.py
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
├── RealEval/
│   ├── tasks.py                 # Task management
│   └── evaluate_task.py         # Task verification and evaluation
├── screenshots/                 # Debugging visuals
└── caching/                     # Site-specific data caching
```
