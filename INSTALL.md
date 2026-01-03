# Installation Guide

## The Simplest Way (One Command)

Install directly from GitHub:

```bash
pip install git+https://github.com/agi-inc/claude-web.git
```

That's it! You're done. 🎉

## Verify Installation

```bash
# Check if installed
web-agent --version

# Run demo
web-agent demo
```

## For Claude Code Users

### Step 1: Install
```bash
pip install git+https://github.com/agi-inc/claude-web.git
```

### Step 2: Tell Claude
In any Claude Code session, simply say:
```
"Install the web agent and start a browser"
```

Or even simpler:
```
"pip install git+https://github.com/agi-inc/claude-web.git && web-agent start"
```

### Step 3: Start Automating
```
"Navigate to https://example.com and take a screenshot"
```

## Alternative Installation Methods

### Using uv (faster)
```bash
uv pip install git+https://github.com/agi-inc/claude-web.git
```

### From source (for development)
```bash
git clone https://github.com/agi-inc/claude-web.git
cd claude-web
pip install -e tools/
```

### With full dependencies
```bash
pip install "git+https://github.com/agi-inc/claude-web.git#egg=claude-web-agent[full]"
```

## What Gets Installed

- `web-agent` CLI command
- `claude-web` CLI command (alias)
- Python package `claude_web_agent`
  - `WebTool` - Browser automation class
  - `BrowserCDP` - Browser launcher
  - `MultiBrowserManager` - Multi-browser management

## Quick Test

After installation:

```bash
# Start a browser
web-agent start

# Should print:
# ✓ Browser started on port 9222
# 🌐 Browser ready!
```

## Troubleshooting

### Command not found
If `web-agent` command is not found, your Python scripts directory may not be in PATH.

**Fix:**
```bash
# Find where pip installed it
pip show claude-web-agent

# Add to PATH (in ~/.bashrc or ~/.zshrc)
export PATH="$HOME/.local/bin:$PATH"
```

### Chrome not found
The package needs Chrome or Chromium installed.

**Install Chrome:**
- **Ubuntu/Debian:** `sudo apt install google-chrome-stable`
- **macOS:** Download from google.com/chrome
- **Other:** The tool will use any of: google-chrome, chromium-browser, chromium

### Port already in use
If port 9222 is busy, the tool auto-selects the next free port.

Or specify a different port:
```bash
web-agent start --port 9223
```

## Uninstall

```bash
pip uninstall claude-web-agent
```

## Need Help?

- Check [README.md](README.md) for usage examples
- See [CLAUDE.md](CLAUDE.md) for Claude Code patterns
- Open an issue on GitHub

---

**Questions?** Just ask Claude: "Help me set up as a web agent"
