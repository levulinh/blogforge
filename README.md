# Blog Manager

A multi-agent CLI system that writes and publishes blog posts to your Jekyll blog. Powered by the **OpenAI Agents SDK**, **OpenRouter** (LLM + image generation), and **Tavily** (web search + images).

## Architecture

```
User (topic + instructions)
        │
        ▼
  Orchestrator Agent
        │
        ├──tool──► Researcher Agent   →  Tavily web search (text + images)
        │
        ├──tool──► Writer Agent       →  OpenRouter image generation + blog style reference
        │                                (loops back if Critic requests revisions)
        ├──tool──► Critic Agent       →  quality review (score 1-10, approve at 7+)
        │
        └──tool──► save_blog_post     →  downloads images, writes _posts/YYYY-MM-DD-slug.md
```

All agents and image generation are powered by **OpenRouter** using a single API key.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- API keys for:
  - [OpenRouter](https://openrouter.ai/keys) — LLM + image generation (all AI)
  - [Tavily](https://app.tavily.com) — web search with images

## Setup

```bash
# 1. Clone / navigate to the project
cd blog_manager/

# 2. Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 3. Configure API keys
cp .env.example .env
# Edit .env and fill in your API keys
```

## Usage

### Interactive mode

```bash
python -m blog_manager
```

You'll be prompted for:
1. **Blog topic** — what to write about
2. **Extra instructions** — optional tone/format/style preferences

Example session:
```
📌 Enter your blog topic: The future of agentic AI systems
📝 Extra instructions: casual tone, include code examples, end with a call to action
```

### CLI flags

```bash
# Provide topic and instructions directly
python -m blog_manager --topic "Rust vs Python for data science" --instructions "technical, include benchmarks"

# Dry run — generate but don't save to blog
python -m blog_manager --topic "My topic" --dry-run

# Short flags
python -m blog_manager -t "My topic" -i "casual tone"
```

### Options

| Flag | Short | Description |
|------|-------|-------------|
| `--topic` | `-t` | Blog topic to write about |
| `--instructions` | `-i` | Extra instructions (tone, format, style) |
| `--dry-run` | | Research and write but don't save to blog |

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | ✅ | OpenRouter API key (used for all LLM + image generation) |
| `TAVILY_API_KEY` | ✅ | Tavily search API key |
| `OPENROUTER_MODEL` | ❌ | Override default LLM model (default: `anthropic/claude-3.5-sonnet`) |
| `OPENROUTER_IMAGE_MODEL` | ❌ | Override image generation model (default: `openai/dall-e-3`) |

## Output

Blog posts are saved to `../levulinh.github.io/_posts/YYYY-MM-DD-<slug>.md` with:
- Jekyll frontmatter (layout, title, date, description, tags, giscus_comments)
- Downloaded images in `assets/img/blog/<slug>/`
- Jekyll figure includes for all images

## Development

```bash
# Linting
.venv/bin/ruff check blog_manager/
.venv/bin/ruff format blog_manager/

# Type checking
.venv/bin/ty check blog_manager/
```

## Agents

| Agent | Role | Tools |
|-------|------|-------|
| **Orchestrator** | Coordinates the pipeline | research_topic, write_blog_post, critique_post, save_blog_post |
| **Researcher** | Web research with images | tavily_search |
| **Writer** | Drafts Jekyll Markdown posts | list_blog_posts, read_blog_post, generate_illustration |
| **Critic** | Quality review (score 1-10) | *(none — pure LLM reasoning)* |
