#!/usr/bin/env python3
"""
Text-Based Element Finder
Usage: python find_by_text.py "text" <URL>
Finds elements by their visible text content
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


def find_elements_by_text(driver, search_text):
    """Find all elements containing the specified text"""
    matches = []
    
    # XPath to find elements containing exact text
    xpath_exact = f"//*[text()='{search_text}']"
    # XPath to find elements containing text (case-insensitive)
    xpath_contains = f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{search_text.lower()}')]"
    
    # Try exact match first
    try:
        elements = driver.find_elements(By.XPATH, xpath_exact)
        for element in elements:
            if element.is_displayed():
                matches.append(("exact", element))
    except:
        pass
    
    # Try contains match
    try:
        elements = driver.find_elements(By.XPATH, xpath_contains)
        for element in elements:
            if element.is_displayed() and search_text.lower() in element.text.lower():
                # Avoid duplicates from exact match
                if not any(match[1] == element for match in matches):
                    matches.append(("contains", element))
    except:
        pass
    
    # Also search in clickable elements specifically
    clickable_selectors = ["a", "button", "[role='button']", "[onclick]", "input[type='button']", "input[type='submit']"]
    
    for selector in clickable_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if (element.is_displayed() and element.text.strip() and 
                    search_text.lower() in element.text.lower()):
                    # Avoid duplicates
                    if not any(match[1] == element for match in matches):
                        matches.append(("clickable", element))
        except:
            continue
    
    return matches


def generate_selectors(element):
    """Generate multiple selector options for an element"""
    selectors = []
    
    # ID selector (highest priority)
    element_id = element.get_attribute("id")
    if element_id:
        selectors.append(f"#{element_id}")
    
    # Class selector
    element_class = element.get_attribute("class")
    if element_class:
        classes = element_class.strip().split()
        if classes:
            selectors.append(f".{classes[0]}")
            if len(classes) > 1:
                selectors.append(f".{'.'.join(classes[:2])}")
    
    # Attribute selectors
    href = element.get_attribute("href")
    if href:
        selectors.append(f"a[href='{href}']")
    
    name = element.get_attribute("name")
    if name:
        selectors.append(f"[name='{name}']")
    
    data_testid = element.get_attribute("data-testid")
    if data_testid:
        selectors.append(f"[data-testid='{data_testid}']")
    
    # Tag-based selectors
    tag = element.tag_name
    selectors.append(f"{tag}")
    
    # Text-based XPath (most reliable for text content)
    text = element.text.strip()
    if text:
        selectors.append(f"//*[text()='{text}']")
        selectors.append(f"//*[contains(text(), '{text}')]")
    
    return selectors


def find_by_text(url, search_text):
    """Find elements by text and provide multiple selector options"""
    print(f"🔍 Searching for '{search_text}' in: {url}")
    
    driver = setup_driver()
    
    try:
        driver.get(url)
        time.sleep(3)
        
        # Take screenshot for reference
        os.makedirs("screenshots", exist_ok=True)
        screenshot_file = f"screenshots/text_search_{int(time.time())}.png"
        driver.save_screenshot(screenshot_file)
        
        # Find matching elements
        matches = find_elements_by_text(driver, search_text)
        
        if not matches:
            print(f"❌ No elements found containing '{search_text}'")
            return None
        
        results = []
        print(f"✅ Found {len(matches)} element(s) containing '{search_text}':")
        
        for i, (match_type, element) in enumerate(matches, 1):
            try:
                selectors = generate_selectors(element)
                
                element_info = {
                    "match_number": i,
                    "match_type": match_type,
                    "tag": element.tag_name,
                    "text": element.text.strip(),
                    "selectors": selectors,
                    "location": {
                        "x": element.location["x"],
                        "y": element.location["y"]
                    },
                    "size": {
                        "width": element.size["width"],
                        "height": element.size["height"]
                    },
                    "attributes": {
                        "id": element.get_attribute("id") or "",
                        "class": element.get_attribute("class") or "",
                        "href": element.get_attribute("href") or "",
                        "role": element.get_attribute("role") or ""
                    }
                }
                
                results.append(element_info)
                
                # Print user-friendly output
                print(f"\n🎯 Match #{i} ({match_type}):")
                print(f"   Text: '{element.text.strip()}'")
                print(f"   Tag: <{element.tag_name}>")
                print(f"   📍 Best selectors:")
                for j, selector in enumerate(selectors[:3], 1):  # Show top 3
                    print(f"      {j}. {selector}")
                
            except Exception as e:
                print(f"   ⚠️  Error analyzing element: {e}")
                continue
        
        # Save detailed results
        result_file = f"text_search_results_{int(time.time())}.json"
        result_data = {
            "search_text": search_text,
            "url": url,
            "screenshot": screenshot_file,
            "matches": results
        }
        
        with open(result_file, 'w') as f:
            json.dump(result_data, f, indent=2)
        
        print(f"\n📁 Files created:")
        print(f"   Screenshot: {screenshot_file}")
        print(f"   Results: {result_file}")
        
        # Provide actionable recommendations
        if results:
            best_match = results[0]
            print(f"\n💡 Recommended selector for '{search_text}':")
            print(f"   {best_match['selectors'][0]}")
            
            print(f"\n🔧 Ready-to-use action:")
            print(f'   {{"type": "click", "selector": "{best_match['selectors'][0]}"}}')
        
        return results
        
    finally:
        driver.quit()


def main():
    if len(sys.argv) != 3:
        print("Usage: python find_by_text.py \"search text\" <URL>")
        print("Example: python find_by_text.py \"Starred\" https://evals-gomail.vercel.app/")
        sys.exit(1)
    
    search_text = sys.argv[1]
    url = sys.argv[2]
    
    find_by_text(url, search_text)


if __name__ == "__main__":
    main()