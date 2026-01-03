"""
Claude Web Agent - Simple web automation for Claude Code

Install:
    pip install claude-web-agent
    # or from source:
    pip install -e .

Usage:
    # Start browser
    web-agent start

    # Use in Python
    from claude_web_agent import WebTool
    web = WebTool(port=9222)
    web.connect()
    web.go("https://example.com")
    web.close()
"""

__version__ = "0.1.0"

from .web_tool import WebTool
from .browser import BrowserCDP, MultiBrowserManager

__all__ = ["WebTool", "BrowserCDP", "MultiBrowserManager"]
