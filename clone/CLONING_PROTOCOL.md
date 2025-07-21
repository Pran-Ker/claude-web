# Perfect Website Replica Cloning Protocol

## Overview
This protocol creates pixel-perfect replicas of websites that look and behave identically to the original when served locally. Use this document as the definitive guide for high-fidelity website cloning.

## Execution Phases

### Phase 1: Initial Analysis & Setup
**Goal:** Understand the website structure and prepare local environment

```bash
# 1. Create project directory
mkdir -p clone/{website_name}/{assets,css,js,images}

# 2. Navigate to site and analyze
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.go('TARGET_URL'); print('Connected to:', web.js('window.location.href'))"
```

**Analysis Checklist:**
- [ ] Page type (static/dynamic/SPA)  
- [ ] CSS framework detection (Bootstrap, Tailwind, etc.)
- [ ] JavaScript dependencies (React, Vue, etc.)
- [ ] Asset complexity (images, fonts, videos)
- [ ] Responsive breakpoints
- [ ] External dependencies (CDNs, APIs)

### Phase 2: Complete Asset Harvest
**Goal:** Download every resource needed for offline rendering

#### 2.1 HTML Capture
```bash
# Get complete page source
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); html = web.js('document.documentElement.outerHTML'); print(html)" > clone/{website_name}/index.html
```

#### 2.2 CSS Extraction
```bash
# Extract all stylesheets
python3 -c "
from tools.web_tool import WebTool
web = WebTool(port=9222)
web.connect()
# Get all CSS links
css_links = web.js('Array.from(document.querySelectorAll(\"link[rel=stylesheet]\")).map(link => link.href)')
print('CSS Links:', css_links)
# Get all inline styles
inline_styles = web.js('Array.from(document.querySelectorAll(\"style\")).map(style => style.textContent)')
print('Inline Styles:', inline_styles)
web.close()
"
```

**Download each CSS file using WebFetch:**
- Use WebFetch to get each stylesheet content
- Save to `clone/{website_name}/css/`
- Update paths in HTML to local references

#### 2.3 Image Asset Download  
```bash
# Find all images
python3 -c "
from tools.web_tool import WebTool
web = WebTool(port=9222)
web.connect()
images = web.js('Array.from(document.querySelectorAll(\"img\")).map(img => ({src: img.src, alt: img.alt, width: img.width, height: img.height}))')
bg_images = web.js('Array.from(document.querySelectorAll(\"*\")).filter(el => getComputedStyle(el).backgroundImage !== \"none\").map(el => getComputedStyle(el).backgroundImage)')
print('Images:', images)
print('Background Images:', bg_images)
web.close()
"
```

**For each image:**
- Use WebFetch to download image content
- Save to `clone/{website_name}/images/`
- Update src attributes to local paths

#### 2.4 JavaScript Capture
```bash
# Extract all scripts
python3 -c "
from tools.web_tool import WebTool
web = WebTool(port=9222)
web.connect()
scripts = web.js('Array.from(document.querySelectorAll(\"script[src]\")).map(script => script.src)')
inline_scripts = web.js('Array.from(document.querySelectorAll(\"script:not([src])\")).map(script => script.textContent)')
print('External Scripts:', scripts)
print('Inline Scripts:', inline_scripts)
web.close()
"
```

#### 2.5 Font and Misc Assets
```bash
# Find font files and other assets
python3 -c "
from tools.web_tool import WebTool
web = WebTool(port=9222)
web.connect()
fonts = web.js('Array.from(document.styleSheets).flatMap(sheet => Array.from(sheet.cssRules || [])).filter(rule => rule.style && rule.style.fontFamily).map(rule => rule.cssText)')
print('Font Rules:', fonts)
web.close()
"
```

### Phase 3: Style Preservation & Path Fixing
**Goal:** Ensure all styles render identically with local paths

#### 3.1 CSS Path Resolution
For each CSS file:
- Replace external URLs with local paths
- Fix relative image references  
- Preserve media queries and responsive breakpoints
- Handle CSS imports (@import statements)

#### 3.2 Computed Style Capture
```bash
# Capture computed styles for critical elements
python3 -c "
from tools.web_tool import WebTool
web = WebTool(port=9222)
web.connect()
# Get computed styles for body and major containers
body_styles = web.js('JSON.stringify(getComputedStyle(document.body))')
main_styles = web.js('Array.from(document.querySelectorAll(\"main, .main, #main, .container\")).map(el => getComputedStyle(el).cssText)')
print('Body Styles:', body_styles)
print('Main Container Styles:', main_styles)
web.close()
"
```

### Phase 4: Exact Reconstruction
**Goal:** Generate the final replica with all assets properly linked

#### 4.1 HTML Processing
- Update all asset references to local paths
- Preserve original DOM structure exactly
- Maintain all data attributes and IDs
- Keep meta tags and head structure intact

#### 4.2 CSS Optimization
- Combine external stylesheets if needed
- Preserve cascade order
- Maintain media query functionality
- Fix font-face declarations for local fonts

#### 4.3 JavaScript Handling
- Include essential JavaScript for functionality
- Remove or modify external API calls
- Preserve interactive elements where possible
- Add fallbacks for dynamic content

### Phase 5: Quality Verification
**Goal:** Ensure replica matches original exactly

#### 5.1 Visual Comparison
```bash
# Take comparison screenshots
python3 -c "from tools.web_tool import WebTool; web = WebTool(port=9222); web.connect(); web.go('ORIGINAL_URL'); web.screenshot('clone/{website_name}/original.png'); web.go('file:///path/to/clone/{website_name}/index.html'); web.screenshot('clone/{website_name}/replica.png')"
```

#### 5.2 Functionality Check
- [ ] All images load correctly
- [ ] CSS styles apply properly  
- [ ] Fonts render as expected
- [ ] Responsive behavior works
- [ ] Basic interactions function
- [ ] No console errors

#### 5.3 File Structure Validation
```
clone/{website_name}/
├── index.html          # Main page
├── css/               # All stylesheets  
├── js/                # JavaScript files
├── images/            # All images
├── fonts/             # Font files
├── original.png       # Reference screenshot
├── replica.png        # Verification screenshot
└── assets/            # Other assets
```

## Success Criteria
**Perfect replica achieved when:**
- ✅ Visual appearance is pixel-perfect match
- ✅ All assets load without external dependencies  
- ✅ Responsive behavior matches original
- ✅ No broken images or missing resources
- ✅ Typography renders identically
- ✅ Layout structure is preserved exactly

## Common Pitfalls to Avoid
- ❌ Missing CSS cascade order preservation
- ❌ Incorrect relative path conversion
- ❌ Forgetting viewport meta tags
- ❌ Missing Google Fonts or external fonts
- ❌ JavaScript errors breaking layout
- ❌ Image size/quality degradation
- ❌ Media query breakpoint changes

## Tool Usage Pattern
**When user says "clone [website]":**
1. Create project directory structure
2. Execute all phases systematically  
3. Use WebTool for DOM access and screenshots
4. Use WebFetch for downloading external resources
5. Use Write tool for generating final files
6. Verify with comparison screenshots

## Emergency Debugging
**If replica doesn't match:**
1. Compare original.png vs replica.png screenshots
2. Check browser console for missing resources
3. Validate all asset paths are correct
4. Verify CSS cascade hasn't been disrupted
5. Test at different screen sizes
6. Check for JavaScript errors affecting layout