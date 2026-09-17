"""Unit tests for the selective syncer engine."""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

# Dynamically load syncer module without triggering package __init__.py (which requires homeassistant)
syncer_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "custom_components",
    "private_repo_syncer",
    "syncer.py",
)
spec = importlib.util.spec_from_file_location("syncer", os.path.abspath(syncer_path))
syncer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(syncer)

ComponentNotFoundError = syncer.ComponentNotFoundError
SecurityError = syncer.SecurityError
extract_components_from_zip = syncer.extract_components_from_zip
analyze_archive_structure = syncer.analyze_archive_structure


class TestSyncer(unittest.TestCase):
    """Test syncer extraction logic and safety checks."""

    def setUp(self) -> None:
        """Create temporary test directory."""
        self.test_dir = tempfile.mkdtemp()
        self.custom_components_dir = os.path.join(self.test_dir, "custom_components")
        os.makedirs(self.custom_components_dir, exist_ok=True)

    def tearDown(self) -> None:
        """Clean up temporary test directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_mock_mixed_zip(self) -> bytes:
        """Create a zip mimicking a mixed Go/Node/HA project structure."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Top-level mixed files (MUST BE IGNORED)
            zf.writestr("myrepo-main/API_EN.md", "# API Doc")
            zf.writestr("myrepo-main/docker-compose.yml", "version: '3'")
            zf.writestr("myrepo-main/Dockerfile", "FROM golang:alpine")
            zf.writestr("myrepo-main/go.mod", "module myrepo")
            zf.writestr("myrepo-main/main.go", "package main\nfunc main() {}")
            zf.writestr("myrepo-main/node_modules/express/index.js", "console.log('ignored');")
            zf.writestr("myrepo-main/package.json", '{"name": "fullstack"}')
            zf.writestr("myrepo-main/pnpm-lock.yaml", "lockfileVersion: 5.4")
            zf.writestr("myrepo-main/internal/server/http.go", "package server")
            zf.writestr("myrepo-main/hacs.json", '{"name": "Bandwagon Usage"}')

            # The actual HA custom_component files (MUST BE EXTRACTED)
            manifest = {
                "domain": "bandwagon_usage",
                "name": "Bandwagon Usage Monitor",
                "version": "1.2.3",
                "codeowners": ["@test"],
                "iot_class": "cloud_polling",
            }
            zf.writestr(
                "myrepo-main/custom_components/bandwagon_usage/manifest.json",
                json.dumps(manifest),
            )
            zf.writestr(
                "myrepo-main/custom_components/bandwagon_usage/__init__.py",
                "# Component Init",
            )
            zf.writestr(
                "myrepo-main/custom_components/bandwagon_usage/sensor.py",
                "# Sensor Platform",
            )
            zf.writestr(
                "myrepo-main/custom_components/bandwagon_usage/translations/zh-Hans.json",
                '{"state": "运行中"}',
            )

        return buf.getvalue()

    def test_mixed_repo_selective_extraction(self) -> None:
        """Test that ONLY the custom_component is extracted from mixed projects."""
        zip_bytes = self._create_mock_mixed_zip()

        results = extract_components_from_zip(
            zip_bytes=zip_bytes,
            target_custom_components_dir=self.custom_components_dir,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["domain"], "bandwagon_usage")
        self.assertEqual(results[0]["version"], "1.2.3")

        dest = os.path.join(self.custom_components_dir, "bandwagon_usage")
        self.assertTrue(os.path.isdir(dest))

        # Check that component files exist
        self.assertTrue(os.path.isfile(os.path.join(dest, "manifest.json")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "__init__.py")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "sensor.py")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "translations", "zh-Hans.json")))

        # Check that mixed project files are NOT extracted
        self.assertFalse(os.path.exists(os.path.join(self.custom_components_dir, "node_modules")))
        self.assertFalse(os.path.exists(os.path.join(self.custom_components_dir, "main.go")))
        self.assertFalse(os.path.exists(os.path.join(self.custom_components_dir, "Dockerfile")))
        self.assertFalse(os.path.exists(os.path.join(dest, "main.go")))
        self.assertFalse(os.path.exists(os.path.join(dest, "node_modules")))

    def test_monorepo_multiple_components(self) -> None:
        """Test extracting multiple custom components from a single monorepo."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "repo-sha/custom_components/plugin_one/manifest.json",
                json.dumps({"domain": "plugin_one", "version": "1.0.0"}),
            )
            zf.writestr("repo-sha/custom_components/plugin_one/__init__.py", "")
            zf.writestr(
                "repo-sha/custom_components/plugin_two/manifest.json",
                json.dumps({"domain": "plugin_two", "version": "2.0.0"}),
            )
            zf.writestr("repo-sha/custom_components/plugin_two/__init__.py", "")

        results = extract_components_from_zip(
            zip_bytes=buf.getvalue(),
            target_custom_components_dir=self.custom_components_dir,
        )

        domains = [r["domain"] for r in results]
        self.assertIn("plugin_one", domains)
        self.assertIn("plugin_two", domains)
        self.assertTrue(
            os.path.isfile(os.path.join(self.custom_components_dir, "plugin_one", "__init__.py"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.custom_components_dir, "plugin_two", "__init__.py"))
        )

    def test_zip_slip_security_prevention(self) -> None:
        """Test that directory traversal (Zip Slip) attacks are blocked."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "malicious-zip/custom_components/evil_plugin/manifest.json",
                json.dumps({"domain": "evil_plugin", "version": "6.6.6"}),
            )
            # Path traversal attempt
            zf.writestr(
                "malicious-zip/custom_components/evil_plugin/../../../../../../etc/passwd",
                "malicious content",
            )

        with self.assertRaises(SecurityError):
            extract_components_from_zip(
                zip_bytes=buf.getvalue(),
                target_custom_components_dir=self.custom_components_dir,
            )

    def test_backup_and_update(self) -> None:
        """Test that updating an existing component works and keeps existing if no failure."""
        dest = os.path.join(self.custom_components_dir, "bandwagon_usage")
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "old_file.txt"), "w") as f:
            f.write("old version")

        # Run extraction of new version
        zip_bytes = self._create_mock_mixed_zip()
        extract_components_from_zip(
            zip_bytes=zip_bytes,
            target_custom_components_dir=self.custom_components_dir,
            backup_existing=True,
        )

        # Dest should have been replaced with new files, old file gone
        self.assertFalse(os.path.exists(os.path.join(dest, "old_file.txt")))
        self.assertTrue(os.path.exists(os.path.join(dest, "manifest.json")))


if __name__ == "__main__":
    unittest.main()
