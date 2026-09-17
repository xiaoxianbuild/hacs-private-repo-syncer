"""Home Assistant integration for syncing HACS components from private repositories."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ATTR_FORCE,
    ATTR_REPOSITORY,
    CONF_GITHUB_TOKEN,
    CONF_REPOSITORIES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_CHECK_UPDATES,
    SERVICE_SYNC,
)
from .coordinator import PrivateRepoCoordinator
from .github_client import GitHubClient, GitHubClientError

_LOGGER = logging.getLogger(__name__)

SYNC_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_REPOSITORY): cv.string,
        vol.Optional(ATTR_FORCE, default=False): cv.boolean,
    }
)

CHECK_UPDATES_SERVICE_SCHEMA = vol.Schema({})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HACS Private Repo Syncer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Prefer options over initial data
    token = entry.options.get(CONF_GITHUB_TOKEN, entry.data[CONF_GITHUB_TOKEN])
    repositories = entry.options.get(CONF_REPOSITORIES, entry.data[CONF_REPOSITORIES])
    scan_interval_minutes = entry.options.get(
        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )

    session = async_get_clientsession(hass)
    client = GitHubClient(session, token)

    coordinator = PrivateRepoCoordinator(
        hass=hass,
        client=client,
        repositories=repositories,
        update_interval=timedelta(minutes=scan_interval_minutes),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except GitHubClientError as err:
        raise ConfigEntryNotReady(f"Failed to connect to GitHub: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward entry setups to platforms (e.g. update)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Initial auto-sync: If the component is not yet installed locally, install it immediately
    async def _async_auto_sync_on_setup() -> None:
        for repo_entry in coordinator.repositories:
            full_repo = repo_entry.get("repo")
            if not full_repo:
                continue

            repo_data = coordinator.data.get(full_repo, {})
            # If not installed or missing local folder, download and extract immediately
            if not repo_data.get("installed_version"):
                _LOGGER.info("Initial sync: Auto-downloading and installing %s", full_repo)
                try:
                    await coordinator.async_sync_repository(full_repo, force=True)
                except Exception as exc:
                    _LOGGER.error("Failed to auto-sync %s on setup: %s", full_repo, exc)

    hass.async_create_task(_async_auto_sync_on_setup())

    # Register integration services
    async def async_handle_sync(call: ServiceCall) -> None:
        """Handle the sync service call."""
        target_repo = call.data.get(ATTR_REPOSITORY)
        force = call.data.get(ATTR_FORCE, False)

        repos_to_sync = (
            [target_repo]
            if target_repo
            else [r["repo"] for r in coordinator.repositories]
        )

        for full_repo in repos_to_sync:
            try:
                _LOGGER.info("Manually triggering sync for %s", full_repo)
                await coordinator.async_sync_repository(full_repo, force=force)
            except Exception as exc:
                _LOGGER.error("Manual sync failed for %s: %s", full_repo, exc)

    async def async_handle_check_updates(call: ServiceCall) -> None:
        """Handle check updates service call."""
        _LOGGER.info("Manually requesting refresh for private repo coordinator")
        await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_SYNC):
        hass.services.async_register(
            DOMAIN, SERVICE_SYNC, async_handle_sync, schema=SYNC_SERVICE_SCHEMA
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CHECK_UPDATES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CHECK_UPDATES,
            async_handle_check_updates,
            schema=CHECK_UPDATES_SERVICE_SCHEMA,
        )

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # If no entries left, remove services
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_SYNC)
        hass.services.async_remove(DOMAIN, SERVICE_CHECK_UPDATES)

    return unload_ok
