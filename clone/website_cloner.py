#!/usr/bin/env python3
"""
Simple Website Cloner using Task Agent
Keeps it simple initially, improves iteratively
"""

class WebsiteCloner:
    def __init__(self, url, website_name):
        self.url = url
        self.website_name = website_name
        self.output_dir = f"/Users/pran-ker/Developer/claude-web/clone/{website_name}/"
        
    def clone_website(self):
        """Main method that uses Task agent to handle all steps"""
        print(f"Starting clone of {self.url} -> {self.website_name}")
        
        # Single Task agent handles everything sequentially
        print("Launching clone task agent...")
        content_data = self.launch_content_fetcher()
        text_data = self.launch_text_extractor(content_data)
        links_data = self.launch_link_analyzer(content_data)
        images_data = self.launch_image_processor(content_data)
        clone_result = self.launch_clone_generator(text_data, links_data, images_data)
        
        print(f"Clone completed: {clone_result}")
        return clone_result
    
    def launch_content_fetcher(self):
        """Launch Agent 1: Content Fetcher using Task agent"""
        print("   📡 Content fetcher agent starting...")
        return {
            "url": self.url,
            "status": "task_ready",
            "prompt": f"""Analyze the website {self.url} and extract:
            1. Page title
            2. Main navigation items  
            3. Key sections (hero, content, footer, etc.)
            4. Content type (landing page, blog, app, etc.)
            5. Complexity estimate (simple/medium/complex)
            6. Total number of links found
            7. Total number of images found
            
            Return structured data that can be used by other agents.""",
            "timestamp": self._get_timestamp()
        }
    
    def _get_timestamp(self):
        """Helper to get current timestamp"""
        import datetime
        return datetime.datetime.now().isoformat()
    
    def launch_text_extractor(self, content_data):
        """Extract text content - keep it simple"""
        if content_data.get("status") != "fetched":
            return {"status": "skipped", "reason": "content fetch failed"}
            
        # Just get the essential text using WebFetch
        try:
            from tools import WebFetch
            text_content = WebFetch(
                url=self.url,
                prompt="Extract all text content, headings, and main copy. Return in a structured format."
            )
            return {"status": "extracted", "content": text_content}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    def launch_link_analyzer(self, content_data):
        """Extract links - simple and effective"""
        try:
            from tools import WebFetch
            links = WebFetch(
                url=self.url,
                prompt="Find ALL clickable links on this page and return their URLs and link text."
            )
            return {"status": "extracted", "links": links}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    def launch_image_processor(self, content_data):
        """Extract images - focus on what I actually need"""
        try:
            from tools import WebFetch
            images = WebFetch(
                url=self.url,
                prompt="Find all images, their sources, alt text, and describe their purpose (logo, content, decoration)."
            )
            return {"status": "extracted", "images": images}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    def launch_clone_generator(self, text_data, links_data, images_data):
        """Generate the actual files"""
        import os
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Create a simple HTML file with the extracted content
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clone of {self.website_name}</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <!-- Text: {text_data.get('status', 'unknown')} -->
    <!-- Links: {links_data.get('status', 'unknown')} -->  
    <!-- Images: {images_data.get('status', 'unknown')} -->
    
    <h1>Clone of {self.website_name}</h1>
    <p>Cloned from: {self.url}</p>
    
</body>
</html>"""
        
        with open(f"{self.output_dir}/index.html", "w") as f:
            f.write(html_content)
            
        return {"status": "generated", "path": self.output_dir}

if __name__ == "__main__":
    # Simple test
    cloner = WebsiteCloner("https://realevals.xyz", "realevals")
    result = cloner.clone_website()
    print(f"Result: {result}")