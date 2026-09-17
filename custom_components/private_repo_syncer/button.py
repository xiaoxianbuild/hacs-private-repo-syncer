"""Button platform for HACS Private Repo Syncer."""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
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
    """Set up Private Repo Syncer button entities based on a config entry."""
    coordinator: PrivateRepoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for repo_cfg in coordinator.repositories:
        full_repo = repo_cfg.get("repo")
        if full_repo:
            entities.append(PrivateRepoSyncButton(coordinator, full_repo))
            entities.append(PrivateRepoCheckUpdatesButton(coordinator, full_repo))

    async_add_entities(entities)


class PrivateRepoBaseButton(CoordinatorEntity[PrivateRepoCoordinator], ButtonEntity):
    """Base button entity for a private repository."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PrivateRepoCoordinator, full_repo: str) -> None:
        """Initialize base button."""
        super().__init__(coordinator)
        self._full_repo = full_repo

    @property
    def _repo_data(self) -> Dict[str, Any]:
        """Get repository data from coordinator."""
        return self.coordinator.data.get(self._full_repo, {}) if self.coordinator.data else {}

    @property
    def device_info(self) -> DeviceInfo:
        """Link button to the repository device."""
        target_type = self._repo_data.get("target_type", "release")
        return DeviceInfo(
            identifiers={(DOMAIN, self._full_repo)},
            name=self._full_repo,
            manufacturer="GitHub Private",
            model=f"HACS Syncer ({target_type})",
            configuration_url=f"https://github.com/{self._full_repo}",
        )


class PrivateRepoSyncButton(PrivateRepoBaseButton):
    """Button to manually trigger sync and installation of the private component."""

    _attr_icon = "mdi:cloud-download-outline"
    _attr_translation_key = "sync_now"

    def __init__(self, coordinator: PrivateRepoCoordinator, full_repo: str) -> None:
        """Initialize sync button."""
        super().__init__(coordinator, full_repo)
        clean_name = full_repo.replace("/", "_").replace("-", "_")
        self._attr_unique_id = f"{DOMAIN}_{clean_name}_sync_now"
        self._attr_name = "Sync Now"

    async def async_press(self) -> None:
        """Handle button press: manually download and install/update component."""
        _LOGGER.info("Manual sync triggered via button for %s", self._full_repo)
        await self.coordinator.async_sync_repository(self._full_repo, force=True)


class PrivateRepoCheckUpdatesButton(PrivateRepoBaseButton):
    """Button to manually check for newer releases/commits from GitHub."""

    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "check_updates"

    def __init__(self, coordinator: PrivateRepoCoordinator, full_repo: str) -> None:
        """Initialize check updates button."""
        super().__init__(coordinator, full_repo)
        clean_name = full_repo.replace("/", "_").replace("-", "_")
        self._attr_unique_id = f"{DOMAIN}_{clean_name}_check_updates"
        self._attr_name = "Check Updates"

    async def async_press(self) -> None:
        """Handle button press: query GitHub for newer version info."""
        _LOGGER.info("Manual check updates triggered via button for %s", self._full_repo)
        await self.coordinator.async_request_refresh()
