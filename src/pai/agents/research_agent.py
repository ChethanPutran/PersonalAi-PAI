"""Research agent for web crawling and summarization."""

from typing import Dict, Any, List
from loguru import logger

from pai.agents.base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    """Autonomous research agent."""
    
    def __init__(self, kernel):
        super().__init__("research_agent", kernel)
        self._capabilities = [
            "research.search_web",
            "research.crawl_website",
            "research.summarize",
            "research.monitor_changes"
        ]
    
    async def initialize(self) -> None:
        """Initialize research agent."""
        logger.info("Research agent initialized")
    
    async def process_goal(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Process a research goal."""
        logger.info(f"Research agent processing goal: {goal}")
        
        # Determine research type
        if "search" in goal.lower():
            return await self._search(goal, context)
        elif "crawl" in goal.lower() or "monitor" in goal.lower():
            return await self._crawl(goal, context)
        elif "summarize" in goal.lower():
            return await self._summarize(goal, context)
        else:
            # Default: search and summarize
            search_results = await self._search(goal, context)
            summary = await self._summarize(search_results.get("content", ""), context)
            return {
                "goal": goal,
                "search_results": search_results,
                "summary": summary
            }
    
    async def _search(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Perform web search."""
        # Use browser plugin
        search_query = self._extract_search_query(goal)
        
        # Use browser plugin to search
        browser_result = await self.use_plugin(
            "browser",
            "browser.navigate",
            {"url": f"https://www.google.com/search?q={search_query}"}
        )
        
        # Extract search results
        results = await self.use_plugin(
            "browser",
            "browser.get_text",
            {"selector": "div.g"}
        )
        
        return {
            "query": search_query,
            "results": results,
            "timestamp": context.get("timestamp")
        }
    
    async def _crawl(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Crawl a website."""
        url = context.get("url")
        if not url:
            raise ValueError("URL required for crawling")
        
        await self.use_plugin("browser", "browser.navigate", {"url": url})
        
        # Get page content
        content = await self.use_plugin("browser", "browser.get_text", {"selector": "body"})
        
        # Store in memory
        await self.store_memory("long_term", {
            "type": "crawled_content",
            "url": url,
            "content": content
        })
        
        return {
            "url": url,
            "content_length": len(content),
            "crawled": True
        }
    
    async def _summarize(self, text: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Summarize text content."""
        # This would use an LLM plugin in production
        summary = f"Summary of: {text[:100]}..." if len(text) > 100 else text
        
        return {
            "original_length": len(text),
            "summary": summary,
            "summary_length": len(summary)
        }
    
    def _extract_search_query(self, goal: str) -> str:
        """Extract search query from goal."""
        # Simple extraction - in production use LLM
        if "search for" in goal.lower():
            parts = goal.lower().split("search for")
            query = parts[1].strip()
        else:
            query = goal
        
        return query.replace(" ", "+")
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle events."""
        if event_type == "web.update":
            # Monitor and report website changes
            url = data.get("url")
            logger.info(f"Research agent detected update at {url}")
            await self.store_memory("episodic", {
                "event": "website_updated",
                "url": url,
                "timestamp": data.get("timestamp")
            })