# Claude Web Agent 🌐

**Convert any Claude Code instance into a web agent with ONE command.**

Connect Claude Code to web automation tools and browsers. Ask it to do whatever you want, and it will behave like a web agent better than most out there.

![Minesweeper Demo](assets/images/Minesweeper-demo.gif)

---

## ⚡ Installation (< 30 seconds)

### The Simplest Way
```bash
pip install git+https://github.com/agi-inc/claude-web.git
```

That's it! You're now a web agent. 🎉

### Alternative: From Source
```bash
git clone https://github.com/agi-inc/claude-web.git
cd claude-web
pip install -e tools/
```

---

## 🚀 Quick Start

### 1. Start a browser
```bash
web-agent start
```

### 2. Automate anything
Ask Claude Code:
```
"Navigate to https://example.com and fill out the contact form"
```

Claude automatically uses the web agent! ✨

---

## 📖 Usage

### CLI Commands

```bash
# Start browser
web-agent start                                    # Headless mode
web-agent start --no-headless                      # With GUI
web-agent start --count 3                          # Multiple browsers

# Navigate & interact
web-agent navigate https://google.com              # Go to URL
web-agent navigate https://example.com -s shot.png # With screenshot
web-agent fill "input[name='email']" "test@x.com"  # Fill form
web-agent click-element "button.submit"            # Click element
web-agent js "document.title"                      # Run JavaScript

# Manage browsers
web-agent list                                     # Show running browsers
web-agent stop 9222                                # Stop specific port
web-agent stop-all                                 # Stop all browsers

# Demo
web-agent demo                                     # See it in action!
```

### Python API

```python
from claude_web_agent import WebTool

web = WebTool(port=9222)
web.connect()
web.go("https://example.com")
web.fill("input[name='email']", "test@example.com")
web.click("button[type='submit']")
web.screenshot("result.png")
web.close()
```

### One-Liner Examples

```bash
# Quick screenshot
python3 -c "from claude_web_agent import WebTool; w=WebTool(); w.connect(); w.go('https://google.com'); w.screenshot('google.png'); w.close()"

# Get page title
python3 -c "from claude_web_agent import WebTool; w=WebTool(); w.connect(); w.go('https://github.com'); print(w.js('document.title')); w.close()"
```

---

## 🎯 Features

- **One Command Install**: No complex setup
- **Zero Config**: Works immediately
- **CLI + Python API**: Use however you prefer
- **Multi-browser Support**: Run multiple instances
- **Headless by Default**: Optimized for automation
- **Chrome DevTools Protocol**: Fast and reliable
- **Minimal Dependencies**: Only 3 core packages needed

---

## 🤝 For Claude Code Users

After installation, just ask Claude:

- "Search Google for latest news"
- "Fill out the form on example.com"
- "Take a screenshot of this website"
- "Click the login button and submit"

Claude will automatically handle browser control!

---

## 📁 Project Structure

```
claude-web/
├── README.md                    # You are here
├── INSTALL.md                   # Detailed installation guide
├── CLAUDE.md                    # Advanced automation patterns
├── tools/
│   ├── __init__.py             # Package entry point
│   ├── browser.py              # Chrome CDP launcher
│   ├── web_tool.py             # Core automation library
│   ├── cli.py                  # CLI interface
│   ├── pyproject.toml          # Package configuration
│   └── tools.json              # Tool definitions
```

---

## 📦 Dependencies

**Core (required - auto-installed):**
- `click` - CLI interface
- `requests` - HTTP client
- `websocket-client` - CDP connection

**Optional (for advanced features):**
```bash
pip install "claude-web-agent[full]"
```

---

## 📚 Documentation

- **[INSTALL.md](INSTALL.md)** - Detailed installation guide
- **[CLAUDE.md](CLAUDE.md)** - Advanced usage patterns and best practices

---

## 🔧 Advanced Usage

### Old Way (still works)
```bash
CDP_HEADLESS=1 python tools/browser.py start
```

### New Way (recommended)
```bash
web-agent start
```

Both work! The new CLI is just simpler. 😊

---

## 🎁 Bonus Features

- Auto port selection (no conflicts)
- Multi-browser management
- Built-in demo mode
- Clean shutdown handling
- Screenshot optimization
- JavaScript execution
- Form automation
- Element interaction

---

## 🤝 Contributing

Contributions welcome! This is part of the Claude Web project.

---

## 📄 License

MIT License - Use freely!

---

**Made for Claude Code** | **Powered by Chrome DevTools Protocol** | **Simple. Fast. Reliable.**
