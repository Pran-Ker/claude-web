# Web Agent Toolkit Guide

This guide provides essential tools and strategies for automated web task completion using browser automation.

## Core Tools Available

### 1. Browser Management (`browser.py`)
```python
from browser import BrowserCDP
browser = BrowserCDP(port=9222, headless=False)
browser.start()  # Starts Chrome with CDP enabled
```

### 2. Web Actions (`web_tool.py`)
```python
from web_tool import WebTool
web = WebTool(port=9222)
web.connect()

# Core actions
web.go(url)                    # Navigate to URL
web.click(selector)            # Click element
web.fill(selector, text)       # Fill input field
web.type(text)                 # Type text
web.key(key)                   # Press key (Enter, Tab, etc.)
web.js(code)                   # Execute JavaScript
web.screenshot(filename)       # Take screenshot
web.close()                    # Close connection
```

### 3. Page Inspector (`browser_actions.py`)
```python
from browser_actions import BrowserActions
browser = BrowserActions()  # Auto-connects to port 9222

# Information gathering
browser.page_info()            # Get title, URL, ready state
browser.page_text()            # Get all page text
browser.has_text(text)         # Check if page contains text
browser.find_buttons()         # Find clickable elements
browser.find_inputs()          # Find input fields
```

### 4. Site Crawler (`site_crawler.py`)
```python
from site_crawler import SiteCrawler
crawler = SiteCrawler("https://example.com", port=9222)
pages = crawler.crawl_site(max_pages=10)
site_map = crawler.get_site_map()
```

## Task Completion Strategy

### 1. **Understand the Task**
- Read task requirements carefully
- Identify start and end states
- Don't assume the workflow - discover it

### 2. **Discover the Website Structure**
```python
# Use site crawler to map available routes
crawler = SiteCrawler(base_url)
pages = crawler.crawl_site()
# Look for routes like /confirm, /success, /complete, /payment
```

### 3. **Strategic Observation**
- **Use DOM inspection over screenshots** for efficiency
- Take screenshots only when you need visual confirmation
- Read page text to understand content

```python
# Get page structure
page_text = web.js("document.body.textContent")
page_info = browser.page_info()

# Check form elements
inputs = web.js("Array.from(document.querySelectorAll('input')).map(i => ({type: i.type, placeholder: i.placeholder, value: i.value}))")
```

### 4. **Form Interaction Best Practices**
```python
# Fill forms properly
web.fill("input[name='pickup']", "Location A")
web.fill("input[name='destination']", "Location B")

# Trigger form events
web.js("document.querySelector('input').dispatchEvent(new Event('input', {bubbles: true}))")
web.js("document.querySelector('input').dispatchEvent(new Event('change', {bubbles: true}))")

# Handle dropdowns
web.js("document.querySelector('.dropdown-item').click()")
```

### 5. **Verification Strategy**
Never assume task completion. Verify with:

```python
# URL change verification
initial_url = web.js("window.location.href")
# ... perform action ...
final_url = web.js("window.location.href")
if final_url != initial_url:
    print("Navigation occurred")

# Success indicators
success_keywords = ['success', 'complete', 'confirmed', 'booked']
page_text = web.js("document.body.textContent")
success = any(keyword in page_text.lower() for keyword in success_keywords)

# Route verification
if '/confirm' in final_url or '/success' in final_url:
    print("Reached completion page")
```

## Common Pitfalls to Avoid

### 1. **Assuming Completion Too Early**
- ❌ Form filled = task complete
- ✅ Look for concrete success indicators

### 2. **Over-relying on Screenshots**
- ❌ Taking many screenshots without reading them
- ✅ Use DOM inspection primarily, screenshots for visual confirmation

### 3. **Ignoring Form Validation**
- ❌ Assuming form submission worked
- ✅ Check for validation errors, required fields

### 4. **Not Understanding the App Type**
- ❌ Assuming all apps work like real websites
- ✅ Discover actual routes and capabilities

## Debugging Techniques

### 1. **Form Issues**
```python
# Check if form exists
form_exists = web.js("document.querySelector('form') !== null")

# Check button states
button_disabled = web.js("document.querySelector('button').disabled")

# Check for validation errors
errors = web.js("Array.from(document.querySelectorAll('*')).filter(el => el.textContent.includes('error')).map(el => el.textContent)")
```

### 2. **JavaScript Errors**
```python
# Execute JS and check for errors
try:
    result = web.js("document.querySelector('button').click()")
except Exception as e:
    print(f"JavaScript error: {e}")
```

### 3. **Network Issues**
```python
# Check if page is fully loaded
ready = web.js("document.readyState === 'complete'")

# Wait for elements
web.wait(selector, timeout=10)
```

## Task Completion Checklist

### Before Starting:
- [ ] Understand the task requirements
- [ ] Crawl the website to understand structure
- [ ] Identify potential completion routes

### During Task:
- [ ] Use DOM inspection over screenshots
- [ ] Verify each step worked before proceeding
- [ ] Check for form validation and errors
- [ ] Monitor URL changes for navigation

### Completion Verification:
- [ ] URL changed to success/completion route
- [ ] Page contains success indicators
- [ ] Task-specific artifacts present (booking ID, confirmation, etc.)
- [ ] No error messages visible

## Example Task Flow

```python
# 1. Setup
web = WebTool(port=9222)
web.connect()

# 2. Discover structure
crawler = SiteCrawler(base_url)
pages = crawler.crawl_site()
print("Available routes:", [p['url'] for p in pages])

# 3. Navigate and fill form
web.go(base_url)
web.fill("input[name='pickup']", "Location A")
web.fill("input[name='destination']", "Location B")

# 4. Submit and verify
initial_url = web.js("window.location.href")
web.click("button[type='submit']")
time.sleep(3)

final_url = web.js("window.location.href")
if final_url != initial_url:
    print("✅ Task progressed")
    if any(route in final_url for route in ['/success', '/confirm', '/complete']):
        print("✅ Task completed successfully")
else:
    print("❌ Task stuck - debug needed")

web.close()
```

## Key Principles

1. **Discover, don't assume** - Crawl the site to understand what's possible
2. **Verify each step** - Don't assume actions worked
3. **Use the right observation method** - DOM inspection > screenshots
4. **Follow the complete flow** - Don't stop at intermediate steps
5. **Concrete success criteria** - Look for specific completion indicators

This toolkit provides everything needed for effective web task automation. The key is understanding the specific website structure and following the complete user journey to true task completion.