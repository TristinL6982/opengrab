import re
import html as html_module
from urllib.parse import urljoin, unquote


def is_dir_index(html: str) -> bool:
    """Check if the HTML page is a directory index page.
    
    A directory index page is identified by having a <title> or first <h1> 
    that starts with "Index of " (case-insensitive, whitespace-trimmed).
    
    Args:
        html: The HTML content as a string
        
    Returns:
        True if the page is a directory index, False otherwise
    """
    # Look for title tag
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    
    # If no title found, look for first h1 tag
    if not title_match:
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
        if h1_match:
            h1_text = h1_match.group(1).strip()
            return h1_text.lower().startswith("index of ")
        return False
    
    # Check title text
    title_text = title_match.group(1).strip()
    return title_text.lower().startswith("index of ")


def parse_dir_index(html: str, base_url: str) -> list[tuple[str, str]]:
    """Parse a directory index HTML page to extract file and directory links.
    
    Args:
        html: The HTML content as a string
        base_url: The base URL of the directory listing
        
    Returns:
        List of (absolute_url, name) tuples in page order without duplicates
    """
    # Find all links in the page
    href_pattern = re.compile(r'<a[^>]*href=(["\'])(.*?)\1', re.IGNORECASE)
    matches = href_pattern.findall(html)
    
    # Get all href values (unescaped)
    hrefs = [html_module.unescape(href) for _, href in matches]
    
    # Filter and process links
    result = []
    seen_urls = set()
    
    # Calculate base directory URL (up to the last '/')
    if base_url.endswith('/'):
        base_dir_url = base_url
    else:
        # Remove the filename part to get directory
        base_dir_url = '/'.join(base_url.split('/')[:-1]) + '/'
    
    for href in hrefs:
        # Skip empty hrefs, ones starting with ? or #, or special paths
        if not href or href.startswith('?') or href.startswith('#') or \
           href == "../" or href == ".." or \
           href.startswith("mailto:") or href.startswith("javascript:"):
            continue
            
        # Resolve relative URL
        absolute_url = urljoin(base_url, href)
        
        # Skip if it's not within the base directory
        if not absolute_url.startswith(base_dir_url):
            continue
        
        # Skip base directory itself  
        if absolute_url == base_dir_url:
            continue

        # Extract name from URL (last non-empty path segment)
        # Normalize URL path by removing trailing '/' for path processing
        normalized_url = absolute_url.rstrip('/')
        path_parts = [part for part in normalized_url.split('/') if part]
        
        # If the URL contains a hostname, we want the last part after the last '/'
        if len(path_parts) > 1:
            name = path_parts[-1] 
        else:
            name = path_parts[0] if path_parts else ""
            
        # Re-add trailing '/' for directories
        if href.endswith('/'):
            name += '/'
        
        name = unquote(name)
        
        # Avoid duplicates (keep first occurrence)
        if absolute_url not in seen_urls:
            seen_urls.add(absolute_url)
            result.append((absolute_url, name))
    
    return result


def split_entries(entries) -> tuple[list, list]:
    """Split entries into files and subdirectories.
    
    Args:
        entries: List of (absolute_url, name) tuples
        
    Returns:
        Tuple of (files, subdirs) lists
    """
    files = []
    subdirs = []
    
    for url, name in entries:
        # Check if the entry is a directory by looking at trailing slash in name
        if name.endswith('/'):
            subdirs.append((url, name))
        else:
            files.append((url, name))
            
    return (files, subdirs)