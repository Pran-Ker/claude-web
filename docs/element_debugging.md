# Element Selection Debugging

## When Click/Fill Fails - Do This First

### 1. Inspect HTML Source (Most Important)
```python
html = web.js('document.body.innerHTML')
# Find your target text and examine surrounding HTML
start_idx = html.find('target_text') - 200  
end_idx = html.find('target_text') + 200
print(html[max(0,start_idx):end_idx])
```

### 2. Use Better Selectors
- `button[aria-label="exact text"]` - best for buttons  
- `input[placeholder*="partial text"]` - best for form fields
- `[role="button"]` - for custom buttons

### 3. Dropdown Pattern
```python
web.click('input[placeholder*="partial_placeholder"]')
web.type('partial_text')  # Partial text triggers dropdown
web.key('ArrowDown')      # Navigate options  
web.key('Enter')          # Select
```

**Stop using:** generic `button`, coordinate clicking, or guessing selectors.