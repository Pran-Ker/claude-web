#!/usr/bin/env python3
"""
Web Action Tool for Claude
Standalone script to perform actions on websites (click, fill, navigate, etc.)
"""

import sys
import json
import time
import argparse
from typing import Dict, List, Optional, Any, Union
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup
import base64
import os


class WebActionTool:
    def __init__(self, headless: bool = False, timeout: int = 10):
        self.timeout = timeout
        self.driver = None
        self.wait = None
        self.setup_driver(headless)
        self.actions = ActionChains(self.driver)
    
    def setup_driver(self, headless: bool = False):
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
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.implicitly_wait(self.timeout)
            self.wait = WebDriverWait(self.driver, self.timeout)
        except Exception as e:
            raise Exception(f"Failed to initialize Chrome driver: {str(e)}")
    
    def navigate_to(self, url: str) -> Dict[str, Any]:
        """Navigate to a URL"""
        try:
            self.driver.get(url)
            time.sleep(2)
            return {
                "action": "navigate",
                "url": url,
                "title": self.driver.title,
                "current_url": self.driver.current_url,
                "success": True
            }
        except Exception as e:
            return {
                "action": "navigate",
                "url": url,
                "error": str(e),
                "success": False
            }
    
    def click_element(self, selector: str, wait_for: bool = True) -> Dict[str, Any]:
        """Click an element by CSS selector"""
        try:
            if wait_for:
                element = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            else:
                element = self.driver.find_element(By.CSS_SELECTOR, selector)
            
            # Scroll to element if needed
            self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
            time.sleep(0.5)
            
            element.click()
            time.sleep(1)
            
            return {
                "action": "click",
                "selector": selector,
                "element_text": element.text,
                "success": True
            }
        except Exception as e:
            return {
                "action": "click",
                "selector": selector,
                "error": str(e),
                "success": False
            }
    
    def fill_input(self, selector: str, text: str, clear: bool = True) -> Dict[str, Any]:
        """Fill an input field"""
        try:
            element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            
            if clear:
                element.clear()
            
            element.send_keys(text)
            time.sleep(0.5)
            
            return {
                "action": "fill",
                "selector": selector,
                "text": text,
                "success": True
            }
        except Exception as e:
            return {
                "action": "fill",
                "selector": selector,
                "error": str(e),
                "success": False
            }
    
    def select_dropdown(self, selector: str, value: str, by: str = "value") -> Dict[str, Any]:
        """Select from dropdown by value, text, or index"""
        try:
            element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            select = Select(element)
            
            if by == "value":
                select.select_by_value(value)
            elif by == "text":
                select.select_by_visible_text(value)
            elif by == "index":
                select.select_by_index(int(value))
            
            return {
                "action": "select",
                "selector": selector,
                "value": value,
                "by": by,
                "success": True
            }
        except Exception as e:
            return {
                "action": "select",
                "selector": selector,
                "error": str(e),
                "success": False
            }
    
    def wait_for_element(self, selector: str, condition: str = "presence") -> Dict[str, Any]:
        """Wait for an element to meet a condition"""
        try:
            if condition == "presence":
                element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            elif condition == "visible":
                element = self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))
            elif condition == "clickable":
                element = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            
            return {
                "action": "wait",
                "selector": selector,
                "condition": condition,
                "element_text": element.text,
                "success": True
            }
        except Exception as e:
            return {
                "action": "wait",
                "selector": selector,
                "condition": condition,
                "error": str(e),
                "success": False
            }
    
    def scroll_to_element(self, selector: str) -> Dict[str, Any]:
        """Scroll to an element"""
        try:
            element = self.driver.find_element(By.CSS_SELECTOR, selector)
            self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
            time.sleep(1)
            
            return {
                "action": "scroll",
                "selector": selector,
                "success": True
            }
        except Exception as e:
            return {
                "action": "scroll",
                "selector": selector,
                "error": str(e),
                "success": False
            }
    
    def press_key(self, key: str, selector: Optional[str] = None) -> Dict[str, Any]:
        """Press a key, optionally on a specific element"""
        try:
            key_obj = getattr(Keys, key.upper())
            
            if selector:
                element = self.driver.find_element(By.CSS_SELECTOR, selector)
                element.send_keys(key_obj)
            else:
                self.actions.send_keys(key_obj).perform()
            
            return {
                "action": "press_key",
                "key": key,
                "selector": selector,
                "success": True
            }
        except Exception as e:
            return {
                "action": "press_key",
                "key": key,
                "selector": selector,
                "error": str(e),
                "success": False
            }
    
    def get_element_text(self, selector: str) -> Dict[str, Any]:
        """Get text from an element"""
        try:
            element = self.driver.find_element(By.CSS_SELECTOR, selector)
            return {
                "action": "get_text",
                "selector": selector,
                "text": element.text,
                "success": True
            }
        except Exception as e:
            return {
                "action": "get_text",
                "selector": selector,
                "error": str(e),
                "success": False
            }
    
    def take_screenshot(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """Take a screenshot"""
        try:
            if not filename:
                # Use screenshots folder in current directory
                screenshots_dir = os.path.join(os.getcwd(), "screenshots")
                os.makedirs(screenshots_dir, exist_ok=True)
                filename = os.path.join(screenshots_dir, f"screenshot_{int(time.time())}.png")
            
            self.driver.save_screenshot(filename)
            
            return {
                "action": "screenshot",
                "filename": filename,
                "success": True
            }
        except Exception as e:
            return {
                "action": "screenshot",
                "error": str(e),
                "success": False
            }
    
    def get_page_info(self) -> Dict[str, Any]:
        """Get current page information"""
        try:
            return {
                "action": "page_info",
                "title": self.driver.title,
                "url": self.driver.current_url,
                "page_source_length": len(self.driver.page_source),
                "success": True
            }
        except Exception as e:
            return {
                "action": "page_info",
                "error": str(e),
                "success": False
            }
    
    def execute_actions(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute a sequence of actions"""
        results = []
        
        for action in actions:
            action_type = action.get("type")
            
            if action_type == "navigate":
                result = self.navigate_to(action["url"])
            elif action_type == "click":
                result = self.click_element(action["selector"], action.get("wait_for", True))
            elif action_type == "fill":
                result = self.fill_input(action["selector"], action["text"], action.get("clear", True))
            elif action_type == "select":
                result = self.select_dropdown(action["selector"], action["value"], action.get("by", "value"))
            elif action_type == "wait":
                result = self.wait_for_element(action["selector"], action.get("condition", "presence"))
            elif action_type == "scroll":
                result = self.scroll_to_element(action["selector"])
            elif action_type == "key":
                result = self.press_key(action["key"], action.get("selector"))
            elif action_type == "get_text":
                result = self.get_element_text(action["selector"])
            elif action_type == "screenshot":
                result = self.take_screenshot(action.get("filename"))
            elif action_type == "page_info":
                result = self.get_page_info()
            elif action_type == "sleep":
                time.sleep(action.get("seconds", 1))
                result = {"action": "sleep", "seconds": action.get("seconds", 1), "success": True}
            else:
                result = {"action": action_type, "error": f"Unknown action type: {action_type}", "success": False}
            
            results.append(result)
            
            # Stop if action failed and stop_on_error is True
            if not result.get("success") and action.get("stop_on_error", False):
                break
        
        return results
    
    def close(self):
        """Close the browser"""
        if self.driver:
            self.driver.quit()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def main():
    parser = argparse.ArgumentParser(description='Perform actions on websites')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode')
    parser.add_argument('--timeout', type=int, default=10, help='Timeout for waiting')
    parser.add_argument('--actions', required=True, help='JSON string or file path with actions to perform')
    parser.add_argument('--output', choices=['json', 'text'], default='text', help='Output format')
    
    args = parser.parse_args()
    
    # Parse actions
    if os.path.isfile(args.actions):
        with open(args.actions, 'r') as f:
            actions = json.load(f)
    elif not args.actions.startswith('[') and not args.actions.startswith('{'):
        # Check if it's a filename in the tasks folder
        tasks_dir = os.path.join(os.getcwd(), "tasks")
        task_file = os.path.join(tasks_dir, args.actions if args.actions.endswith('.json') else f"{args.actions}.json")
        if os.path.isfile(task_file):
            with open(task_file, 'r') as f:
                actions = json.load(f)
        else:
            print(f"Task file not found: {task_file}")
            sys.exit(1)
    else:
        actions = json.loads(args.actions)
    
    # Execute actions
    with WebActionTool(headless=args.headless, timeout=args.timeout) as tool:
        results = tool.execute_actions(actions)
    
    # Output results
    if args.output == 'json':
        print(json.dumps(results, indent=2))
    else:
        for i, result in enumerate(results):
            print(f"Action {i+1}: {result['action']}")
            if result.get('success'):
                print(f"  ✓ Success")
                if 'text' in result:
                    print(f"  Text: {result['text']}")
                if 'filename' in result:
                    print(f"  Screenshot: {result['filename']}")
            else:
                print(f"  ✗ Error: {result.get('error', 'Unknown error')}")
            print()


if __name__ == "__main__":
    main()