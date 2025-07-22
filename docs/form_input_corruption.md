# Form Input Corruption Fix

## When Input Shows Garbled Text
```python
web.key('Ctrl+a')
web.key('Delete') 
# Then retype clean text
```

**Symptoms:**
- Text appears jumbled/concatenated
- "No results" when dropdown should show options
- Input contains unexpected characters

**Cause:** Mixing different interaction methods can corrupt form state.