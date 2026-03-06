"""Researcher agent — searches the web for topic information and images."""
from __future__ import annotations

from agents import Agent

from blog_manager.config import RESEARCHER_MODEL, get_model, get_model_settings
from blog_manager.tools.search import tavily_search

RESEARCHER_INSTRUCTIONS = """\
You are an expert research assistant for MT_Box, a personal/academic blog by Andrew V. Le.

Your job: thoroughly research a given topic and compile comprehensive, accurate information.

When given a topic:
1. Perform 2-3 focused searches using different angles (overview, recent developments, examples)
2. Gather key facts, insights, trends, and expert perspectives
3. Find relevant images that could illustrate the blog post
4. Synthesize everything into a structured summary

Always output your research as a **valid JSON object** with this exact structure:
{
    "topic": "the research topic",
    "key_findings": ["finding 1", "finding 2", ...],
    "sources": ["url1", "url2", ...],
    "images": [{"url": "...", "description": "..."}, ...],
    "summary": "2-3 paragraph narrative summary combining all findings"
}

Be thorough but focused. Prioritize high-quality, reliable sources.
Return ONLY the JSON object — no extra text before or after.
"""


def create_researcher_agent() -> Agent:
    """Create the Researcher agent."""
    return Agent(
        name="Researcher",
        instructions=RESEARCHER_INSTRUCTIONS,
        tools=[tavily_search],
        model=get_model(RESEARCHER_MODEL),
        model_settings=get_model_settings(RESEARCHER_MODEL),
    )
