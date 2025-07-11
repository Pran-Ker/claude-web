import requests
from urllib.parse import urljoin, urlparse
from web_tool import WebTool
import time


class SiteCrawler:
    def __init__(self, base_url, port=9222):
        self.base_url = base_url
        self.found_urls = set()
        self.crawled_urls = set()
        self.web = WebTool(port)
        self.web.connect()
        
    def get_domain(self, url):
        return urlparse(url).netloc
    
    def crawl_page(self, url):
        """Crawl a single page and extract links"""
        if url in self.crawled_urls:
            return []
        
        print(f"Crawling: {url}")
        self.crawled_urls.add(url)
        
        try:
            # Navigate to the page
            self.web.go(url)
            time.sleep(2)
            
            # Get all links on the page
            links = self.web.js("""
            Array.from(document.querySelectorAll('a[href]')).map(a => a.href)
            """)
            
            # Get any form actions
            form_actions = self.web.js("""
            Array.from(document.querySelectorAll('form[action]')).map(f => f.action)
            """)
            
            # Get current page info
            title = self.web.js("document.title")
            page_text = self.web.js("document.body.textContent")
            
            page_info = {
                'url': url,
                'title': title,
                'text_length': len(page_text) if page_text else 0,
                'has_form': 'form' in page_text.lower() if page_text else False,
                'has_booking': any(word in page_text.lower() for word in ['book', 'confirm', 'payment', 'ride']) if page_text else False
            }
            
            # Filter links to same domain
            same_domain_links = []
            base_domain = self.get_domain(self.base_url)
            
            for link in (links or []):
                if link and self.get_domain(link) == base_domain:
                    same_domain_links.append(link)
                    self.found_urls.add(link)
            
            # Add form actions
            for action in (form_actions or []):
                if action:
                    full_action = urljoin(url, action)
                    if self.get_domain(full_action) == base_domain:
                        same_domain_links.append(full_action)
                        self.found_urls.add(full_action)
            
            return same_domain_links, page_info
            
        except Exception as e:
            print(f"Error crawling {url}: {e}")
            return [], {'url': url, 'error': str(e)}
    
    def crawl_site(self, max_pages=10):
        """Crawl the entire site"""
        print(f"Starting crawl of {self.base_url}")
        
        # Start with base URL
        self.found_urls.add(self.base_url)
        pages_info = []
        
        while len(self.crawled_urls) < max_pages and self.found_urls:
            # Get next URL to crawl
            next_url = next(iter(self.found_urls - self.crawled_urls), None)
            if not next_url:
                break
                
            links, page_info = self.crawl_page(next_url)
            pages_info.append(page_info)
            
            # Add new links to found_urls
            for link in links:
                self.found_urls.add(link)
        
        return pages_info
    
    def get_site_map(self):
        """Get a summary of all found URLs"""
        return {
            'total_found': len(self.found_urls),
            'total_crawled': len(self.crawled_urls),
            'all_urls': sorted(list(self.found_urls)),
            'uncrawled_urls': sorted(list(self.found_urls - self.crawled_urls))
        }
    
    def close(self):
        self.web.close()


# Usage
if __name__ == "__main__":
    crawler = SiteCrawler("https://evals-udriver.vercel.app/")
    
    # Crawl the site
    pages = crawler.crawl_site(max_pages=10)
    
    print("\n" + "="*50)
    print("CRAWL RESULTS")
    print("="*50)
    
    # Show page info
    for page in pages:
        print(f"\nPage: {page['url']}")
        print(f"Title: {page.get('title', 'No title')}")
        print(f"Text length: {page.get('text_length', 0)}")
        print(f"Has booking content: {page.get('has_booking', False)}")
        if 'error' in page:
            print(f"Error: {page['error']}")
    
    # Show site map
    site_map = crawler.get_site_map()
    print(f"\n\nSITE MAP")
    print(f"Total URLs found: {site_map['total_found']}")
    print(f"URLs crawled: {site_map['total_crawled']}")
    print("\nAll URLs:")
    for url in site_map['all_urls']:
        print(f"  {url}")
    
    crawler.close()