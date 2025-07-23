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
# Optimized for AI context (default: JPEG 80% quality)
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.screenshot('screenshots/debug.png'); web.close()"

# High quality when needed
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.screenshot('screenshots/debug.png', quality=95); web.close()"
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
- [ ] **Check caching directory for website notes**: `ls caching/` and read any relevant files

### During Execution
- [ ] Use TodoWrite to track progress
- [ ] One action per bash command
- [ ] Take screenshots before major actions (form submits, navigation)
- [ ] Verify each step worked before proceeding
- [ ] Check for errors after each action

### Completion Verification
- [ ] URL changed to success/completion route
- [ ] Page contains success indicators (confirmed, booked, etc.)
- [ ] No error messages visible
- [ ] Task-specific artifacts present
- [ ] **Log website learnings**: Save important discoveries to `caching/[website-name].md`

### **MANDATORY: Official Task Evaluation**
After completing ANY task, you MUST run the official evaluation using the generic evaluation script:

## Direct Evaluation (Recommended)
```bash
python3 example/evaluate_task.py TASK_ID "YOUR_ANSWER"
```

## Legacy Method (Still Works)
```bash
python3 -c "
from example.tasks import evaluate_task
result = evaluate_task('TASK_ID', 'YOUR_ANSWER')
print(f'✅ Success: {result[\"success\"]}')
print(f'📊 Reward: {result[\"reward\"]}') 
print(f'💬 Message: {result[\"message\"]}')
if not result['success']:
    print(f'❌ Error details: {result.get(\"error\", \"Unknown\")}')
"
```

**Replace**:
- `TASK_ID` with the actual task ID (e.g., 'udriver-7', 'dashdish-10')
- `YOUR_ANSWER` with your extracted result (e.g., "6", "license plate ABC123")

**How Evaluation Works**:
- **JMESPath Tasks**: Extracts specific values from environment state using queries like `differences.currentTrips.added."6".pickup.name`
- **LLM Boolean Tasks**: Uses natural language evaluation with rubrics like "Does the answer reflect that six rides were taken in June?"
- **Ground Truth**: Navigates to `/finish` endpoint to get current environment state
- **Official agisdk**: Uses the same WebCloneEvaluator as the official benchmark

**Examples**:
```bash
# Evaluate udriver-7 (LLM boolean evaluation)
python3 example/evaluate_task.py udriver-7 "6"

# Evaluate dashdish order (JMESPath evaluation)  
python3 example/evaluate_task.py dashdish-10 ""
```

## Common Patterns

### Task Search Pattern
```python
# Search for task by ID
echo "task-id" | python example/tasks.py
# Returns: {'id': 'task-id', 'goal': 'description', 'website': {...}}
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
- **MOST IMPORTANT**: Official evaluation returns `success: True`

## Website Cloning
**Perfect Replica Cloning:** Follow the complete protocol in `clone/CLONING_PROTOCOL.md` for pixel-perfect website replicas.

**Quick Clone:**
```bash
python3 clone/website_cloner.py
```

**Protocol:** When user says "clone [website]", use the systematic approach in `clone/CLONING_PROTOCOL.md` to create exact visual replicas with all assets downloaded locally.

## Emergency Debugging
When nothing works:
1. Take screenshot: `web.screenshot('screenshots/debug.png')`
2. Check page HTML: `web.js('document.documentElement.outerHTML')`
3. Try coordinate clicking at common UI locations
4. Fresh page reload: `web.go(url)` + `time.sleep(3)`

## Advanced Debugging (Only When Standard Methods Fail)
If basic debugging fails, see specialized docs:
- `docs/dropdown_navigation.md` - When dropdown selection fails  
- `docs/form_input_corruption.md` - When text input gets garbled
- `docs/task_completion_verification.md` - When unsure if task is complete