"""Orchestrator agent — coordinates the full blog creation pipeline."""
from __future__ import annotations

from agents import Agent

from blog_manager.config import (
    ORCHESTRATOR_MODEL,
    get_model,
    get_model_settings,
)
from blog_manager.pipeline.critic import create_critic_agent
from blog_manager.pipeline.researcher import create_researcher_agent
from blog_manager.pipeline.writer import create_writer_agent
from blog_manager.tools.blog import save_blog_post

ORCHESTRATOR_INSTRUCTIONS = """\
You are the orchestrator of a blog post production pipeline for MT_Box.

Your pipeline (follow this order strictly):

**Step 1 — RESEARCH**
Call `research_topic` with the user's topic to gather comprehensive information and images.

**Step 2 — WRITE**
Call `write_blog_post` with:
- The research JSON from Step 1
- The user's extra instructions (tone, format, style) if provided

**Step 3 — CRITIQUE**
Call `critique_post` with the draft JSON from Step 2.

**Step 4 — REVISE (if needed)**
If the critique score is < 7 or `approved` is false:
- Call `write_blog_post` again, passing the original research + the critique feedback
- Then call `critique_post` again on the revision
- Maximum 2 revision rounds

**Step 5 — SAVE**
Once the post is approved (score >= 7):
- Extract title, description, tags (space-separated string), content, images, and illustrations
  from the writer's latest JSON output
- If the critic provided `revised_content`, use that as the content instead
- Call `save_blog_post` with all these fields

When calling `save_blog_post`:
- `tags` must be a space-separated string (e.g. "python ai tutorial")
- `images` must be a JSON array string: '[{"url":"...","description":"..."}]'
- `illustrations` must be a JSON array string: '[{"url":"...","description":"..."}]'

Finish by reporting the saved file path and a brief summary of what was published.
"""


def create_orchestrator_agent() -> Agent:
    """Create the Orchestrator agent with all sub-agents wired as tools."""
    researcher = create_researcher_agent()
    writer = create_writer_agent()
    critic = create_critic_agent()

    return Agent(
        name="Orchestrator",
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        tools=[
            researcher.as_tool(
                tool_name="research_topic",
                tool_description=(
                    "Research a topic using web search. "
                    "Pass the topic as a plain string. "
                    "Returns JSON with key_findings, sources, images, and summary."
                ),
            ),
            writer.as_tool(
                tool_name="write_blog_post",
                tool_description=(
                    "Write a Jekyll blog post draft. "
                    "Pass the research JSON and user instructions in a single message. "
                    "Returns JSON with title, description, tags, content, images, illustrations."
                ),
            ),
            critic.as_tool(
                tool_name="critique_post",
                tool_description=(
                    "Critique a blog post draft. "
                    "Pass the blog post JSON and user instructions. "
                    "Returns JSON with approved, overall_score, strengths, feedback."
                ),
            ),
            save_blog_post,
        ],
        model=get_model(ORCHESTRATOR_MODEL),
        model_settings=get_model_settings(ORCHESTRATOR_MODEL),
    )
