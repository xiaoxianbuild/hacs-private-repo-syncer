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
        notification_id = f"synced_{full_repo.replace('/', '_')}"

        # Find repository configuration
        repo_cfg = {}
        for r in self.repositories:
            if r.get("repo") == full_repo:
                repo_cfg = r
                break

        target_type = repo_cfg.get("target_type", "release")
        target_value = repo_cfg.get("target_value") or repo_cfg.get("branch") or "latest"

        # Determine reference if not explicitly passed
        if not ref:
            if target_type in ("branch", "tag") and target_value:
                ref = target_value
            elif target_type == "release":
                if target_value and target_value != "latest":
                    ref = target_value
                else:
                    repo_data = self.data.get(full_repo, {}) if self.data else {}
                    latest_ver = repo_data.get("latest_version")
                    if latest_ver and latest_ver != "unknown":
                        ref = latest_ver

        _LOGGER.info(
            "Starting sync for repository %s (target=%s:%s, ref=%s, force=%s)",
            full_repo,
            target_type,
            target_value,
            ref,
            force,
        )

        try:
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

            # 3. Refresh coordinator data to get updated version & commit details
            await self.async_refresh()

            # 4. Construct rich notification message
            repo_data = self.data.get(full_repo, {}) if self.data else {}
            release_info = repo_data.get("release_info", {})
            extracted_domains = ", ".join(f"`{item['domain']}`" for item in extracted)

            details_lines: List[str] = []
            if target_type == "branch":
                branch_name = release_info.get("branch") or target_value
                sha = release_info.get("commit_sha") or release_info.get("version", "unknown")
                short_sha = sha[:7] if sha and sha != "unknown" else "unknown"
                raw_msg = release_info.get("release_notes") or release_info.get("release_name") or ""
                commit_msg = raw_msg.strip().split("\n")[0] if raw_msg else "无提交说明"
                details_lines.append(f"- 🌿 **分支 (Branch)**: `{branch_name}`")
                details_lines.append(f"- 🔖 **提交 (Commit)**: `{short_sha}`")
                details_lines.append(f"- 📝 **提交说明**: {commit_msg}")
            elif target_type == "tag":
                tag_name = release_info.get("version") or target_value
                details_lines.append(f"- 🏷️ **标签 (Tag)**: `{tag_name}`")
            else:  # release
                rel_ver = release_info.get("version") or target_value
                rel_name = release_info.get("release_name") or rel_ver
                if rel_name != rel_ver:
                    details_lines.append(f"- 🚀 **版本 (Release)**: `{rel_ver}` ({rel_name})")
                else:
                    details_lines.append(f"- 🚀 **版本 (Release)**: `{rel_ver}`")

            details_str = "\n".join(details_lines)

            message = (
                f"### 🎉 插件同步成功！\n\n"
                f"- 📦 **仓库**: `{full_repo}`\n"
                f"{details_str}\n"
                f"- 📁 **已安装组件**: {extracted_domains}\n\n"
                f"> ⚠️ **请重启 Home Assistant** 以使更新后的组件代码生效。"
            )

            persistent_notification.async_create(
                self.hass,
                message=message,
                title=f"✅ [同步成功] {full_repo}",
                notification_id=notification_id,
            )

            return extracted

        except ComponentNotFoundError as exc:
            _LOGGER.error("No valid HACS component found in %s: %s", full_repo, exc)
            message = (
                f"### ❌ 插件同步失败！\n\n"
                f"- 📦 **仓库**: `{full_repo}`\n"
                f"- 🎯 **目标**: `{target_type}: {target_value}`\n"
                f"- ⚠️ **失败原因**: 仓库中未找到合法的自定义组件！\n\n"
                f"> **排查建议**：\n"
                f"> 1. 请确认仓库中包含 `custom_components/<domain>/manifest.json` 或根目录 `manifest.json`；\n"
                f"> 2. 检查选定的分支或标签中是否已提交该文件。"
            )
            persistent_notification.async_create(
                self.hass,
                message=message,
                title=f"❌ [同步失败] {full_repo}",
                notification_id=notification_id,
            )
            raise

        except GitHubClientError as exc:
            _LOGGER.error("GitHub API error during sync for %s: %s", full_repo, exc)
            message = (
                f"### ❌ 插件同步失败！\n\n"
                f"- 📦 **仓库**: `{full_repo}`\n"
                f"- 🎯 **目标**: `{target_type}: {target_value}`\n"
                f"- ⚠️ **GitHub 错误**: {exc}\n\n"
                f"> **排查建议**：\n"
                f"> 1. 请确认 GitHub Personal Access Token 是否具有读取该私有仓库的权限；\n"
                f"> 2. 检查网络连接是否正常。"
            )
            persistent_notification.async_create(
                self.hass,
                message=message,
                title=f"❌ [同步失败] {full_repo}",
                notification_id=notification_id,
            )
            raise

        except Exception as exc:
            _LOGGER.exception("Unexpected sync failure for %s: %s", full_repo, exc)
            message = (
                f"### ❌ 插件同步失败！\n\n"
                f"- 📦 **仓库**: `{full_repo}`\n"
                f"- 🎯 **目标**: `{target_type}: {target_value}`\n"
                f"- ⚠️ **异常详情**: {exc}\n\n"
                f"> 请查看 Home Assistant 后台系统日志获取完整错误堆栈。"
            )
            persistent_notification.async_create(
                self.hass,
                message=message,
                title=f"❌ [同步失败] {full_repo}",
                notification_id=notification_id,
            )
            raise
