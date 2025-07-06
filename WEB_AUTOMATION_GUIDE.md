# Web Automation Guide

## Overview
This guide provides a systematic approach to web automation using `web_tool.py` (analysis) and `web_action_tool.py` (actions).

## Core Principle: ALWAYS ANALYZE FIRST
**Never create actions without first analyzing the website structure.**

## Step-by-Step Process

### 1. Check Existing Tasks
Before starting new automation, check if similar work exists:

```bash
ls tasks/
grep -r "target-domain" tasks/
```

### 2. Website Analysis (Choose Your Method)

#### Method A: Quick Visual Analysis (RECOMMENDED)
For fast problem-solving when you need to see the interface:

```bash
source venv/bin/activate && python analyze_website.py [URL]
```

**This will:**
- Take a screenshot you can examine
- List all clickable elements with their text
- Provide ready-to-use selectors
- Save analysis as JSON for reference

**Example usage:**
```bash
python analyze_website.py https://evals-gomail.vercel.app/
```

#### Method B: Detailed Technical Analysis
For complex websites or when you need specific element data:

```bash
source venv/bin/activate && python web_tool.py [URL] --selectors [SELECTORS] --wait-for body
```

**Essential selectors to check:**
- `button` - All clickable buttons
- `input` - Form inputs
- `[data-testid]` - Test identifiers
- `[class*="submit"]` - Submit-related elements

### 3. Selector Strategy (Priority Order)

1. **ID selectors**: `#unique-id`
2. **Name attributes**: `input[name='username']`
3. **Data attributes**: `[data-testid='submit-btn']`
4. **CSS classes**: `.specific-class`
5. **Structural** (last resort): `div:nth-child(2)`

### 4. Common Action Patterns

**Navigation Pattern:**
```json
[
  {"type": "navigate", "url": "..."},
  {"type": "sleep", "seconds": 3},
  {"type": "screenshot", "filename": "screenshots/before.png"}
]
```

**Form Filling Pattern:**
```json
[
  {"type": "fill", "selector": "input[name='field']", "text": "value"},
  {"type": "click", "selector": "button[type='submit']"}
]
```

### 5. Task Structure Template
```json
[
  {
    "_description": "Task: [Brief description of what this does]",
    "type": "navigate",
    "url": "TARGET_URL"
  },
  {
    "type": "sleep",
    "seconds": 3
  },
  {
    "type": "screenshot",
    "filename": "screenshots/initial_state.png"
  },
  {
    "type": "ACTION_TYPE",
    "selector": "CSS_SELECTOR"
  },
  {
    "type": "screenshot",
    "filename": "screenshots/final_state.png"
  }
]
```

### 6. Common UI Patterns

**Material-UI:**
- Buttons: `button.MuiButton-root`
- Inputs: `input.MuiInputBase-input`

**Common Web Elements:**
- Submit buttons: `button[type='submit']`
- Form fields: `input[type='text']`

### 7. Error Handling

**Common Issues:**
1. **Invalid Selector**: Check CSS syntax, avoid pseudo-selectors
2. **Element Not Found**: Add wait conditions, verify selector
3. **Element Not Clickable**: Scroll to element, wait for clickable state

**Debugging Steps:**
1. Take screenshots at each step
2. Add sleep delays for dynamic content
3. Re-analyze if structure changes

### 8. Tool Commands

**Quick Analysis (Screenshots + Element List):**
```bash
source venv/bin/activate && python analyze_website.py [URL]
```

**Detailed Analysis:**
```bash
source venv/bin/activate && python web_tool.py [URL] --selectors [LIST] --wait-for body
```

**Action:**
```bash
source venv/bin/activate && python web_action_tool.py --actions [TASK_NAME]
```

### 9. Best Practices

**Always Include:**
- Initial navigation with URL
- Wait/sleep for page load
- Before and after screenshots
- Error handling with wait conditions

**Avoid:**
- Creating actions without analysis
- Complex selectors without testing
- Hardcoded wait times
- Assuming element positions remain constant

### 10. File Organization

**Naming Convention:**
- `[action_description].json` (e.g., `create_event.json`)
- Include domain for easy searching

**Cleanup After Tasks:**
- Remove failed task files
- Keep only successful screenshots
- Document working tasks with `_description` field

## Quick Reference

```bash
# Find existing tasks for a domain
grep -l "[domain]" tasks/*.json

# Show all task descriptions
grep -h "_description" tasks/*.json

# Copy successful task pattern
cp tasks/[existing_task].json tasks/[new_task].json
```

**Remember: Analyze first, then act!**