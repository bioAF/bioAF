"""Pydantic schemas for GitHub repo management (ADR-043)."""

from datetime import datetime

from pydantic import BaseModel, Field


class GitHubRepoCreateRequest(BaseModel):
    git_ssh_url: str = Field(..., min_length=1, max_length=500)
    display_name: str | None = Field(default=None, max_length=255)


class GitHubRepoResponse(BaseModel):
    id: int
    git_ssh_url: str
    display_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class GitHubRepoListResponse(BaseModel):
    repos: list[GitHubRepoResponse]
    total: int
