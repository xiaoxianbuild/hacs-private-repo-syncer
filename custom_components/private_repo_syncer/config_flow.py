"""Config flow and Options flow for HACS Private Repo Syncer."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
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
    """Parse newline or comma separated repository strings into structured dicts."""
    lines = [line.strip() for line in raw_text.replace(",", "\n").split("\n")]
    repos: List[Dict[str, str]] = []
    seen = set()

    for line in lines:
        if not line or line.startswith("#"):
            continue

        # Optional format: owner/repo@branch or owner/repo
        branch = ""
        if "@" in line:
            repo_part, branch = line.split("@", 1)
        else:
            repo_part = line

        repo_part = repo_part.strip()
        if "/" in repo_part and repo_part not in seen:
            seen.add(repo_part)
            repos.append({"repo": repo_part, "branch": branch.strip()})

    return repos


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
        """Step 2: Add private repository list and scan interval."""
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

        default_text = "your_username/your_private_integration"
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

        # Convert repo list back to string format for display
        repo_lines = []
        for r in current_repos:
            line = r["repo"]
            if r.get("branch"):
                line += f"@{r['branch']}"
            repo_lines.append(line)
        repo_string = "\n".join(repo_lines)

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
                        CONF_REPOSITORIES, default=repo_string
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
