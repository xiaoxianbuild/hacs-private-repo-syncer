"""GitHub REST API client for HACS Private Repo Syncer."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
import aiohttp

_LOGGER = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubClientError(Exception):
    """Base exception for GitHub client errors."""


class GitHubAuthError(GitHubClientError):
    """Raised when authentication fails (HTTP 401/403)."""


class GitHubNotFoundError(GitHubClientError):
    """Raised when repository or reference is not found (HTTP 404)."""


class GitHubRateLimitError(GitHubClientError):
    """Raised when GitHub API rate limit is exceeded."""


class GitHubClient:
    """Asynchronous client for interacting with GitHub REST API."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        """Initialize GitHub client with aiohttp session and personal access token."""
        self._session = session
        self._token = token.strip()

    @property
    def headers(self) -> Dict[str, str]:
        """Build standard headers for GitHub API requests."""
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "HomeAssistant-HACSPrivateRepoSyncer",
        }

    async def _request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        allow_redirects: bool = True,
    ) -> aiohttp.ClientResponse:
        """Internal request helper with error mapping."""
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)

        try:
            resp = await self._session.request(
                method, url, headers=req_headers, allow_redirects=allow_redirects
            )
        except Exception as err:
            raise GitHubClientError(f"Network error connecting to GitHub: {err}") from err

        if resp.status == 401:
            raise GitHubAuthError("Invalid GitHub Personal Access Token.")
        if resp.status == 403:
            # Check if rate limit exceeded
            remaining = resp.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                reset_time = resp.headers.get("x-ratelimit-reset", "unknown")
                raise GitHubRateLimitError(
                    f"GitHub API rate limit exceeded. Resets at {reset_time}."
                )
            raise GitHubAuthError(
                "Access forbidden (403). Ensure token has 'repo' or 'contents:read' permission."
            )
        if resp.status == 404:
            raise GitHubNotFoundError(f"Resource not found on GitHub: {url}")
        if resp.status >= 500:
            raise GitHubClientError(f"GitHub server error: {resp.status}")

        return resp

    async def verify_token(self) -> Dict[str, Any]:
        """Verify token validity by fetching authenticated user or rate limit info."""
        resp = await self._request("GET", f"{GITHUB_API_BASE}/user")
        data = await resp.json()
        return data

    async def get_repository_info(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch general repository information."""
        resp = await self._request("GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}")
        return await resp.json()

    async def get_latest_version_info(
        self, owner: str, repo: str, preferred_branch: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get latest release information, falling back to the latest commit on branch.

        Returns dict containing:
        - version: tag name or short commit SHA
        - commit_sha: full commit SHA
        - type: 'release' or 'commit'
        - release_url: HTML url
        - release_name: title/name
        - release_notes: markdown body
        - published_at: timestamp
        """
        # 1. Try to get latest release
        try:
            resp = await self._request(
                "GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases/latest"
            )
            data = await resp.json()
            return {
                "version": data.get("tag_name", ""),
                "commit_sha": None,
                "type": "release",
                "release_url": data.get("html_url"),
                "release_name": data.get("name") or data.get("tag_name"),
                "release_notes": data.get("body", ""),
                "published_at": data.get("published_at"),
            }
        except GitHubNotFoundError:
            _LOGGER.debug("No releases found for %s/%s, falling back to branch commit", owner, repo)

        # 2. Fallback to latest commit on specified branch or default branch
        target_branch = preferred_branch
        if not target_branch:
            repo_info = await self.get_repository_info(owner, repo)
            target_branch = repo_info.get("default_branch", "main")

        resp = await self._request(
            "GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{target_branch}"
        )
        commit_data = await resp.json()
        sha = commit_data.get("sha", "")
        commit_msg = commit_data.get("commit", {}).get("message", "")
        published_at = commit_data.get("commit", {}).get("author", {}).get("date")

        return {
            "version": sha[:7] if sha else "unknown",
            "commit_sha": sha,
            "type": "commit",
            "branch": target_branch,
            "release_url": commit_data.get("html_url"),
            "release_name": commit_msg.split("\n")[0] if commit_msg else sha[:7],
            "release_notes": commit_msg,
            "published_at": published_at,
        }

    async def download_zipball(
        self, owner: str, repo: str, ref: Optional[str] = None
    ) -> bytes:
        """Download repository archive as zip bytes."""
        ref_path = f"/{ref}" if ref else ""
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/zipball{ref_path}"

        # First request to get redirect or content
        resp = await self._request("GET", url, allow_redirects=True)
        return await resp.read()
