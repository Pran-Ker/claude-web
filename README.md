# Claude Code as a Web Agent

Connect your cluade code to web action tools, and browsers. Ask it to do what ever you want, and it will be behave like web agent which is better than most out there.

![Minesweeper Demo](assets/images/Minesweeper-demo.gif)

---
## How to use:

Just ask Claude Code, to set it up. 

Or 

```bash
CDP_HEADLESS=1 python tools/browser.py start  #Spin up a browser
```
and then run Claude Code on the Port

---
## Installation  (⏱ < 1 min)

```bash
# 1. Grab the code
git clone https://github.com/agi-inc/claude-web.git
cd claude-web

# 2. (Optional) isolate deps
python -m venv .venv && source .venv/bin/activate   

# 3. Install Python + Playwright deps
cd tools && uv sync && cd ..
playwright install    # fetches browser binaries
```

## Project Structure

```
claude-web/
├── README.md                    
├── CLAUDE.md                    # Detailed automation instructions
├── tools/
    ├── browser.py               # Chrome browser management with CDP
    ├── pyproject.toml           # Dependencies (uv format)
    ├── tools.json               # Tool definitions
    └── web_tool.py              # Core automation library
```
