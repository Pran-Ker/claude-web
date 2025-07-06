# Web Automation Guide for Claude

## Overview
This guide provides a systematic approach to web automation tasks using the predefined tools: `web_tool.py` (analysis) and `web_action_tool.py` (actions).

## Core Principle: ALWAYS ANALYZE FIRST
**Never create actions without first analyzing the website structure.**

## Step-by-Step Process

### 0. Check Existing Action Sets (NEW)
**Before starting any new automation, always check if similar work has been done:**

1. **Review Existing Tasks**: Check the `tasks/` directory for existing JSON files
   ```bash
   ls tasks/
   cat tasks/*.json | grep -E '"url"|"_description"'
   ```

2. **Search for Website URLs**: Look for tasks targeting the same domain
   ```bash
   grep -r "your-target-domain" tasks/
   ```

3. **Read Existing Task Descriptions**: Examine `_description` fields to understand what was attempted
   ```bash
   grep -A 1 "_description" tasks/*.json
   ```

4. **Learn from Previous Attempts**: 
   - If tasks exist for the same website, read them to understand:
     - What selectors were used
     - What actions succeeded/failed
     - What timing delays were needed
     - What navigation patterns worked
   - Adapt existing successful patterns instead of starting from scratch

### 1. Website Analysis Phase
Before any automation, always run the web analysis tool:

```bash
source venv/bin/activate && python web_tool.py [URL] --selectors [RELEVANT_SELECTORS] --wait-for body
```

**Standard selectors to check:**
- `button` - All clickable buttons
- `input` - Form inputs
- `h1`, `h2`, `h3` - Headings and page structure
- `[class*="create"]` - Elements with "create" in class name
- `[class*="submit"]` - Submit-related elements
- `[class*="form"]` - Form-related elements
- `[data-testid]` - Test identifiers
- `nav` - Navigation elements
- `main` - Main content areas
- `[role="button"]` - ARIA button roles

### 2. Element Identification Strategy

#### For Buttons:
1. Look for text content in the analysis results
2. Check CSS classes (e.g., `MuiButton-root`, `btn`, `button`)
3. Look for specific patterns like `MuiButton-text` for Material-UI
4. Use nth-child selectors as last resort

#### For Forms:
1. Identify input fields by `name` attribute
2. Look for form containers
3. Check for submit buttons
4. Identify validation elements

#### For Navigation:
1. Look for nav elements
2. Check for menu items
3. Identify breadcrumbs
4. Look for dropdown menus

### 3. Action Planning

#### Common Action Patterns:
1. **Navigation + Screenshot Pattern:**
   ```json
   [
     {"type": "navigate", "url": "..."},
     {"type": "sleep", "seconds": 3},
     {"type": "screenshot", "filename": "screenshots/before.png"}
   ]
   ```

2. **Form Filling Pattern:**
   ```json
   [
     {"type": "fill", "selector": "input[name='field']", "text": "value"},
     {"type": "select", "selector": "select[name='dropdown']", "value": "option"},
     {"type": "click", "selector": "button[type='submit']"}
   ]
   ```

3. **Click and Wait Pattern:**
   ```json
   [
     {"type": "click", "selector": "button.target"},
     {"type": "wait", "selector": ".result", "condition": "visible"},
     {"type": "screenshot", "filename": "screenshots/after.png"}
   ]
   ```

### 4. Selector Strategy Priority

1. **ID selectors** (most reliable): `#unique-id`
2. **Name attributes**: `input[name='username']`
3. **Data attributes**: `[data-testid='submit-btn']`
4. **CSS classes**: `.specific-class`
5. **Text-based** (use carefully): Find elements containing specific text
6. **Structural** (least reliable): `div:nth-child(2)`

### 5. Common Selector Patterns

#### Material-UI Components:
- Buttons: `button.MuiButton-root`, `button.MuiButton-text`
- Inputs: `input.MuiInputBase-input`
- Selects: `div.MuiSelect-root`

#### React Big Calendar:
- Calendar container: `.rbc-calendar`
- Events: `.rbc-event`
- Time slots: `.rbc-time-slot`

#### Common Web Patterns:
- Submit buttons: `button[type='submit']`, `input[type='submit']`
- Cancel buttons: `button[type='button']`
- Form fields: `input[type='text']`, `input[type='email']`

### 6. Error Handling and Debugging

#### Common Issues:
1. **Invalid Selector Error**: 
   - Check CSS syntax
   - Avoid pseudo-selectors like `:contains()`
   - Use proper escaping for special characters

2. **Element Not Found**:
   - Add wait conditions
   - Check if element loads dynamically
   - Verify selector accuracy

3. **Element Not Clickable**:
   - Scroll to element first
   - Wait for element to be clickable
   - Check for overlapping elements

#### Debugging Steps:
1. Take screenshots at each step
2. Add sleep delays for dynamic content
3. Use `page_info` action to check current state
4. Re-analyze website if structure changes

### 7. Task File Organization

#### File Naming Convention:
- `action_description.json` (e.g., `create_event.json`)
- `website_action.json` (e.g., `calendar_create.json`)
- `test_scenario.json` (e.g., `form_validation.json`)

