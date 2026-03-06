"""Pydantic models for structured agent outputs."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ImageResult(BaseModel):
    url: str
    description: str = ""


class ResearchResult(BaseModel):
    topic: str
    key_findings: list[str] = Field(description="List of key facts and insights")
    sources: list[str] = Field(description="List of source URLs")
    images: list[ImageResult] = Field(
        default_factory=list, description="Relevant images found"
    )
    summary: str = Field(description="2-3 paragraph summary of research findings")


class BlogPost(BaseModel):
    title: str
    description: str = Field(
        description="Short 1-2 sentence description for meta/frontmatter"
    )
    tags: list[str] = Field(description="List of relevant tags (3-5 tags)")
    content: str = Field(
        description="Full blog post content in Markdown (without frontmatter)"
    )
    images: list[ImageResult] = Field(default_factory=list)
    illustrations: list[ImageResult] = Field(
        default_factory=list, description="AI-generated illustrations"
    )


class CriticReview(BaseModel):
    approved: bool
    overall_score: int = Field(ge=1, le=10, description="Quality score from 1-10")
    strengths: list[str] = Field(description="What works well in the post")
    feedback: list[str] = Field(description="Specific actionable improvement suggestions")
    revised_content: str | None = Field(
        None, description="Optionally provide revised content with minor fixes applied"
    )
