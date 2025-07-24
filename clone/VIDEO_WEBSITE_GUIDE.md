# Video Website Creation Guide

## Browser Testing Command
Always test video websites using the web automation tool:

```bash
python3 -c "
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from tools.web_tool import WebTool
web = WebTool(port=9222)
web.connect()
web.go(f'file://{os.path.abspath(\"./project-name/index.html\")}')
web.close()
"
```

## Remotion Setup (Complex Video Generation)
If user wants professional video rendering with React:

```bash
npx create-video@latest project-name
# Select template when prompted
cd project-name
npm start
```

Note: This requires interactive template selection and may create git submodules.

## File Paths
- Video files should be placed in a subdirectory (e.g., `prannay-video/`)
- Use relative paths for browser automation: `./project-name/index.html`

## Port Issues
- Default HTTP server port 8000 may be in use
- Try alternative ports: 8001, 8002, etc.
- Browser automation uses port 9222