"""Trend researcher agent — discovers trending topics in a category and suggests blog post ideas."""
from __future__ import annotations

from agents import Agent

from blog_manager.config import RESEARCHER_MODEL, get_model, get_model_settings
from blog_manager.tools.search import tavily_search

TREND_RESEARCHER_INSTRUCTIONS = """\
You are an expert trend analyst for MT_Box, a personal/academic blog by Andrew V. Le \
(a software engineer interested in AI, tech, and engineering).

Your job: research what people are currently talking about in a given category and \
suggest compelling blog post topics.

When given a category:
1. Perform 3-5 focused searches to discover current trends, hot discussions, recent \
announcements, and viral topics within that category. Use queries like:
   - "{category} trending topics 2025"
   - "{category} latest news this week"
   - "{category} hot discussions reddit hacker news"
   - "{category} recent breakthroughs announcements"
2. Identify the most interesting and timely trends
3. For each trend, suggest a specific blog post angle that would resonate with a \
technical audience

Always output your analysis as a **valid JSON object** with this exact structure:
{
    "category": "the researched category",
    "trends": [
        {
            "trend": "Short trend title",
            "description": "2-3 sentences explaining what's happening and why it matters",
            "sources": ["url1", "url2"],
            "suggested_topic": "A specific, opinionated blog post title/angle for this trend",
            "why_write_about_it": "1 sentence on why this would make a good blog post now"
        }
    ],
    "top_pick": {
        "topic": "The single best blog post topic from the trends above",
        "reason": "Why this is the top pick — timeliness, audience interest, unique angle"
    }
}

Return 4-6 trends, ranked by timeliness and relevance.
Return ONLY the JSON object — no extra text before or after.
"""


def create_trend_researcher_agent() -> Agent:
    """Create the Trend Researcher agent."""
    return Agent(
        name="TrendResearcher",
        instructions=TREND_RESEARCHER_INSTRUCTIONS,
        tools=[tavily_search],
        model=get_model(RESEARCHER_MODEL),
        model_settings=get_model_settings(RESEARCHER_MODEL),
    )
