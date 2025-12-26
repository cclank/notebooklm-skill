#!/usr/bin/env python3
"""
Upload Sources to NotebookLM
Supports uploading local files, URLs, and text content to NotebookLM notebooks

Features:
- Upload local files (PDF, TXT, MD, etc.)
- Add URLs (websites, YouTube videos)
- Add pasted text content
- Create new notebooks
- Batch upload from directory
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

from patchright.sync_api import sync_playwright, Page, FileChooser

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, LIBRARY_FILE
from browser_utils import BrowserFactory, StealthUtils
from auth_manager import AuthManager


# NotebookLM UI Selectors (discovered from browser analysis 2024-12)
# Multiple selectors for fallback - tries each in order
SELECTORS = {
    # Dashboard - Create new notebook button
    "create_notebook_button": [
        "button.create-new-button",
        "mat-card.create-new-action-button",
        'button[aria-label="新建笔记本"]',
        'button[aria-label="Create new notebook"]',
        'button[aria-label="Create"]',
    ],
    
    # Inside notebook - Create notebook button (different from dashboard)
    "create_notebook_inside": [
        "button.create-notebook-button",
        'button[aria-label="创建笔记本"]',
    ],
    
    # Notebook title
    "title_label": [
        "span.title-label-inner",
        ".notebook-title",
        'span[class*="title"]',
    ],
    "title_input": [
        "input.title-input",
        'input[aria-label="笔记本标题"]',
        'input[aria-label="Notebook title"]',
    ],
    
    # Add source button (sidebar) - may be covered by modal backdrop
    "add_source_button": [
        "button.add-source-button",
        "button.upload-button",  # Alternative button in center
        'button[aria-label="添加来源"]',
        'button[aria-label="Add source"]',
        'button:has-text("添加来源")',
        'button:has-text("Add source")',
        'button:has-text("上传来源")',  # Alternative text
    ],
    
    # Upload options in modal (dialog may auto-open for new notebooks)
    "upload_file_button": [
        'button:has-text("上传文件")',
        'button:has-text("Upload file")',
        'button:has-text("Upload")',
        'button.drop-zone-icon-button:has-text("上传文件")',
        'button.drop-zone-icon-button:has-text("Upload")',
    ],
    "website_button": [
        'button:has-text("网站")',
        'button:has-text("Website")',
        'button.drop-zone-icon-button:has-text("网站")',
        'button.drop-zone-icon-button:has-text("Website")',
    ],
    "paste_text_button": [
        'button:has-text("复制的文字")',
        'button:has-text("Copied text")',
        'button:has-text("Paste text")',
        'button.drop-zone-icon-button:has-text("复制的文字")',
        'button.drop-zone-icon-button:has-text("Copied text")',
    ],
    
    # Input fields in modal
    "url_input": [
        'textarea[aria-label="输入网址"]',
        'textarea[aria-label="Enter URLs"]',
        'textarea[placeholder*="URL"]',
        'textarea[placeholder*="网址"]',
    ],
    "text_input": [
        'textarea[aria-label="粘贴的文字"]',
        'textarea[aria-label="Copied text"]',
        'textarea[aria-label="Paste text"]',
        'textarea[placeholder*="粘贴"]',
        'textarea[placeholder*="paste"]',
    ],
    
    # Action buttons
    "insert_button": [
        'button:has-text("插入")',
        'button:has-text("Insert")',
        'button[aria-label="插入"]',
        'button[aria-label="Insert"]',
    ],
    "close_modal_button": [
        'button[aria-label="关闭"]',
        'button[aria-label="Close"]',
        'button.close-button',
    ],
    "back_button": [
        'button[aria-label="返回"]',
        'button[aria-label="Back"]',
    ],
    
    # Source list (to verify upload success)
    "source_item": [
        ".source-item",
        ".source-card",
        '[class*="source-item"]',
        '[class*="source-card"]',
    ],
}

# Supported file extensions
SUPPORTED_EXTENSIONS = {
    ".pdf": "PDF document",
    ".txt": "Text file",
    ".md": "Markdown file",
    ".docx": "Word document",
    ".doc": "Word document (legacy)",
}


class UploadManager:
    """
    Manages uploading sources to NotebookLM
    """

    def __init__(self, show_browser: bool = False):
        """
        Initialize the upload manager
        
        Args:
            show_browser: Whether to show browser window during operations
        """
        self.show_browser = show_browser
        self.stealth = StealthUtils()
        self.auth = AuthManager()
        
        # Ensure data directory exists
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _get_selectors(self, key: str) -> List[str]:
        """Get selector list from SELECTORS dict"""
        value = SELECTORS.get(key, [])
        if isinstance(value, str):
            return [value]
        return value

    def _wait_for_page_ready(self, page: Page, timeout: int = 10000):
        """Wait for page to be fully loaded and interactive"""
        try:
            # Wait for network to be idle
            page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
        # Additional delay for dynamic content
        self.stealth.random_delay(1000, 2000)

    def _find_element(self, page: Page, selector_key: str, timeout: int = 10000) -> Optional[str]:
        """
        Try to find an element with multiple selector options
        Returns the selector that worked, or None if not found
        """
        selectors = self._get_selectors(selector_key)
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=timeout // len(selectors), state="visible")
                print(f"    ✓ Found element with: {sel[:50]}...")
                return sel
            except Exception:
                continue
        print(f"    ✗ Could not find element for: {selector_key}")
        return None

    def _click_element(self, page: Page, selector_key: str, timeout: int = 10000) -> bool:
        """Click an element, trying multiple selectors if needed"""
        selectors = self._get_selectors(selector_key)
        
        for sel in selectors:
            try:
                # First wait for the element to be visible
                page.wait_for_selector(sel, timeout=timeout // len(selectors), state="visible")
                element = page.query_selector(sel)
                if element and element.is_visible():
                    print(f"    ✓ Clicking: {sel[:50]}...")
                    self.stealth.realistic_click(page, sel)
                    return True
            except Exception:
                continue
        
        # If all selectors failed, raise with helpful message
        raise Exception(f"Could not find element '{selector_key}'. Tried selectors: {selectors}")

    def create_notebook(self, name: str) -> Dict[str, Any]:
        """
        Create a new notebook in NotebookLM
        
        Args:
            name: Name for the new notebook
            
        Returns:
            Dict with status, notebook_url, and message
        """
        print(f"📓 Creating new notebook: {name}")
        
        if not self.auth.is_authenticated():
            return {"status": "error", "error": "Not authenticated. Run auth_manager.py setup first."}
        
        playwright = None
        context = None
        
        try:
            playwright = sync_playwright().start()
            context = BrowserFactory.launch_persistent_context(
                playwright,
                headless=not self.show_browser
            )
            
            page = context.new_page()
            print("  🌐 Navigating to NotebookLM...")
            page.goto("https://notebooklm.google.com", wait_until="domcontentloaded", timeout=30000)
            
            # Check authentication
            if "accounts.google.com" in page.url:
                raise RuntimeError("Authentication required. Run auth_manager.py setup first.")
            
            # Wait for page to be fully loaded
            print("  ⏳ Waiting for page to load...")
            self._wait_for_page_ready(page)
            
            # Click create button
            print("  📝 Clicking create button...")
            self._click_element(page, "create_notebook_button")
            self.stealth.random_delay(2000, 3000)
            
            # Wait for notebook to be created
            self._wait_for_page_ready(page, timeout=5000)
            
            # Close the add source modal if it opens automatically
            try:
                self._click_element(page, "close_modal_button", timeout=3000)
                self.stealth.random_delay(500, 1000)
            except Exception:
                pass  # Modal might not open automatically
            
            # Click on title to rename
            print("  ✏️ Renaming notebook...")
            try:
                title_selector = self._find_element(page, "title_label", timeout=5000)
                if title_selector:
                    self.stealth.realistic_click(page, title_selector)
                    self.stealth.random_delay(300, 500)
                    
                    # Wait for input and type name
                    input_selector = self._find_element(page, "title_input", timeout=3000)
                    if input_selector:
                        input_element = page.query_selector(input_selector)
                        if input_element:
                            input_element.fill("")  # Clear existing
                            self.stealth.human_type(page, input_selector, name)
                            page.keyboard.press("Enter")
                            self.stealth.random_delay(500, 1000)
            except Exception as e:
                print(f"  ⚠️ Could not rename notebook: {e}")
            
            # Get the notebook URL
            notebook_url = page.url
            
            print(f"  ✅ Notebook created: {notebook_url}")
            
            return {
                "status": "success",
                "notebook_url": notebook_url,
                "name": name,
                "message": f"Created notebook '{name}'"
            }
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return {"status": "error", "error": str(e)}
            
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def upload_files(
        self,
        files: List[str],
        notebook_url: Optional[str] = None,
        notebook_id: Optional[str] = None,
        create_notebook: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload local files to a NotebookLM notebook
        
        Args:
            files: List of file paths to upload
            notebook_url: URL of existing notebook
            notebook_id: ID of notebook from library
            create_notebook: Name for new notebook to create
            
        Returns:
            Dict with status, uploaded files, and any errors
        """
        print(f"📤 Uploading {len(files)} file(s)...")
        
        if not self.auth.is_authenticated():
            return {"status": "error", "error": "Not authenticated. Run auth_manager.py setup first."}
        
        # Validate files
        valid_files = []
        for file_path in files:
            path = Path(file_path)
            if not path.exists():
                print(f"  ⚠️ File not found: {file_path}")
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                print(f"  ⚠️ Unsupported file type: {path.suffix} ({file_path})")
                continue
            valid_files.append(str(path.absolute()))
        
        if not valid_files:
            return {"status": "error", "error": "No valid files to upload"}
        
        # Resolve notebook URL
        target_url = self._resolve_notebook_url(notebook_url, notebook_id)
        
        playwright = None
        context = None
        
        try:
            playwright = sync_playwright().start()
            context = BrowserFactory.launch_persistent_context(
                playwright,
                headless=not self.show_browser
            )
            
            page = context.new_page()
            
            # Create new notebook if requested
            if create_notebook:
                print("  🌐 Navigating to NotebookLM...")
                page.goto("https://notebooklm.google.com", wait_until="domcontentloaded", timeout=30000)
                
                if "accounts.google.com" in page.url:
                    raise RuntimeError("Authentication required.")
                
                self._wait_for_page_ready(page)
                
                print("  📝 Creating notebook...")
                self._click_element(page, "create_notebook_button")
                self.stealth.random_delay(2000, 3000)
                self._wait_for_page_ready(page, timeout=5000)
                
                # Close modal if it opens
                try:
                    self._click_element(page, "close_modal_button", timeout=3000)
                    self.stealth.random_delay(500, 1000)
                except Exception:
                    pass
                
                # Rename
                try:
                    title_selector = self._find_element(page, "title_label", timeout=5000)
                    if title_selector:
                        self.stealth.realistic_click(page, title_selector)
                        self.stealth.random_delay(300, 500)
                        input_selector = self._find_element(page, "title_input", timeout=3000)
                        if input_selector:
                            input_el = page.query_selector(input_selector)
                            if input_el:
                                input_el.fill("")
                                self.stealth.human_type(page, input_selector, create_notebook)
                                page.keyboard.press("Enter")
                                self.stealth.random_delay(500, 1000)
                except Exception as e:
                    print(f"  ⚠️ Could not rename: {e}")
                
                target_url = page.url
                print(f"  📓 Created notebook: {target_url}")
            elif target_url:
                print(f"  🌐 Navigating to notebook...")
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                
                if "accounts.google.com" in page.url:
                    raise RuntimeError("Authentication required.")
                
                self._wait_for_page_ready(page)
            else:
                return {"status": "error", "error": "No notebook specified"}
            
            # Upload each file
            uploaded = []
            likely_uploaded = []
            errors = []
            
            for file_path in valid_files:
                try:
                    result = self._upload_single_file(page, file_path)
                    if result["status"] == "success":
                        uploaded.append(file_path)
                    elif result["status"] == "likely_success":
                        likely_uploaded.append(file_path)
                        print(f"    ⚠️ {result.get('message', 'Check manually')}")
                    else:
                        errors.append({"file": file_path, "error": result.get("error", "Unknown error")})
                except Exception as e:
                    errors.append({"file": file_path, "error": str(e)})
            
            # Build result message
            total_success = len(uploaded) + len(likely_uploaded)
            if total_success > 0:
                status = "success"
                message = f"Uploaded {len(uploaded)}/{len(valid_files)} files"
                if likely_uploaded:
                    message += f" ({len(likely_uploaded)} need manual verification)"
            else:
                status = "error"
                message = "No files were uploaded successfully"
            
            return {
                "status": status,
                "uploaded": uploaded,
                "likely_uploaded": likely_uploaded,
                "errors": errors,
                "notebook_url": target_url,
                "message": message
            }
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return {"status": "error", "error": str(e)}
            
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def _upload_single_file(self, page: Page, file_path: str) -> Dict[str, Any]:
        """Upload a single file to the current notebook"""
        file_name = Path(file_path).name
        print(f"  📄 Uploading: {file_name}")
        
        # Try to click upload_file_button first (dialog may already be open for new notebooks)
        upload_btn_found = False
        try:
            upload_selector = self._find_element(page, "upload_file_button", timeout=3000)
            if upload_selector:
                print("    ✓ Dialog already open")
                upload_btn_found = True
        except Exception:
            pass
        
        # If upload button not found, try opening the add source modal
        if not upload_btn_found:
            print("    → Opening add source dialog...")
            try:
                self._click_element(page, "add_source_button", timeout=5000)
                self.stealth.random_delay(1000, 1500)
            except Exception as e:
                return {"status": "error", "error": f"Could not open add source dialog: {e}"}
        
        # Click upload file button and handle file chooser
        # Increase timeout to 30 seconds for slow connections
        file_selected = False
        try:
            with page.expect_file_chooser(timeout=30000) as fc_info:
                self._click_element(page, "upload_file_button")
            
            file_chooser = fc_info.value
            file_chooser.set_files(file_path)
            file_selected = True
            print(f"    ✓ File selected: {file_name}")
        except Exception as e:
            # File chooser timeout doesn't necessarily mean failure
            # The file might still be uploading in the background
            print(f"    ⚠️ File chooser event timeout (may still be uploading): {e}")
        
        # Wait longer for upload to complete (large files need more time)
        print(f"    ⏳ Waiting for upload to complete...")
        self.stealth.random_delay(8000, 12000)
        
        # Check if file was added (look for source items)
        # Use multiple retries with increasing wait times
        max_retries = 3
        for attempt in range(max_retries):
            source_selector = self._find_element(page, "source_item", timeout=15000)
            if source_selector:
                print(f"    ✅ Upload confirmed - source found")
                return {"status": "success", "file": file_name}
            
            if attempt < max_retries - 1:
                print(f"    ⏳ Waiting... (attempt {attempt + 1}/{max_retries})")
                self.stealth.random_delay(5000, 8000)
        
        # If we selected the file but can't confirm, report as "likely success"
        if file_selected:
            print(f"    ⚠️ Cannot confirm upload, but file was selected - likely successful")
            return {
                "status": "likely_success",
                "file": file_name,
                "message": "File was selected but UI confirmation timed out. Check notebook manually."
            }
        else:
            return {"status": "error", "error": "Upload failed - file was not selected"}

    def add_urls(
        self,
        urls: List[str],
        notebook_url: Optional[str] = None,
        notebook_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add URLs (websites/YouTube) to a NotebookLM notebook
        
        Args:
            urls: List of URLs to add
            notebook_url: URL of existing notebook
            notebook_id: ID of notebook from library
            
        Returns:
            Dict with status and results
        """
        print(f"🔗 Adding {len(urls)} URL(s)...")
        
        if not self.auth.is_authenticated():
            return {"status": "error", "error": "Not authenticated"}
        
        target_url = self._resolve_notebook_url(notebook_url, notebook_id)
        if not target_url:
            return {"status": "error", "error": "No notebook specified"}
        
        playwright = None
        context = None
        
        try:
            playwright = sync_playwright().start()
            context = BrowserFactory.launch_persistent_context(
                playwright,
                headless=not self.show_browser
            )
            
            page = context.new_page()
            print("  🌐 Navigating to notebook...")
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            
            if "accounts.google.com" in page.url:
                raise RuntimeError("Authentication required.")
            
            self._wait_for_page_ready(page)
            
            # Try to click website_button first (dialog may already be open for new notebooks)
            print("  🔗 Looking for website option...")
            website_btn_found = False
            try:
                website_selector = self._find_element(page, "website_button", timeout=3000)
                if website_selector:
                    print("    ✓ Dialog already open, clicking website...")
                    self.stealth.realistic_click(page, website_selector)
                    website_btn_found = True
            except Exception:
                pass
            
            # If website button not found, try opening the add source modal
            if not website_btn_found:
                print("    → Opening add source dialog...")
                try:
                    self._click_element(page, "add_source_button", timeout=5000)
                    self.stealth.random_delay(1000, 1500)
                    
                    # Now click website button
                    self._click_element(page, "website_button")
                except Exception as e:
                    raise Exception(f"Could not open add source dialog: {e}")
            
            self.stealth.random_delay(500, 1000)
            
            # Enter URLs (newline separated)
            print("  📝 Entering URLs...")
            url_text = "\n".join(urls)
            url_input_selector = self._find_element(page, "url_input", timeout=5000)
            if url_input_selector:
                self.stealth.human_type(page, url_input_selector, url_text)
            self.stealth.random_delay(500, 1000)
            
            # Click insert
            print("  📤 Inserting...")
            self._click_element(page, "insert_button")
            
            # Wait for processing
            print("  ⏳ Processing URLs...")
            self.stealth.random_delay(5000, 8000)
            
            print(f"  ✅ Added {len(urls)} URL(s)")
            
            return {
                "status": "success",
                "urls": urls,
                "notebook_url": target_url,
                "message": f"Added {len(urls)} URL(s)"
            }
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return {"status": "error", "error": str(e)}
            
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def add_text(
        self,
        text: str,
        notebook_url: Optional[str] = None,
        notebook_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add pasted text content to a NotebookLM notebook
        
        Args:
            text: Text content to add
            notebook_url: URL of existing notebook
            notebook_id: ID of notebook from library
            
        Returns:
            Dict with status and results
        """
        print(f"📝 Adding text content ({len(text)} chars)...")
        
        if not self.auth.is_authenticated():
            return {"status": "error", "error": "Not authenticated"}
        
        target_url = self._resolve_notebook_url(notebook_url, notebook_id)
        if not target_url:
            return {"status": "error", "error": "No notebook specified"}
        
        playwright = None
        context = None
        
        try:
            playwright = sync_playwright().start()
            context = BrowserFactory.launch_persistent_context(
                playwright,
                headless=not self.show_browser
            )
            
            page = context.new_page()
            print("  🌐 Navigating to notebook...")
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            
            if "accounts.google.com" in page.url:
                raise RuntimeError("Authentication required.")
            
            self._wait_for_page_ready(page)
            
            # Try to click paste_text_button first (dialog may already be open for new notebooks)
            print("  📋 Looking for paste text option...")
            paste_btn_found = False
            try:
                # First check if the paste text button is already visible (dialog auto-opened)
                paste_selector = self._find_element(page, "paste_text_button", timeout=3000)
                if paste_selector:
                    print("    ✓ Dialog already open, clicking paste text...")
                    self.stealth.realistic_click(page, paste_selector)
                    paste_btn_found = True
            except Exception:
                pass
            
            # If paste button not found, try opening the add source modal
            if not paste_btn_found:
                print("    → Opening add source dialog...")
                try:
                    self._click_element(page, "add_source_button", timeout=5000)
                    self.stealth.random_delay(1000, 1500)
                    
                    # Now click paste text button
                    self._click_element(page, "paste_text_button")
                except Exception as e:
                    raise Exception(f"Could not open add source dialog: {e}")
            
            self.stealth.random_delay(500, 1000)
            
            # Enter text content
            print("  📝 Entering text content...")
            text_input_selector = self._find_element(page, "text_input", timeout=5000)
            if text_input_selector:
                # Use fill for large text (faster than human_type)
                text_input = page.query_selector(text_input_selector)
                if text_input:
                    text_input.fill(text)
                    print(f"    ✓ Filled {len(text)} characters")
            else:
                raise Exception("Could not find text input field")
            
            self.stealth.random_delay(500, 1000)
            
            # Click insert
            print("  📤 Inserting...")
            self._click_element(page, "insert_button")
            
            # Wait for processing
            print("  ⏳ Processing text...")
            self.stealth.random_delay(5000, 8000)
            
            print(f"  ✅ Added text content")
            
            return {
                "status": "success",
                "text_length": len(text),
                "notebook_url": target_url,
                "message": f"Added text content ({len(text)} chars)"
            }
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return {"status": "error", "error": str(e)}
            
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def upload_directory(
        self,
        directory: str,
        extensions: Optional[List[str]] = None,
        notebook_url: Optional[str] = None,
        notebook_id: Optional[str] = None,
        create_notebook: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload all matching files from a directory
        
        Args:
            directory: Path to directory
            extensions: List of extensions to include (default: all supported)
            notebook_url: URL of existing notebook
            notebook_id: ID of notebook from library
            create_notebook: Name for new notebook
            
        Returns:
            Dict with status and results
        """
        print(f"📁 Scanning directory: {directory}")
        
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            return {"status": "error", "error": f"Directory not found: {directory}"}
        
        # Determine extensions to look for
        if extensions:
            exts = [f".{e.lstrip('.')}" for e in extensions]
        else:
            exts = list(SUPPORTED_EXTENSIONS.keys())
        
        # Find matching files
        files = []
        for ext in exts:
            files.extend(dir_path.glob(f"*{ext}"))
            files.extend(dir_path.glob(f"*{ext.upper()}"))
        
        # Remove duplicates and sort
        files = sorted(set(str(f) for f in files))
        
        if not files:
            return {"status": "error", "error": f"No matching files found in {directory}"}
        
        print(f"  📄 Found {len(files)} file(s)")
        
        # Upload all files
        return self.upload_files(
            files=files,
            notebook_url=notebook_url,
            notebook_id=notebook_id,
            create_notebook=create_notebook
        )

    def _resolve_notebook_url(
        self,
        notebook_url: Optional[str],
        notebook_id: Optional[str]
    ) -> Optional[str]:
        """Resolve notebook URL from URL or ID"""
        if notebook_url:
            return notebook_url
        
        if notebook_id and LIBRARY_FILE.exists():
            try:
                with open(LIBRARY_FILE, 'r') as f:
                    library = json.load(f)
                    notebooks = library.get("notebooks", [])
                    for nb in notebooks:
                        if nb.get("id") == notebook_id:
                            return nb.get("url")
            except Exception:
                pass
        
        return None


def main():
    """CLI interface for upload manager"""
    parser = argparse.ArgumentParser(description="Upload sources to NotebookLM")
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Create notebook
    create_parser = subparsers.add_parser("create", help="Create a new notebook")
    create_parser.add_argument("--name", required=True, help="Notebook name")
    create_parser.add_argument("--show-browser", action="store_true", help="Show browser")
    
    # Upload files
    upload_parser = subparsers.add_parser("upload", help="Upload files")
    upload_parser.add_argument("--files", required=True, help="Comma-separated file paths")
    upload_parser.add_argument("--notebook-url", help="Target notebook URL")
    upload_parser.add_argument("--notebook-id", help="Target notebook ID from library")
    upload_parser.add_argument("--create-notebook", help="Create new notebook with this name")
    upload_parser.add_argument("--show-browser", action="store_true", help="Show browser")
    
    # Upload directory
    dir_parser = subparsers.add_parser("upload-dir", help="Upload files from directory")
    dir_parser.add_argument("--directory", required=True, help="Directory path")
    dir_parser.add_argument("--extensions", help="Comma-separated extensions (e.g., pdf,md,txt)")
    dir_parser.add_argument("--notebook-url", help="Target notebook URL")
    dir_parser.add_argument("--notebook-id", help="Target notebook ID from library")
    dir_parser.add_argument("--create-notebook", help="Create new notebook with this name")
    dir_parser.add_argument("--show-browser", action="store_true", help="Show browser")
    
    # Add URLs
    url_parser = subparsers.add_parser("add-urls", help="Add website/YouTube URLs")
    url_parser.add_argument("--urls", required=True, help="Comma-separated URLs")
    url_parser.add_argument("--notebook-url", help="Target notebook URL")
    url_parser.add_argument("--notebook-id", help="Target notebook ID from library")
    url_parser.add_argument("--show-browser", action="store_true", help="Show browser")
    
    # Add text
    text_parser = subparsers.add_parser("add-text", help="Add pasted text content")
    text_parser.add_argument("--text", help="Text content (or use --file)")
    text_parser.add_argument("--file", help="Read text from file")
    text_parser.add_argument("--notebook-url", help="Target notebook URL")
    text_parser.add_argument("--notebook-id", help="Target notebook ID from library")
    text_parser.add_argument("--show-browser", action="store_true", help="Show browser")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialize manager
    show_browser = getattr(args, "show_browser", False)
    manager = UploadManager(show_browser=show_browser)
    
    # Execute command
    if args.command == "create":
        result = manager.create_notebook(args.name)
        
    elif args.command == "upload":
        files = [f.strip() for f in args.files.split(",")]
        result = manager.upload_files(
            files=files,
            notebook_url=args.notebook_url,
            notebook_id=args.notebook_id,
            create_notebook=args.create_notebook
        )
        
    elif args.command == "upload-dir":
        extensions = [e.strip() for e in args.extensions.split(",")] if args.extensions else None
        result = manager.upload_directory(
            directory=args.directory,
            extensions=extensions,
            notebook_url=args.notebook_url,
            notebook_id=args.notebook_id,
            create_notebook=args.create_notebook
        )
        
    elif args.command == "add-urls":
        urls = [u.strip() for u in args.urls.split(",")]
        result = manager.add_urls(
            urls=urls,
            notebook_url=args.notebook_url,
            notebook_id=args.notebook_id
        )
        
    elif args.command == "add-text":
        if args.file:
            with open(args.file, 'r', encoding='utf-8') as f:
                text = f.read()
        elif args.text:
            text = args.text
        else:
            print("Error: --text or --file required")
            return
            
        result = manager.add_text(
            text=text,
            notebook_url=args.notebook_url,
            notebook_id=args.notebook_id
        )
    
    # Output result
    print("\n" + "=" * 50)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
