"""Phase 5 Agents: Autonomous research, monitoring, and form automation."""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class ResearchResult:
    """Result from research agent."""
    query: str
    sources: List[Dict[str, str]]  # [{url, title, snippet}]
    summary: str
    key_findings: List[str]
    timestamp: str


class ResearchAgent:
    """Autonomous web research agent for information gathering."""
    
    def __init__(self):
        """Initialize research agent."""
        self.scrapy_enabled = False
        self.playwright = None
        self.search_results_cache: Dict[str, ResearchResult] = {}
    
    async def initialize(self) -> None:
        """Initialize research dependencies."""
        try:
            from scrapy.crawler import CrawlerProcess
            self.scrapy_enabled = True
            logger.info("Scrapy enabled for web crawling")
        except ImportError:
            logger.warning("Scrapy not available - using Playwright fallback")
        
        try:
            from playwright.async_api import async_playwright
            self.playwright = async_playwright
            logger.info("Playwright initialized for web scraping")
        except ImportError:
            logger.warning("Playwright not available")
    
    async def research_topic(self, topic: str, num_sources: int = 5,
                            use_cache: bool = True) -> ResearchResult:
        """Research a topic using web sources.
        
        Args:
            topic: Topic to research
            num_sources: Number of sources to gather
            use_cache: Whether to use cached results
            
        Returns:
            Research result with findings
        """
        # Check cache first
        cache_key = hashlib.md5(topic.encode()).hexdigest()
        if use_cache and cache_key in self.search_results_cache:
            logger.debug(f"Using cached research for: {topic}")
            return self.search_results_cache[cache_key]
        
        logger.info(f"Researching topic: {topic}")
        
        sources = await self._gather_sources(topic, num_sources)
        summary = await self._summarize_findings(sources)
        findings = await self._extract_key_findings(sources, summary)
        
        result = ResearchResult(
            query=topic,
            sources=sources,
            summary=summary,
            key_findings=findings,
            timestamp=datetime.utcnow().isoformat()
        )
        
        # Cache result
        self.search_results_cache[cache_key] = result
        
        logger.info(f"Research complete: found {len(sources)} sources")
        return result
    
    async def _gather_sources(self, query: str, num_sources: int) -> List[Dict[str, str]]:
        """Gather web sources for a query."""
        sources = []
        
        try:
            # Try DuckDuckGo search as fallback
            import requests
            from bs4 import BeautifulSoup
            
            search_url = f"https://duckduckgo.com/html?q={query}"
            headers = {"User-Agent": "PAI Research Agent"}
            
            response = requests.get(search_url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Parse search results
            for result in soup.find_all('div', class_='result')[:num_sources]:
                link = result.find('a', class_='result__url')
                title = result.find('a', class_='result__link')
                snippet = result.find('a', class_='result__snippet')
                
                if link and title:
                    sources.append({
                        'url': link.get('href', ''),
                        'title': title.text[:100],
                        'snippet': snippet.text[:200] if snippet else ''
                    })
            
            logger.debug(f"Gathered {len(sources)} sources")
            
        except Exception as e:
            logger.warning(f"Source gathering failed: {e}")
        
        return sources
    
    async def _summarize_findings(self, sources: List[Dict[str, str]]) -> str:
        """Summarize findings from sources."""
        if not sources:
            return "No sources found"
        
        # Combine snippets
        combined = " ".join([s.get('snippet', '') for s in sources])
        
        # Simple summary: take first 500 chars
        summary = combined[:500] + "..." if len(combined) > 500 else combined
        
        return summary or "Unable to extract summary"
    
    async def _extract_key_findings(self, sources: List[Dict[str, str]], 
                                    summary: str) -> List[str]:
        """Extract key findings from research."""
        findings = []
        
        # Use first source title as key finding
        if sources:
            findings.append(f"Primary source: {sources[0].get('title', 'Unknown')}")
        
        # Add source count
        findings.append(f"Total sources reviewed: {len(sources)}")
        
        # Add summary snippet
        if summary:
            findings.append(summary[:100])
        
        return findings[:5]


@dataclass
class ChangeDetectionResult:
    """Result from change detection."""
    url: str
    changed: bool
    previous_hash: str
    current_hash: str
    changes: List[str]
    timestamp: str


class MonitoringAgent:
    """Autonomous monitoring agent for website change detection."""
    
    def __init__(self):
        """Initialize monitoring agent."""
        self.monitored_urls: Dict[str, Dict[str, Any]] = {}
        self.change_history: Dict[str, List[ChangeDetectionResult]] = {}
        self.fetch_engine = None
    
    async def initialize(self) -> None:
        """Initialize monitoring dependencies."""
        try:
            import httpx
            self.fetch_engine = httpx.AsyncClient()
            logger.info("Monitoring agent initialized")
        except ImportError:
            logger.warning("httpx not available for monitoring")
    
    async def add_monitor(self, url: str, check_interval: int = 3600) -> None:
        """Add URL to monitoring list.
        
        Args:
            url: URL to monitor
            check_interval: Check interval in seconds
        """
        logger.info(f"Adding URL to monitoring: {url}")
        
        # Get initial content hash
        content = await self._fetch_url_content(url)
        content_hash = hashlib.md5(content.encode()).hexdigest()
        
        self.monitored_urls[url] = {
            "last_check": datetime.utcnow(),
            "check_interval": check_interval,
            "content_hash": content_hash,
            "last_change": None
        }
        
        self.change_history[url] = []
    
    async def check_for_changes(self, url: Optional[str] = None) -> List[ChangeDetectionResult]:
        """Check monitored URLs for changes.
        
        Args:
            url: Specific URL to check (all if None)
            
        Returns:
            List of detected changes
        """
        urls_to_check = [url] if url else self.monitored_urls.keys()
        changes = []
        
        for check_url in urls_to_check:
            if check_url not in self.monitored_urls:
                continue
            
            # Fetch current content
            content = await self._fetch_url_content(check_url)
            current_hash = hashlib.md5(content.encode()).hexdigest()
            previous_hash = self.monitored_urls[check_url]["content_hash"]
            
            if current_hash != previous_hash:
                # Change detected
                detected_changes = self._analyze_changes(content)
                
                result = ChangeDetectionResult(
                    url=check_url,
                    changed=True,
                    previous_hash=previous_hash,
                    current_hash=current_hash,
                    changes=detected_changes,
                    timestamp=datetime.utcnow().isoformat()
                )
                
                changes.append(result)
                self.change_history[check_url].append(result)
                
                # Update stored hash
                self.monitored_urls[check_url]["content_hash"] = current_hash
                self.monitored_urls[check_url]["last_change"] = datetime.utcnow()
                
                logger.info(f"Change detected on {check_url}")
            
            # Update check time
            self.monitored_urls[check_url]["last_check"] = datetime.utcnow()
        
        return changes
    
    async def _fetch_url_content(self, url: str) -> str:
        """Fetch content from URL."""
        if not self.fetch_engine:
            return ""
        
        try:
            response = await self.fetch_engine.get(url, timeout=10.0)
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return ""
    
    def _analyze_changes(self, content: str) -> List[str]:
        """Analyze what changed in content."""
        # Simple analysis: detect new words/patterns
        changes = []
        
        if "new" in content.lower():
            changes.append("New content detected")
        if "update" in content.lower():
            changes.append("Update noticed")
        if "offer" in content.lower():
            changes.append("Offer detected")
        
        return changes[:3] if changes else ["Content modified"]
    
    def get_monitoring_status(self) -> Dict[str, Any]:
        """Get current monitoring status."""
        status = {
            "total_monitored": len(self.monitored_urls),
            "total_changes": sum(len(h) for h in self.change_history.values()),
            "monitored_urls": []
        }
        
        for url, info in self.monitored_urls.items():
            last_change = info.get("last_change")
            status["monitored_urls"].append({
                "url": url,
                "last_checked": info["last_check"].isoformat(),
                "last_changed": last_change.isoformat() if last_change else None,
                "changes_count": len(self.change_history.get(url, []))
            })
        
        return status


@dataclass
class FormSubmission:
    """Record of form submission."""
    url: str
    form_fields: Dict[str, str]
    success: bool
    response: str
    timestamp: str


class FormAutomationAgent:
    """Autonomous agent for intelligent form filling and submission."""
    
    def __init__(self):
        """Initialize form automation agent."""
        self.playwright = None
        self.submission_history: List[FormSubmission] = []
    
    async def initialize(self) -> None:
        """Initialize form automation dependencies."""
        try:
            from playwright.async_api import async_playwright
            self.playwright = async_playwright
            logger.info("Form automation agent initialized")
        except ImportError:
            logger.error("Playwright required for form automation")
            raise
    
    async def analyze_form(self, url: str) -> Dict[str, Any]:
        """Analyze form structure and fields.
        
        Args:
            url: URL containing form
            
        Returns:
            Form structure analysis
        """
        logger.info(f"Analyzing form on {url}")
        
        try:
            async with self.playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.goto(url)
                
                # Find all form fields
                forms = await page.query_selector_all("form")
                form_data = []
                
                for form_idx, form in enumerate(forms):
                    fields = []
                    
                    # Get all input fields
                    inputs = await form.query_selector_all("input")
                    for inp in inputs:
                        field_type = await inp.get_attribute("type")
                        field_name = await inp.get_attribute("name")
                        field_id = await inp.get_attribute("id")
                        
                        fields.append({
                            "type": field_type or "text",
                            "name": field_name,
                            "id": field_id
                        })
                    
                    # Get text areas
                    textareas = await form.query_selector_all("textarea")
                    for ta in textareas:
                        fields.append({
                            "type": "textarea",
                            "name": await ta.get_attribute("name"),
                            "id": await ta.get_attribute("id")
                        })
                    
                    form_data.append({
                        "form_index": form_idx,
                        "fields": fields,
                        "action": await form.get_attribute("action"),
                        "method": await form.get_attribute("method")
                    })
                
                await browser.close()
                
                logger.info(f"Found {len(forms)} form(s) with {sum(len(f['fields']) for f in form_data)} fields")
                return {"forms": form_data}
                
        except Exception as e:
            logger.error(f"Form analysis failed: {e}")
            return {"forms": [], "error": str(e)}
    
    async def fill_and_submit_form(self, url: str, form_index: int, 
                                   field_values: Dict[str, str]) -> FormSubmission:
        """Fill and submit a form.
        
        Args:
            url: URL containing form
            form_index: Index of form to fill
            field_values: Dictionary of field names to values
            
        Returns:
            Submission result
        """
        logger.info(f"Submitting form {form_index} on {url}")
        
        try:
            async with self.playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.goto(url)
                
                # Get form
                forms = await page.query_selector_all("form")
                if form_index >= len(forms):
                    raise ValueError(f"Form index {form_index} out of range")
                
                form = forms[form_index]
                
                # Fill fields
                for field_name, value in field_values.items():
                    try:
                        selector = f"input[name='{field_name}'], textarea[name='{field_name}']"
                        await page.fill(selector, value)
                    except Exception as e:
                        logger.warning(f"Failed to fill field {field_name}: {e}")
                
                # Submit form
                submit_button = await form.query_selector("button[type='submit'], input[type='submit']")
                if submit_button:
                    await submit_button.click()
                else:
                    # Try pressing Enter
                    await page.press(f"form", "Enter")
                
                # Wait for navigation or response
                await page.wait_for_load_state("networkidle", timeout=10000)
                
                # Get response
                response_text = await page.content()
                success = "success" in response_text.lower() or "thank" in response_text.lower()
                
                await browser.close()
                
                submission = FormSubmission(
                    url=url,
                    form_fields=field_values,
                    success=success,
                    response=response_text[:500],
                    timestamp=datetime.utcnow().isoformat()
                )
                
                self.submission_history.append(submission)
                
                logger.info(f"Form submission {'succeeded' if success else 'completed'}")
                return submission
                
        except Exception as e:
            logger.error(f"Form submission failed: {e}")
            return FormSubmission(
                url=url,
                form_fields=field_values,
                success=False,
                response=str(e),
                timestamp=datetime.utcnow().isoformat()
            )
    
    def get_submission_history(self, limit: int = 10) -> List[FormSubmission]:
        """Get submission history.
        
        Args:
            limit: Max results
            
        Returns:
            Recent submissions
        """
        return self.submission_history[-limit:]