#### Task Structure Template:
```json
[
  {
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
    "selector": "CSS_SELECTOR",
    "additional_params": "..."
  },
  {
    "type": "screenshot",
    "filename": "screenshots/final_state.png"
  }
]
```

### 8. Best Practices

#### Always Include:
1. **Initial navigation** with URL
2. **Wait/sleep** for page load
3. **Before and after screenshots** for verification
4. **Error handling** with `wait_for` conditions

#### Avoid:
1. Creating actions without analysis
2. Using complex selectors without testing
3. Hardcoding wait times (use dynamic waits)
4. Assuming element positions remain constant

### 9. Tool Usage Commands

#### Analysis Command:
```bash
source venv/bin/activate && python web_tool.py [URL] --selectors [LIST] --wait-for body
```

#### Action Command:
```bash
source venv/bin/activate && python web_action_tool.py --actions [TASK_NAME]
```

#### Task File Location:
- Analysis tool: `web_tool.py`
- Action tool: `web_action_tool.py`
- Task definitions: `tasks/[task_name].json`
- Screenshots: `screenshots/[filename].png`

### 10. Troubleshooting Checklist

- [ ] Did I analyze the website first?
- [ ] Are my selectors valid CSS?
- [ ] Did I include proper wait conditions?
- [ ] Are screenshots taken for verification?
- [ ] Did I test the selector in the analysis results?
- [ ] Is the task file properly formatted JSON?
- [ ] Are file paths correct for screenshots?

## Example Workflow

1. **Check Existing**: `ls tasks/ && grep -r "target-domain" tasks/`
2. **Review Previous**: Read existing task files for the same domain
3. **Analyze**: `python web_tool.py [TARGET_URL] --selectors [RELEVANT_SELECTORS]`
4. **Plan**: Review results, identify target elements, adapt from existing patterns
5. **Create**: Write task JSON with proper selectors (or modify existing)
6. **Test**: `python web_action_tool.py --actions [TASK_NAME]`
7. **Verify**: Check screenshots and results
8. **Refine**: Adjust selectors if needed
9. **Cleanup**: Remove failed tasks, document successful ones

Remember: The key to successful web automation is thorough analysis before action!

## Task Management and Cleanup

### Post-Execution Cleanup
After completing web automation tasks, always perform cleanup to maintain a clean working environment:

1. **Remove Failed Tasks**: Delete any task JSON files that failed during execution
   ```bash
   rm tasks/failed_task_name.json
   ```

2. **Document Working Tasks**: Add descriptive comments to successful task files to explain their purpose
   - Use a `_description` field in the JSON for documentation
   - Example:
   ```json
   [
     {
       "_description": "Task: Click on the 'Enter Location' input field on https://evals-udriver.vercel.app/",
       "type": "navigate",
       "url": "https://evals-udriver.vercel.app/"
     }
   ]
   ```

3. **Organize Screenshots**: Keep only relevant screenshots from successful runs
   ```bash
   # Remove failed run screenshots
   rm screenshots/failed_*.png
   ```

### Task Documentation Standards
- Always include a `_description` field as the first element in task JSON arrays
- Use clear, descriptive names for task files (e.g., `fill_destination_field.json`)
- Document the target website and main action performed
- Keep task files focused on single, specific actions

### Maintenance Best Practices
- Regularly review and clean up old task files
- Keep a maximum of 10 task files in the tasks directory
- Archive completed tasks if they need to be preserved
- Maintain screenshots only for current/recent successful runs

## Action Set Reuse Strategy

### When to Reuse Existing Actions
- **Same Website Domain**: Always check for existing tasks on the same domain
- **Similar UI Patterns**: Look for tasks with similar Material-UI or React components
- **Common Actions**: Search for existing patterns like form filling, button clicking, navigation

### How to Adapt Existing Actions
1. **Copy Successful Patterns**: Use working selectors and timing from successful tasks
2. **Modify URLs**: Update navigation URLs while keeping successful interaction patterns
3. **Adjust Selectors**: Fine-tune selectors based on new analysis while following proven patterns
4. **Preserve Timing**: Keep successful sleep/wait patterns that worked before

### Action Set Naming Convention
- Use descriptive names that include website and action: `[site]_[action].json`
- Include domain in filename for easy searching: `grep -l "[domain]" tasks/*.json`
- Group related actions with consistent prefixes: `[category]_[action].json`

### Knowledge Transfer Between Tasks
- **Successful Selectors**: Document what CSS selectors work reliably
- **Timing Patterns**: Note required delays for different UI frameworks
- **Navigation Flows**: Record successful paths through complex interfaces
- **Error Patterns**: Document what approaches failed and why

### Quick Reference Commands
```bash
# Find all tasks for a domain
grep -l "[target-domain]" tasks/*.json

# Show all task descriptions
grep -h "_description" tasks/*.json

# Find successful patterns by action type
grep -A 5 '"type": "click"' tasks/*.json
grep -A 5 '"type": "fill"' tasks/*.json

# Copy and modify existing successful task
cp tasks/[existing_task].json tasks/[new_task].json
# Edit the new file with similar patterns
```