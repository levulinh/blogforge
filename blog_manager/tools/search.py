"""Tavily web search tool."""
from __future__ import annotations

import json
import os

from agents import function_tool


@function_tool
def tavily_search(query: str, max_results: int = 5) -> str:
    """Search the web for information and images on a topic.

    Returns JSON with search results and relevant images.
    """
    from tavily import TavilyClient

    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return json.dumps({"error": "TAVILY_API_KEY not set", "results": [], "images": []})

    client = TavilyClient(api_key=api_key)
    response = client.search(
        query=query,
        max_results=max_results,
        include_images=True,
        include_image_descriptions=True,
        include_answer=True,
    )

    return json.dumps(
        {
            "query": query,
            "answer": response.get("answer", ""),
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                }
                for r in response.get("results", [])
            ],
            "images": response.get("images", []),
        },
        ensure_ascii=False,
    )
