# CLAUDE.md - Web Agent Automation Guide

## Core Web Actions (from tools.json)

### navigate
```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.go('URL'); web.close()"
```

### click
```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.click('selector'); web.close()"
```

### type
```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.fill('selector', 'text'); web.close()"
```

### screenshot
```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.screenshot('screenshots/debug.png'); web.close()"
```

### key
```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.key('Tab'); web.close()"
```

### execute_js
```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); result = web.js('code'); print(result); web.close()"
```

### coordinate_click (fallback)
```bash
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.cmd('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': 400, 'y': 200, 'button': 'left', 'clickCount': 1}); web.cmd('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': 400, 'y': 200, 'button': 'left', 'clickCount': 1}); web.close()"
```

## Task Execution Pattern

### 1. ALWAYS Use Small Actions
- One action per bash command
- Check results after each action
- Never batch multiple interactions

### 2. Element Discovery Strategy
```python
# Count elements first
input_count = web.js('document.querySelectorAll("input").length')
button_count = web.js('document.querySelectorAll("button").length')

# Get element details (handle JS execution failures)
try:
    buttons = web.js('Array.from(document.querySelectorAll("button")).map(b => b.textContent.trim())')
except:
    # Fallback to coordinate clicking if JS fails
    pass
```

### 3. Form Interaction Protocol
```python
# Method 1: Selector-based (preferred)
web.click('input[placeholder*="pickup"]')
web.fill('input[placeholder*="pickup"]', 'Location A')

# Method 2: Sequential clicking (when selectors fail)
web.click('input')  # First input
web.type('Location A')
web.key('Tab')
web.type('Location B')

# Method 3: Coordinate clicking (last resort)
web.cmd('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': 400, 'y': 200, 'button': 'left', 'clickCount': 1})
web.cmd('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': 400, 'y': 200, 'button': 'left', 'clickCount': 1})
```

### 4. Verification Requirements
```python
# ALWAYS verify completion
initial_url = web.js('window.location.href')
# ... perform action ...
final_url = web.js('window.location.href')

# Check for changes
if final_url != initial_url:
    print("✅ Navigation occurred")
else:
    print("❌ No change - action failed")

# Check for success indicators
success_keywords = ['success', 'complete', 'confirmed', 'booked', 'scheduled']
page_text = web.js('document.body.textContent.lower()')
success = any(keyword in page_text for keyword in success_keywords)
```

## Debugging Protocol

### When JavaScript Fails
```python
# If web.js() returns None or fails:
# 1. Check page load state
ready = web.js('document.readyState')

# 2. Try fresh navigation
web.go(url)
time.sleep(3)

# 3. Use coordinate clicking
web.cmd('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': X, 'y': Y, 'button': 'left', 'clickCount': 1})
```

### When Form Submission Fails
```python
# Try multiple approaches:
# 1. Click submit button
web.click('button[type="submit"]')

# 2. Press Enter
web.key('Enter')

# 3. Try all buttons
button_count = web.js('document.querySelectorAll("button").length')
for i in range(button_count):
    web.click(f'button:nth-child({i+1})')
    time.sleep(2)
    # Check if URL changed
    if web.js('window.location.href') != initial_url:
        break
```

## Task Completion Checklist

### Before Starting
- [ ] Understand task goal and expected outcome
- [ ] Check if browser is already on port 9222
- [ ] Create screenshots directory if needed: `mkdir -p screenshots`

### During Execution
- [ ] Use TodoWrite to track progress
- [ ] One action per bash command
- [ ] Verify each step worked before proceeding
- [ ] Check for errors after each action

### Completion Verification
- [ ] URL changed to success/completion route
- [ ] Page contains success indicators (confirmed, booked, etc.)
- [ ] No error messages visible
- [ ] Task-specific artifacts present

## Common Patterns

### Task Search Pattern
```python
# Search for task by ID
echo "task-id" | python example/tasks.py
# Returns: {'id': 'task-id', 'goal': 'description', 'website': {...}}
```

### UDriver/Booking Pattern
```python
# 1. Navigate to site
web.go('https://evals-udriver.vercel.app/')

# 2. Fill pickup location
web.click('input')
web.type('Pickup Location')
web.key('Tab')

# 3. Fill destination
web.type('Destination')
web.key('Tab')

# 4. Set time if available
web.fill('input[type="time"]', '14:00')

# 5. Submit
web.click('button')

# 6. Verify completion
final_url = web.js('window.location.href')
success = '/success' in final_url or '/confirm' in final_url
```

## Never Do This
- ❌ Batch multiple interactions in one bash command
- ❌ Assume form submission worked without verification
- ❌ Use screenshots for routine inspection
- ❌ Skip verification steps
- ❌ Continue without checking if previous action worked

## Always Do This
- ✅ One action per bash command
- ✅ Verify each step completed successfully
- ✅ Check URL changes and page content
- ✅ Use DOM inspection over screenshots
- ✅ Handle JavaScript execution failures gracefully
- ✅ Use coordinate clicking when selectors fail

## Key Success Indicators
- URL navigation (initial_url != final_url)
- Success keywords in page text
- Specific completion routes (/success, /confirm, /complete)
- Form validation passes
- No error messages visible

## Emergency Debugging
When nothing works:
1. Take screenshot: `web.screenshot('screenshots/debug.png')`
2. Check page HTML: `web.js('document.documentElement.outerHTML')`
3. Try coordinate clicking at common UI locations
4. Fresh page reload: `web.go(url)` + `time.sleep(3)`