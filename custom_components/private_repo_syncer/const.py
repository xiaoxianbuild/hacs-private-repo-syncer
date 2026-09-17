"""Constants for HACS Private Repo Syncer."""

DOMAIN = "private_repo_syncer"
NAME = "HACS Private Repo Syncer"
VERSION = "1.0.2"

# Configuration keys
CONF_GITHUB_TOKEN = "github_token"
CONF_REPOSITORIES = "repositories"
CONF_SCAN_INTERVAL = "scan_interval"

# Default values
DEFAULT_SCAN_INTERVAL = 120  # minutes (2 hours)
MIN_SCAN_INTERVAL = 15  # minutes

# Platforms
PLATFORMS = ["update"]

# Services
SERVICE_SYNC = "sync"
SERVICE_CHECK_UPDATES = "check_updates"

# Service parameters
ATTR_REPOSITORY = "repository"
ATTR_FORCE = "force"

# Storage & Cache keys
STORAGE_KEY = f"{DOMAIN}.storage"
STORAGE_VERSION = 1
