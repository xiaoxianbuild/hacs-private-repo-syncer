"""Config flow and Options flow for HACS Private Repo Syncer."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_GITHUB_TOKEN,
    CONF_REPOSITORIES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .github_client import (
    GitHubAuthError,
    GitHubClient,
    GitHubClientError,
    GitHubNotFoundError,
)

_LOGGER = logging.getLogger(__name__)


def _parse_repo_string(raw_text: str) -> List[Dict[str, str]]:
    """Parse newline or comma separated repository URLs/strings into structured dicts.

    Supports:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/tree/branch_name
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - owner/repo@branch_name
    - owner/repo
    """
    lines = [line.strip() for line in raw_text.replace(",", "\n").split("\n")]
    repos: List[Dict[str, str]] = []
    seen = set()

    for line in lines:
        if not line or line.startswith("#"):
            continue

        url_str = line.strip()
        branch = ""
        repo_part = ""

        # SSH format: git@github.com:owner/repo.git
        if url_str.startswith("git@github.com:"):
            url_str = url_str[len("git@github.com:") :]
            if url_str.endswith(".git"):
                url_str = url_str[:-4]
            repo_part = url_str

        # HTTP/HTTPS format: https://github.com/owner/repo[/tree/branch]
        elif url_str.startswith("http://") or url_str.startswith("https://"):
            parsed = urlparse(url_str)
            path = parsed.path.strip("/")
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                owner = parts[0]
                repo = parts[1]
                if repo.endswith(".git"):
                    repo = repo[:-4]
                repo_part = f"{owner}/{repo}"
                if len(parts) >= 4 and parts[2] in ("tree", "blob"):
                    branch = "/".join(parts[3:])
            else:
                repo_part = path

        else:
            # Shorthand format: owner/repo or owner/repo@branch
            if url_str.startswith("github.com/"):
                url_str = url_str[len("github.com/") :]

            if "@" in url_str:
                repo_part, branch = url_str.split("@", 1)
            else:
                repo_part = url_str

            if repo_part.endswith(".git"):
                repo_part = repo_part[:-4]

        repo_part = repo_part.strip()
        branch = branch.strip()

        if "/" in repo_part:
            parts = [p for p in repo_part.split("/") if p]
            if len(parts) >= 2:
                clean_repo = f"{parts[0]}/{parts[1]}"
                unique_key = f"{clean_repo}@{branch}" if branch else clean_repo
                if unique_key not in seen:
                    seen.add(unique_key)
                    repos.append({"repo": clean_repo, "branch": branch})

    return repos


def _format_repos_to_urls(repos: List[Dict[str, str]]) -> str:
    """Format repository dicts into full GitHub URLs for UI display."""
    urls: List[str] = []
    for r in repos:
        repo = r.get("repo", "")
        branch = r.get("branch", "")
        if not repo:
            continue
        if branch:
            urls.append(f"https://github.com/{repo}/tree/{branch}")
        else:
            urls.append(f"https://github.com/{repo}")
    return "\n".join(urls)


class PrivateRepoSyncerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HACS Private Repo Syncer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        super().__init__()
        self._token: Optional[str] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Step 1: Get and validate GitHub Personal Access Token."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_GITHUB_TOKEN].strip()
            session = async_get_clientsession(self.hass)
            client = GitHubClient(session, token)

            try:
                await client.verify_token()
                self._token = token
                return await self.async_step_repositories()
            except GitHubAuthError:
                errors["base"] = "invalid_auth"
            except GitHubClientError as err:
                _LOGGER.error("GitHub connection error during auth verification: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as exc:
                _LOGGER.exception("Unexpected error verifying token: %s", exc)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GITHUB_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_repositories(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Step 2: Add private repository URLs and scan interval."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            raw_repos = user_input.get(CONF_REPOSITORIES, "")
            parsed_repos = _parse_repo_string(raw_repos)

            if not parsed_repos:
                errors["base"] = "no_valid_repositories"
            else:
                # Validate that at least the first repo exists and is accessible
                session = async_get_clientsession(self.hass)
                client = GitHubClient(session, self._token)
                first_repo = parsed_repos[0]["repo"]
                owner, repo = first_repo.split("/", 1)

                try:
                    await client.get_repository_info(owner, repo)
                    return self.async_create_entry(
                        title="Private Repo Syncer",
                        data={
                            CONF_GITHUB_TOKEN: self._token,
                            CONF_REPOSITORIES: parsed_repos,
                            CONF_SCAN_INTERVAL: int(
                                user_input.get(
                                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                                )
                            ),
                        },
                    )
                except GitHubNotFoundError:
                    errors["base"] = "repo_not_found"
                except GitHubAuthError:
                    errors["base"] = "repo_forbidden"
                except Exception as exc:
                    _LOGGER.error("Error validating repository %s: %s", first_repo, exc)
                    errors["base"] = "cannot_connect"

        default_text = "https://github.com/your_username/your_private_integration"
        return self.async_show_form(
            step_id="repositories",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_REPOSITORIES, default=default_text
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow handler."""
        return PrivateRepoSyncerOptionsFlow(config_entry)


class PrivateRepoSyncerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for HACS Private Repo Syncer."""

    def __init__(self, config_entry: Optional[config_entries.ConfigEntry] = None) -> None:
        """Initialize options flow, supporting both legacy and modern HA core."""
        super().__init__()
        self._entry_fallback = config_entry

    @property
    def _active_entry(self) -> config_entries.ConfigEntry:
        """Retrieve config entry safely across different HA versions."""
        if hasattr(self, "config_entry") and self.config_entry is not None:
            return self.config_entry
        return self._entry_fallback

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage configuration options (update token, repos, interval)."""
        errors: Dict[str, str] = {}
        entry = self._active_entry
        entry_data = entry.data if entry else {}
        entry_options = entry.options if entry else {}

        current_token = entry_options.get(
            CONF_GITHUB_TOKEN, entry_data.get(CONF_GITHUB_TOKEN, "")
        )
        current_repos = entry_options.get(
            CONF_REPOSITORIES, entry_data.get(CONF_REPOSITORIES, [])
        )
        current_interval = entry_options.get(
            CONF_SCAN_INTERVAL, entry_data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )

        if user_input is not None:
            new_token = user_input.get(CONF_GITHUB_TOKEN, current_token).strip()
            raw_repos = user_input.get(CONF_REPOSITORIES, "")
            parsed_repos = _parse_repo_string(raw_repos)

            if not parsed_repos:
                errors["base"] = "no_valid_repositories"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_GITHUB_TOKEN: new_token,
                        CONF_REPOSITORIES: parsed_repos,
                        CONF_SCAN_INTERVAL: int(
                            user_input.get(CONF_SCAN_INTERVAL, current_interval)
                        ),
                    },
                )

        # Format repository URLs for user editing
        repo_urls_string = _format_repos_to_urls(current_repos)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GITHUB_TOKEN, default=current_token
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Required(
                        CONF_REPOSITORIES, default=repo_urls_string
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
            errors=errors,
        )
