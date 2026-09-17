"""Update platform for HACS Private Repo Syncer."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PrivateRepoCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Private Repo Syncer update entities based on a config entry."""
    coordinator: PrivateRepoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for repo_cfg in coordinator.repositories:
        full_repo = repo_cfg.get("repo")
        if full_repo:
            entities.append(PrivateRepoUpdateEntity(coordinator, full_repo))

    async_add_entities(entities)


class PrivateRepoUpdateEntity(CoordinatorEntity[PrivateRepoCoordinator], UpdateEntity):
    """Represents a private repository component update status."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: PrivateRepoCoordinator, full_repo: str) -> None:
        """Initialize the update entity."""
        super().__init__(coordinator)
        self._full_repo = full_repo
        clean_name = full_repo.replace("/", "_").replace("-", "_")
        self._attr_unique_id = f"{DOMAIN}_{clean_name}"
        self._attr_has_entity_name = True

        repo_part = full_repo.split("/", 1)[-1]
        self._attr_name = repo_part.replace("-", " ").replace("_", " ").title()

    @property
    def _repo_data(self) -> Dict[str, Any]:
        """Get coordinator data for this repository."""
        return self.coordinator.data.get(self._full_repo, {}) if self.coordinator.data else {}

    @property
    def installed_version(self) -> Optional[str]:
        """Version installed and currently in use."""
        return self._repo_data.get("installed_version")

    @property
    def latest_version(self) -> Optional[str]:
        """Latest version available for install."""
        return self._repo_data.get("latest_version")

    @property
    def release_url(self) -> Optional[str]:
        """URL to the full release notes of the latest version."""
        release_info = self._repo_data.get("release_info", {})
        return release_info.get("release_url")

    @property
    def release_summary(self) -> Optional[str]:
        """Summary of the release notes or commit message."""
        release_info = self._repo_data.get("release_info", {})
        return release_info.get("release_name")

    async def async_release_notes(self) -> Optional[str]:
        """Return full release notes or commit body."""
        release_info = self._repo_data.get("release_info", {})
        return release_info.get("release_notes") or "No release notes provided."

    async def async_install(
        self, version: Optional[str] = None, backup: bool = False, **kwargs: Any
    ) -> None:
        """Install an update by downloading and extracting the latest version."""
        _LOGGER.info("Installing update for %s (version=%s)", self._full_repo, version)
        ref = version if version and version != "unknown" else None
        await self.coordinator.async_sync_repository(self._full_repo, force=True, ref=ref)
