"""Syncer module for selectively extracting HACS components from repositories.

This module handles:
1. Selective extraction from mixed/monorepo archives (e.g. Go/Node projects with custom_components).
2. Protection against Zip Slip path traversal vulnerabilities.
3. Reading manifest.json and hacs.json metadata.
4. Safe backup and rollback mechanism during component updates.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import zipfile

_LOGGER = logging.getLogger(__name__)


class SyncerError(Exception):
    """Base exception for syncer operations."""


class ComponentNotFoundError(SyncerError):
    """Raised when no valid HACS custom_components can be located."""


class SecurityError(SyncerError):
    """Raised when an archive contains insecure paths (e.g. Zip Slip)."""


def _is_safe_path(base_dir: str, target_path: str) -> bool:
    """Check if target_path is safely contained within base_dir (Zip Slip defense)."""
    abs_base = os.path.abspath(base_dir)
    abs_target = os.path.abspath(target_path)
    # Ensure abs_target starts with abs_base followed by a path separator or is equal
    return abs_target == abs_base or abs_target.startswith(abs_base + os.sep)


def analyze_archive_structure(zip_file: zipfile.ZipFile) -> List[Dict[str, Any]]:
    """Inspect zip archive to detect custom component locations and metadata.

    Supports:
    1. Mixed/Monorepo structure: `<root>/custom_components/<domain>/...`
    2. Standard repository structure: `custom_components/<domain>/...`
    3. Flat structure with `manifest.json` at repository root.
    """
    namelist = zip_file.namelist()
    detected_components: List[Dict[str, Any]] = []
    seen_domains: Set[str] = set()

    # Step 1: Check for custom_components/<domain>/manifest.json
    for name in namelist:
        parts = [p for p in name.strip("/").split("/") if p]
        if "custom_components" in parts:
            idx = parts.index("custom_components")
            # We expect: ... / custom_components / <domain> / manifest.json
            if len(parts) > idx + 2 and parts[idx + 2] == "manifest.json":
                domain = parts[idx + 1]
                if domain in seen_domains:
                    continue

                # The prefix inside the zip that corresponds to this component
                component_prefix = "/".join(parts[: idx + 2]) + "/"
                manifest_path_in_zip = "/".join(parts[: idx + 3])

                # Read manifest.json from zip to get metadata
                try:
                    with zip_file.open(manifest_path_in_zip) as mf:
                        manifest_data = json.loads(mf.read().decode("utf-8"))
                except Exception as err:
                    _LOGGER.warning("Failed to parse manifest in %s: %s", manifest_path_in_zip, err)
                    manifest_data = {}

                detected_components.append(
                    {
                        "domain": domain,
                        "prefix": component_prefix,
                        "name": manifest_data.get("name", domain),
                        "version": manifest_data.get("version", ""),
                        "manifest": manifest_data,
                        "type": "standard_or_monorepo",
                    }
                )
                seen_domains.add(domain)

    if detected_components:
        return detected_components

    # Step 2: Check for flat repository structure (manifest.json at repository root)
    # Usually GitHub archives have a top-level directory like <repo-name>-<ref>/
    for name in namelist:
        parts = [p for p in name.strip("/").split("/") if p]
        if len(parts) == 1 and parts[0] == "manifest.json":
            manifest_path_in_zip = parts[0]
            component_prefix = ""
        elif len(parts) == 2 and parts[1] == "manifest.json":
            manifest_path_in_zip = "/".join(parts)
            component_prefix = parts[0] + "/"
        else:
            continue

        try:
            with zip_file.open(manifest_path_in_zip) as mf:
                manifest_data = json.loads(mf.read().decode("utf-8"))
                domain = manifest_data.get("domain")
                if domain and domain not in seen_domains:
                    detected_components.append(
                        {
                            "domain": domain,
                            "prefix": component_prefix,
                            "name": manifest_data.get("name", domain),
                            "version": manifest_data.get("version", ""),
                            "manifest": manifest_data,
                            "type": "content_in_root",
                        }
                    )
                    seen_domains.add(domain)
        except Exception as err:
            _LOGGER.warning("Failed to inspect flat manifest %s: %s", manifest_path_in_zip, err)

    return detected_components


def extract_components_from_zip(
    zip_bytes: bytes,
    target_custom_components_dir: str,
    target_domains: Optional[List[str]] = None,
    backup_existing: bool = True,
) -> List[Dict[str, Any]]:
    """Selectively extract only custom component directories from a zip archive.

    Any mixed repository files (e.g. node_modules, Dockerfile, go.mod) are strictly ignored.

    Args:
        zip_bytes: Raw bytes of the downloaded zip archive.
        target_custom_components_dir: Path to `/config/custom_components`.
        target_domains: Optional list of domains to extract. If None, extract all detected.
        backup_existing: Whether to backup the existing component directory before overwriting.

    Returns:
        List of metadata dictionaries for the extracted components.
    """
    os.makedirs(target_custom_components_dir, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        detected = analyze_archive_structure(zf)
        if not detected:
            raise ComponentNotFoundError(
                "No valid HACS custom component found in archive. "
                "Expected either 'custom_components/<domain>/manifest.json' or root 'manifest.json'."
            )

        extracted_results: List[Dict[str, Any]] = []

        for comp in detected:
            domain = comp["domain"]
            if target_domains and domain not in target_domains:
                continue

            prefix = comp["prefix"]
            dest_dir = os.path.join(target_custom_components_dir, domain)
            backup_dir = None

            # Backup existing version if present
            if backup_existing and os.path.exists(dest_dir):
                timestamp = int(time.time())
                backup_dir = os.path.join(
                    target_custom_components_dir, f".backup_{domain}_{timestamp}"
                )
                try:
                    shutil.move(dest_dir, backup_dir)
                    _LOGGER.debug("Backed up %s to %s", dest_dir, backup_dir)
                except Exception as err:
                    _LOGGER.error("Failed to create backup for %s: %s", domain, err)
                    raise SyncerError(f"Backup failed for {domain}: {err}") from err

            temp_dest_dir = dest_dir + f".tmp_{int(time.time())}"
            os.makedirs(temp_dest_dir, exist_ok=True)

            try:
                # Extract only members under the target prefix
                for member in zf.infolist():
                    if not member.filename.startswith(prefix):
                        continue

                    # Relative path within the component directory
                    rel_path = member.filename[len(prefix) :]
                    if not rel_path or rel_path.endswith("/"):
                        continue

                    target_file_path = os.path.join(temp_dest_dir, rel_path)

                    # Security: Defend against Zip Slip
                    if not _is_safe_path(temp_dest_dir, target_file_path):
                        raise SecurityError(
                            f"Illegal path in archive: {member.filename} attempts traversal."
                        )

                    os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
                    with zf.open(member) as source_file, open(target_file_path, "wb") as target_file:
                        shutil.copyfileobj(source_file, target_file)

                # Atomically replace destination
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                shutil.move(temp_dest_dir, dest_dir)

                # Successful extraction: Clean up backup
                if backup_dir and os.path.exists(backup_dir):
                    shutil.rmtree(backup_dir, ignore_errors=True)

                _LOGGER.info("Successfully installed/updated component '%s' into %s", domain, dest_dir)
                extracted_results.append(
                    {
                        "domain": domain,
                        "name": comp["name"],
                        "version": comp["version"],
                        "install_path": dest_dir,
                    }
                )

            except SecurityError as sec_err:
                _LOGGER.error("Security alert for %s, rolling back: %s", domain, sec_err)
                if os.path.exists(temp_dest_dir):
                    shutil.rmtree(temp_dest_dir, ignore_errors=True)
                if backup_dir and os.path.exists(backup_dir):
                    if os.path.exists(dest_dir):
                        shutil.rmtree(dest_dir, ignore_errors=True)
                    shutil.move(backup_dir, dest_dir)
                raise

            except Exception as exc:
                # Rollback on failure
                _LOGGER.error("Extraction failed for %s, rolling back: %s", domain, exc)
                if os.path.exists(temp_dest_dir):
                    shutil.rmtree(temp_dest_dir, ignore_errors=True)
                if backup_dir and os.path.exists(backup_dir):
                    if os.path.exists(dest_dir):
                        shutil.rmtree(dest_dir, ignore_errors=True)
                    shutil.move(backup_dir, dest_dir)
                raise SyncerError(f"Failed to extract component {domain}: {exc}") from exc

        return extracted_results
