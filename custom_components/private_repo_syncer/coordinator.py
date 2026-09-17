"""DataUpdateCoordinator for HACS Private Repo Syncer."""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import os
from typing import Any, Dict, List, Optional

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .github_client import GitHubClient, GitHubClientError
from .syncer import ComponentNotFoundError, extract_components_from_zip

_LOGGER = logging.getLogger(__name__)


class PrivateRepoCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator to manage polling and synchronization of private repositories."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: GitHubClient,
        repositories: List[Dict[str, Any]],
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self.repositories = repositories
        self.custom_components_dir = hass.config.path("custom_components")

    def get_installed_component_version(self, domain: str) -> Optional[str]:
        """Read installed component version from its local manifest.json."""
        manifest_path = os.path.join(self.custom_components_dir, domain, "manifest.json")
        if not os.path.isfile(manifest_path):
            return None

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("version")
        except Exception as err:
            _LOGGER.warning("Failed to read local manifest for %s: %s", domain, err)
            return None

    def scan_installed_components(self) -> Dict[str, str]:
        """Scan all currently installed custom components and their versions."""
        installed = {}
        if not os.path.exists(self.custom_components_dir):
            return installed

        for entry in os.listdir(self.custom_components_dir):
            full_path = os.path.join(self.custom_components_dir, entry)
            if os.path.isdir(full_path):
                ver = self.get_installed_component_version(entry)
                if ver is not None:
                    installed[entry] = ver
        return installed

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch latest version info for all monitored repositories."""
        data: Dict[str, Any] = {}
        installed_map = await self.hass.async_add_executor_job(self.scan_installed_components)

        for repo_entry in self.repositories:
            full_repo = repo_entry.get("repo", "")
            if "/" not in full_repo:
                continue

            owner, repo = full_repo.split("/", 1)
            target_type = repo_entry.get("target_type", "release")
            target_value = repo_entry.get("target_value") or repo_entry.get("branch") or None
            target_domain = repo_entry.get("target_domain")

            try:
                remote_info = await self.client.get_latest_version_info(
                    owner, repo, target_type=target_type, target_value=target_value
                )
                latest_version = remote_info.get("version", "unknown")

                # Determine installed version
                inferred_domain = target_domain or repo.replace("-", "_").lower()
                installed_version = installed_map.get(inferred_domain)

                # Check if update is available
                update_available = (
                    installed_version is not None
                    and latest_version != "unknown"
                    and installed_version != latest_version
                )

                data[full_repo] = {
                    "owner": owner,
                    "repo": repo,
                    "full_name": full_repo,
                    "target_type": target_type,
                    "target_value": target_value,
                    "target_domain": inferred_domain,
                    "latest_version": latest_version,
                    "installed_version": installed_version,
                    "update_available": update_available,
                    "release_info": remote_info,
                }
            except GitHubClientError as err:
                _LOGGER.warning("Error fetching info for %s: %s", full_repo, err)
                if self.data and full_repo in self.data:
                    data[full_repo] = self.data[full_repo]
                else:
                    data[full_repo] = {
                        "owner": owner,
                        "repo": repo,
                        "full_name": full_repo,
                        "target_type": target_type,
                        "target_value": target_value,
                        "target_domain": target_domain or repo.replace("-", "_").lower(),
                        "latest_version": "unknown",
                        "installed_version": None,
                        "update_available": False,
                        "error": str(err),
                    }
            except Exception as exc:
                _LOGGER.exception("Unexpected error updating repository %s: %s", full_repo, exc)

        return data

    async def async_sync_repository(
        self, full_repo: str, force: bool = False, ref: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Download and extract component(s) from a repository archive."""
        if "/" not in full_repo:
            raise ValueError(f"Invalid repository identifier: {full_repo}")

        owner, repo = full_repo.split("/", 1)

        # Determine reference if not explicitly passed
        if not ref:
            for r in self.repositories:
                if r.get("repo") == full_repo:
                    t_type = r.get("target_type")
                    t_val = r.get("target_value") or r.get("branch")
                    if t_type in ("branch", "tag") and t_val:
                        ref = t_val
                    elif t_type == "release" and t_val and t_val != "latest":
                        ref = t_val
                    break

        _LOGGER.info("Starting sync for repository %s (ref=%s, force=%s)", full_repo, ref, force)

        # 1. Download zipball from GitHub
        zip_bytes = await self.client.download_zipball(owner, repo, ref=ref)

        # 2. Extract in executor thread
        def _extract() -> List[Dict[str, Any]]:
            return extract_components_from_zip(
                zip_bytes=zip_bytes,
                target_custom_components_dir=self.custom_components_dir,
                backup_existing=True,
            )

        extracted = await self.hass.async_add_executor_job(_extract)

        # 3. Refresh coordinator data
        await self.async_refresh()

        # 4. Notify user to restart Home Assistant
        extracted_domains = ", ".join(item["domain"] for item in extracted)
        persistent_notification.async_create(
            self.hass,
            message=(
                f"Repository **{full_repo}** has been synced successfully!\n\n"
                f"Updated component(s): `{extracted_domains}`.\n\n"
                f"Please **restart Home Assistant** for the code changes to take effect."
            ),
            title="Private Repo Synced",
            notification_id=f"synced_{full_repo.replace('/', '_')}",
        )

        return extracted
