from typing import Any, Dict, List

from langchain_community.tools import DuckDuckGoSearchRun

_search = DuckDuckGoSearchRun()


def execute(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Search the web and return a concise summary plus raw results."""
    results: List[Dict[str, Any]] = []
    output = _search.invoke(query)
    results.append({"title": "DuckDuckGo", "snippet": output})
    return {
        "query": query,
        "max_results": max_results,
        "results": results,
        "summary": output,
    }