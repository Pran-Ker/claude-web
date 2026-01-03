#!/usr/bin/env python3
"""
CLI tool for Claude Web Agent - Makes web automation super simple
"""

import sys
import os
import json
import click
from .browser import BrowserCDP, MultiBrowserManager, find_free_port, stop_port_external, list_pidfile_ports
from .web_tool import WebTool


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Claude Web Agent - Simple web automation for Claude Code"""
    pass


@cli.command()
@click.option('--port', '-p', type=int, help='Port number (auto-selects if not specified)')
@click.option('--headless/--no-headless', default=True, help='Run in headless mode')
@click.option('--count', '-c', type=int, default=1, help='Number of browsers to start')
def start(port, headless, count):
    """Start Chrome browser(s) with CDP enabled"""
    manager = MultiBrowserManager()
    ports = []

    for i in range(count):
        try:
            if i == 0 and port:
                use_port = port
            else:
                use_port = None

            url = manager.start(port=use_port, headless=headless)
            actual_port = int(url.split(':')[-1])
            ports.append(actual_port)
            click.echo(f"✓ Browser started on port {actual_port}")
        except Exception as e:
            click.echo(f"✗ Failed to start browser: {e}", err=True)
            sys.exit(1)

    click.echo(f"\n🌐 Browser{'s' if count > 1 else ''} ready!")
    click.echo(f"Ports: {', '.join(map(str, ports))}")
    click.echo(f"\nConnect with: WebTool(port={ports[0]})")


@cli.command()
@click.argument('ports', nargs=-1, type=int)
def stop(ports):
    """Stop browser(s) by port number"""
    if not ports:
        click.echo("Error: Specify port number(s) to stop", err=True)
        click.echo("Usage: web-agent stop 9222 9223")
        sys.exit(1)

    for port in ports:
        if stop_port_external(port):
            click.echo(f"✓ Stopped browser on port {port}")
        else:
            click.echo(f"✗ No browser found on port {port}", err=True)


@cli.command('stop-all')
def stop_all():
    """Stop all running browsers"""
    ports = list_pidfile_ports()
    if not ports:
        click.echo("No browsers running")
        return

    for port in ports:
        stop_port_external(port)
        click.echo(f"✓ Stopped browser on port {port}")

    click.echo(f"\n✓ Stopped {len(ports)} browser{'s' if len(ports) > 1 else ''}")


@cli.command()
def list():
    """List all running browsers"""
    ports = list_pidfile_ports()
    if not ports:
        click.echo("No browsers running")
        return

    click.echo("Running browsers:")
    for port in ports:
        click.echo(f"  • Port {port}: http://localhost:{port}")


@cli.command()
@click.argument('url')
@click.option('--port', '-p', type=int, default=9222, help='Browser port')
@click.option('--screenshot', '-s', help='Save screenshot to file')
def navigate(url, port, screenshot):
    """Navigate to a URL and optionally take screenshot"""
    try:
        web = WebTool(port=port)
        web.connect()
        click.echo(f"Navigating to {url}...")
        web.go(url)

        if screenshot:
            web.screenshot(screenshot)
            click.echo(f"✓ Screenshot saved to {screenshot}")
        else:
            click.echo("✓ Navigation complete")

        web.close()
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('code')
@click.option('--port', '-p', type=int, default=9222, help='Browser port')
def js(code, port):
    """Execute JavaScript in the browser"""
    try:
        web = WebTool(port=port)
        web.connect()
        result = web.js(code)
        if result is not None:
            click.echo(result)
        web.close()
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('selector')
@click.option('--port', '-p', type=int, default=9222, help='Browser port')
def click_element(selector, port):
    """Click an element by CSS selector"""
    try:
        web = WebTool(port=port)
        web.connect()
        success = web.click(selector)
        if success:
            click.echo(f"✓ Clicked {selector}")
        else:
            click.echo(f"✗ Failed to click {selector}", err=True)
        web.close()
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('selector')
@click.argument('text')
@click.option('--port', '-p', type=int, default=9222, help='Browser port')
def fill(selector, text, port):
    """Fill an input field with text"""
    try:
        web = WebTool(port=port)
        web.connect()
        web.fill(selector, text)
        click.echo(f"✓ Filled {selector} with: {text}")
        web.close()
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
def setup():
    """Set up Claude Code as a web agent (creates CLAUDE.md if missing)"""
    claude_md_path = "CLAUDE.md"

    if os.path.exists(claude_md_path):
        click.echo(f"✓ {claude_md_path} already exists")
        if not click.confirm("Overwrite?", default=False):
            return

    # Basic web agent instructions
    content = """# Web Agent Setup

This project is configured as a Claude Code Web Agent.

## Quick Start

```bash
# Start browser
web-agent start

# Stop browser
web-agent stop 9222

# Navigate and screenshot
web-agent navigate https://example.com -s screenshot.png
```

## Python Usage

```python
from claude_web_agent import WebTool

web = WebTool(port=9222)
web.connect()
web.go("https://example.com")
web.fill("input[name='search']", "hello")
web.screenshot("result.png")
web.close()
```

See full documentation at: https://github.com/agi-inc/claude-web
"""

    with open(claude_md_path, 'w') as f:
        f.write(content)

    click.echo(f"✓ Created {claude_md_path}")
    click.echo("\n🎉 Web agent setup complete!")
    click.echo("   You can now use Claude Code as a web agent.")


@cli.command()
def demo():
    """Run a simple demo (Google search)"""
    click.echo("Starting demo: Google search...")

    try:
        # Start browser
        manager = MultiBrowserManager()
        url = manager.start(headless=False)
        port = int(url.split(':')[-1])
        click.echo(f"✓ Browser started on port {port}")

        # Connect and run demo
        web = WebTool(port=port)
        web.connect()

        click.echo("Navigating to Google...")
        web.go("https://google.com")

        click.echo("Searching for 'Claude AI'...")
        web.fill("textarea[name='q']", "Claude AI")
        web.key("Enter")

        import time
        time.sleep(2)

        click.echo("✓ Demo complete! Check your browser.")
        click.echo(f"\nTo stop: web-agent stop {port}")

        web.close()

    except Exception as e:
        click.echo(f"✗ Demo failed: {e}", err=True)
        sys.exit(1)


def main():
    """Entry point for the CLI"""
    cli()


if __name__ == '__main__':
    main()
