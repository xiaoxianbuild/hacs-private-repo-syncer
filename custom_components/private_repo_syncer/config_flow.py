"""Config flow and Options flow for HACS Private Repo Syncer."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
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
    CONF_REPOSITORY_URL,
    CONF_SCAN_INTERVAL,
    CONF_TARGET_SELECTION,
    CONF_TARGET_TYPE,
    CONF_TARGET_VALUE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    TARGET_TYPE_BRANCH,
    TARGET_TYPE_RELEASE,
    TARGET_TYPE_TAG,
)
from .github_client import (
    GitHubAuthError,
    GitHubClient,
    GitHubClientError,
    GitHubNotFoundError,
)

_LOGGER = logging.getLogger(__name__)


def parse_github_repo_url(url_str: str) -> Optional[Tuple[str, str]]:
    """Parse a GitHub repository URL into (owner, repo).

    Accepts:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo/tree/branch
    - git@github.com:owner/repo.git
    - owner/repo
    """
    clean = url_str.strip()
    if not clean:
        return None

    if clean.startswith("git@github.com:"):
        clean = clean[len("git@github.com:") :]
        if clean.endswith(".git"):
            clean = clean[:-4]
        parts = [p for p in clean.split("/") if p]
        return (parts[0], parts[1]) if len(parts) >= 2 else None

    if clean.startswith("http://") or clean.startswith("https://"):
        parsed = urlparse(clean)
        path = parsed.path.strip("/")
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            repo = parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]
            return (parts[0], repo)
        return None

    if clean.startswith("github.com/"):
        clean = clean[len("github.com/") :]

    parts = [p for p in clean.split("/") if p]
    if len(parts) >= 2:
        repo = parts[1]
        if "@" in repo:
            repo = repo.split("@", 1)[0]
        if repo.endswith(".git"):
            repo = repo[:-4]
        return (parts[0], repo)

    return None


class PrivateRepoSyncerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HACS Private Repo Syncer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        super().__init__()
        self._token: Optional[str] = None
        self._repo_url: Optional[str] = None
        self._owner: Optional[str] = None
        self._repo: Optional[str] = None
        self._repo_info: Dict[str, Any] = {}
        self._releases: List[Dict[str, Any]] = []
        self._tags: List[Dict[str, Any]] = []
        self._branches: List[Dict[str, Any]] = []

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Step 1: Input GitHub Personal Access Token and Repository URL."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_GITHUB_TOKEN].strip()
            raw_url = user_input[CONF_REPOSITORY_URL].strip()

            parsed = parse_github_repo_url(raw_url)
            if not parsed:
                errors["repository_url"] = "invalid_repo_url"
            else:
                owner, repo = parsed
                session = async_get_clientsession(self.hass)
                client = GitHubClient(session, token)

                try:
                    # Validate Token and Repository accessibility
                    repo_info = await client.get_repository_info(owner, repo)

                    self._token = token
                    self._repo_url = f"https://github.com/{owner}/{repo}"
                    self._owner = owner
                    self._repo = repo
                    self._repo_info = repo_info

                    # Proceed to Step 2: Select Branch / Tag / Release
                    return await self.async_step_target()

                except GitHubAuthError:
                    errors["base"] = "invalid_auth"
                except GitHubNotFoundError:
                    errors["repository_url"] = "repo_not_found"
                except GitHubClientError as err:
                    _LOGGER.error("GitHub connection error verifying %s/%s: %s", owner, repo, err)
                    errors["base"] = "cannot_connect"
                except Exception as exc:
                    _LOGGER.exception("Unexpected error verifying repository: %s", exc)
                    errors["base"] = "unknown"

        default_token = self._token or ""
        default_url = self._repo_url or "https://github.com/owner/repo"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GITHUB_TOKEN, default=default_token
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Required(
                        CONF_REPOSITORY_URL, default=default_url
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_target(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Step 2: Choose whether to track Release, Tag, or Branch."""
        errors: Dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        client = GitHubClient(session, self._token)

        # Fetch releases, tags, and branches from GitHub
        if not self._releases and not self._tags and not self._branches:
            try:
                self._releases = await client.get_releases(self._owner, self._repo)
                self._tags = await client.get_tags(self._owner, self._repo)
                self._branches = await client.get_branches(self._owner, self._repo)
            except Exception as exc:
                _LOGGER.error("Failed to query target options for %s/%s: %s", self._owner, self._repo, exc)
                errors["base"] = "cannot_connect"

        # Build selection options
        options: List[Dict[str, str]] = []

        # 1. Releases
        options.append(
            {
                "value": "release:latest",
                "label": "🚀 Latest Release (推荐：跟随最新正式发布)",
            }
        )
        for rel in self._releases[:10]:
            tag = rel.get("tag_name", "")
            name = rel.get("name") or tag
            options.append(
                {
                    "value": f"release:{tag}",
                    "label": f"📦 Release: {tag} - {name}",
                }
            )

        # 2. Tags
        for tag in self._tags[:10]:
            name = tag.get("name", "")
            options.append(
                {
                    "value": f"tag:{name}",
                    "label": f"🏷️ Tag: {name}",
                }
            )

        # 3. Branches
        default_branch = self._repo_info.get("default_branch", "main")
        options.append(
            {
                "value": f"branch:{default_branch}",
                "label": f"🌿 Branch: {default_branch} (默认分支代码)",
            }
        )
        for br in self._branches[:15]:
            b_name = br.get("name", "")
            if b_name != default_branch:
                options.append(
                    {
                        "value": f"branch:{b_name}",
                        "label": f"🌿 Branch: {b_name}",
                    }
                )

        if user_input is not None:
            selection = user_input.get(CONF_TARGET_SELECTION, "release:latest")
            scan_interval = int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

            target_type = TARGET_TYPE_RELEASE
            target_value = "latest"

            if ":" in selection:
                prefix, val = selection.split(":", 1)
                target_type = prefix
                target_value = val

            full_repo = f"{self._owner}/{self._repo}"
            entry_title = full_repo

            return self.async_create_entry(
                title=entry_title,
                data={
                    CONF_GITHUB_TOKEN: self._token,
                    CONF_REPOSITORY_URL: self._repo_url,
                    "owner": self._owner,
                    "repo": self._repo,
                    CONF_TARGET_TYPE: target_type,
                    CONF_TARGET_VALUE: target_value,
                    CONF_SCAN_INTERVAL: scan_interval,
                    CONF_REPOSITORIES: [
                        {
                            "repo": full_repo,
                            "target_type": target_type,
                            "target_value": target_value,
                            "branch": target_value if target_type == TARGET_TYPE_BRANCH else "",
                        }
                    ],
                },
            )

        return self.async_show_form(
            step_id="target",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TARGET_SELECTION, default="release:latest"
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
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
        """Initialize options flow."""
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
        """Manage repository target branch/tag/release and poll interval."""
        errors: Dict[str, str] = {}
        entry = self._active_entry
        entry_data = entry.data if entry else {}
        entry_options = entry.options if entry else {}

        current_token = entry_options.get(
            CONF_GITHUB_TOKEN, entry_data.get(CONF_GITHUB_TOKEN, "")
        )
        owner = entry_data.get("owner", "")
        repo = entry_data.get("repo", "")
        current_target_type = entry_options.get(
            CONF_TARGET_TYPE, entry_data.get(CONF_TARGET_TYPE, TARGET_TYPE_RELEASE)
        )
        current_target_value = entry_options.get(
            CONF_TARGET_VALUE, entry_data.get(CONF_TARGET_VALUE, "latest")
        )
        current_interval = entry_options.get(
            CONF_SCAN_INTERVAL, entry_data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )

        session = async_get_clientsession(self.hass)
        client = GitHubClient(session, current_token)

        releases = await client.get_releases(owner, repo) if owner and repo else []
        tags = await client.get_tags(owner, repo) if owner and repo else []
        branches = await client.get_branches(owner, repo) if owner and repo else []

        options: List[Dict[str, str]] = [
            {
                "value": "release:latest",
                "label": "🚀 Latest Release (推荐：跟随最新正式发布)",
            }
        ]
        for rel in releases[:10]:
            t = rel.get("tag_name", "")
            name = rel.get("name") or t
            options.append({"value": f"release:{t}", "label": f"📦 Release: {t} - {name}"})

        for tag in tags[:10]:
            name = tag.get("name", "")
            options.append({"value": f"tag:{name}", "label": f"🏷️ Tag: {name}"})

        for br in branches[:15]:
            b_name = br.get("name", "")
            options.append({"value": f"branch:{b_name}", "label": f"🌿 Branch: {b_name}"})

        current_selection = f"{current_target_type}:{current_target_value}"
        # If current selection not in options, append it
        if not any(opt["value"] == current_selection for opt in options):
            options.insert(1, {"value": current_selection, "label": f"Current: {current_selection}"})

        if user_input is not None:
            new_token = user_input.get(CONF_GITHUB_TOKEN, current_token).strip()
            selection = user_input.get(CONF_TARGET_SELECTION, current_selection)
            new_interval = int(user_input.get(CONF_SCAN_INTERVAL, current_interval))

            target_type = TARGET_TYPE_RELEASE
            target_value = "latest"
            if ":" in selection:
                target_type, target_value = selection.split(":", 1)

            full_repo = f"{owner}/{repo}"
            return self.async_create_entry(
                title="",
                data={
                    CONF_GITHUB_TOKEN: new_token,
                    CONF_TARGET_TYPE: target_type,
                    CONF_TARGET_VALUE: target_value,
                    CONF_SCAN_INTERVAL: new_interval,
                    CONF_REPOSITORIES: [
                        {
                            "repo": full_repo,
                            "target_type": target_type,
                            "target_value": target_value,
                            "branch": target_value if target_type == TARGET_TYPE_BRANCH else "",
                        }
                    ],
                },
            )

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
                        CONF_TARGET_SELECTION, default=current_selection
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
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
