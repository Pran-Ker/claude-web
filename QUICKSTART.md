# Quick Start - Claude Web Agent

## Install (One Command)

```bash
pip install git+https://github.com/agi-inc/claude-web.git
```

## Use (Three Commands)

```bash
# 1. Start browser
web-agent start

# 2. Tell Claude
# "Navigate to https://example.com and take a screenshot"

# 3. Done! 🎉
```

## That's It!

You've just converted Claude Code into a web agent.

---

## Full Example

```bash
# Install
pip install git+https://github.com/agi-inc/claude-web.git

# Start browser
web-agent start

# Now ask Claude anything:
# "Search Google for 'Claude AI'"
# "Fill out the contact form on example.com"
# "Take a screenshot of news.ycombinator.com"
# "Click the login button and enter credentials"
```

---

## Available Commands

```bash
web-agent start              # Start browser
web-agent list               # Show running browsers
web-agent stop 9222          # Stop specific browser
web-agent stop-all           # Stop all browsers
web-agent demo               # See it in action
web-agent --help             # Show all commands
```

---

## Python Usage

```python
from tools import WebTool

web = WebTool(port=9222)
web.connect()
web.go("https://google.com")
web.fill("textarea[name='q']", "Claude AI")
web.key("Enter")
web.screenshot("result.png")
web.close()
```

---

## Need More?

- [README.md](README.md) - Full documentation
- [INSTALL.md](INSTALL.md) - Detailed installation guide
- [CLAUDE.md](CLAUDE.md) - Advanced patterns for Claude Code

---

**Questions?** Just ask Claude: *"Help me use the web agent"*
