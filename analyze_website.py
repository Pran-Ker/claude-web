#!/usr/bin/env python3
"""
Simple Website Screenshot Analyzer
Usage: python analyze_website.py <URL>
"""

import sys
import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import os


def setup_driver():
    """Setup Chrome driver with optimal settings"""
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)


def analyze_website(url):
    """Take screenshot and analyze clickable elements"""
    print(f"🔍 Analyzing: {url}")
    
    driver = setup_driver()
    
    try:
        driver.get(url)
        time.sleep(3)
        
        # Take screenshot
        os.makedirs("screenshots", exist_ok=True)
        screenshot_file = f"screenshots/analysis_{int(time.time())}.png"
        driver.save_screenshot(screenshot_file)
        
        # Find all clickable elements with their text
        clickable_elements = []
        
        selectors = ["a", "button", "[role='button']", "[onclick]", "input[type='button']", "input[type='submit']"]
        
        for selector in selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for i, element in enumerate(elements):
                try:
                    if element.is_displayed() and element.is_enabled():
                        text = element.text.strip()
                        if text:  # Only include elements with visible text
                            clickable_elements.append({
                                "text": text,
                                "selector": f"{selector}:nth-of-type({i+1})",
                                "tag": element.tag_name,
                                "id": element.get_attribute("id") or "",
                                "class": element.get_attribute("class") or "",
                                "href": element.get_attribute("href") or ""
                            })
                except:
                    continue
        
        # Get page title and body text preview
        page_text = driver.find_element(By.TAG_NAME, "body").text[:1000]
        
        result = {
            "url": url,
            "screenshot": screenshot_file,
            "title": driver.title,
            "clickable_elements": clickable_elements,
            "page_text_preview": page_text
        }
        
        # Save analysis
        analysis_file = f"analysis_{int(time.time())}.json"
        with open(analysis_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        # Print results for easy reading
        print(f"📸 Screenshot: {screenshot_file}")
        print(f"📄 Analysis: {analysis_file}")
        print(f"📊 Found {len(clickable_elements)} clickable elements:")
        
        for element in clickable_elements:
            print(f"  • '{element['text'][:50]}' -> {element['selector']}")
        
        return result
        
    finally:
        driver.quit()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python analyze_website.py <URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    analyze_website(url)