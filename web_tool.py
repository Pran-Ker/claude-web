#!/usr/bin/env python3
"""
Web Analysis Tool for Claude
Standalone script to analyze websites and extract DOM elements
"""

import sys
import json
import time
from typing import Dict, List, Optional, Any
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup
import base64
import os
import argparse


def setup_driver(headless: bool = True, timeout: int = 10):
    """Setup Chrome driver with appropriate options"""
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.implicitly_wait(timeout)
        return driver
    except Exception as e:
        raise Exception(f"Failed to initialize Chrome driver: {str(e)}")


def analyze_website(url: str, selectors: Optional[List[str]] = None, 
                   screenshot: bool = False, wait_for_element: Optional[str] = None) -> Dict[str, Any]:
    """
    Analyze a website and return DOM elements and optionally a screenshot
    """
    driver = None
    try:
        driver = setup_driver()
        
        # Load the page
        driver.get(url)
        
        # Wait for specific element if provided
        if wait_for_element:
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_element))
                )
            except TimeoutException:
                pass  # Continue anyway
        
        # Give page time to load JavaScript
        time.sleep(3)
        
        # Get page source
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        
        result = {
            'url': url,
            'title': driver.title,
            'text_content': soup.get_text(strip=True),
            'elements': {},
            'screenshot': None
        }
        
        # Extract specific elements if selectors provided
        if selectors:
            for selector in selectors:
                try:
                    elements = soup.select(selector)
                    result['elements'][selector] = [
                        {
                            'tag': elem.name,
                            'text': elem.get_text(strip=True),
                            'attributes': dict(elem.attrs) if elem.attrs else {}
                        }
                        for elem in elements
                    ]
                except Exception as e:
                    result['elements'][selector] = f"Error: {str(e)}"
        
        # Take screenshot if requested
        if screenshot:
            screenshot_path = f"/tmp/screenshot_{int(time.time())}.png"
            driver.save_screenshot(screenshot_path)
            result['screenshot_path'] = screenshot_path
        
        return result
        
    except Exception as e:
        return {
            'error': str(e),
            'url': url
        }
    finally:
        if driver:
            driver.quit()


def main():
    parser = argparse.ArgumentParser(description='Analyze website DOM elements')
    parser.add_argument('url', help='Website URL to analyze')
    parser.add_argument('--selectors', nargs='*', help='CSS selectors to extract')
    parser.add_argument('--screenshot', action='store_true', help='Take screenshot')
    parser.add_argument('--wait-for', help='CSS selector to wait for')
    parser.add_argument('--output', choices=['json', 'text'], default='text', help='Output format')
    
    args = parser.parse_args()
    
    # Default selectors if none provided
    if not args.selectors:
        args.selectors = ['h1', 'h2', 'h3', 'button', 'input', 'nav', 'main', '[data-testid]']
    
    result = analyze_website(
        args.url,
        selectors=args.selectors,
        screenshot=args.screenshot,
        wait_for_element=args.wait_for
    )
    
    if args.output == 'json':
        # Remove screenshot data for JSON output (too large)
        if 'screenshot' in result:
            del result['screenshot']
        print(json.dumps(result, indent=2))
    else:
        # Text output
        if 'error' in result:
            print(f"Error: {result['error']}")
            sys.exit(1)
        
        print(f"Title: {result.get('title', 'N/A')}")
        print(f"URL: {result.get('url', 'N/A')}")
        print(f"Text content length: {len(result.get('text_content', ''))}")
        
        if result.get('screenshot_path'):
            print(f"Screenshot saved: {result['screenshot_path']}")
        
        # Show found elements
        print("\nFound Elements:")
        for selector, elements in result.get('elements', {}).items():
            if isinstance(elements, list) and elements:
                print(f"  {selector}: {len(elements)} elements")
                for i, elem in enumerate(elements[:3]):  # Show first 3
                    text = elem['text'][:100].replace('\n', ' ').strip()
                    attrs = elem.get('attributes', {})
                    attr_str = ""
                    if 'class' in attrs:
                        attr_str += f" class='{attrs['class'][:30]}'"
                    if 'id' in attrs:
                        attr_str += f" id='{attrs['id']}'"
                    print(f"    [{i}] <{elem['tag']}{attr_str}> {text}")
            elif isinstance(elements, str):
                print(f"  {selector}: {elements}")
        
        # Show first 500 characters of text content
        text_content = result.get('text_content', '')
        if text_content:
            print(f"\nFirst 500 chars of page text:")
            print(text_content[:500])


if __name__ == "__main__":
    main()