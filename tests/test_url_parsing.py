"""Unit tests for repository URL parsing and target extraction."""

import unittest
from urllib.parse import urlparse


def parse_github_repo_url(url_str: str):
    """Mirror of parse_github_repo_url in config_flow.py."""
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


class TestUrlParsing(unittest.TestCase):
    """Test URL and shorthand repository parsing."""

    def test_standard_urls(self):
        cases = [
            ("https://github.com/xiaoxianbuild/repo_a", ("xiaoxianbuild", "repo_a")),
            ("https://github.com/xiaoxianbuild/repo_b.git", ("xiaoxianbuild", "repo_b")),
            ("https://github.com/xiaoxianbuild/repo_c/tree/dev", ("xiaoxianbuild", "repo_c")),
            ("git@github.com:xiaoxianbuild/repo_d.git", ("xiaoxianbuild", "repo_d")),
            ("xiaoxianbuild/repo_e", ("xiaoxianbuild", "repo_e")),
            ("xiaoxianbuild/repo_f@main", ("xiaoxianbuild", "repo_f")),
        ]
        for inp, expected in cases:
            self.assertEqual(parse_github_repo_url(inp), expected, f"Failed for {inp}")

    def test_invalid_urls(self):
        invalid = ["", "invalid", "https://google.com/foo", "https://github.com/"]
        for inp in invalid:
            res = parse_github_repo_url(inp)
            self.assertTrue(res is None or len(res) != 2 or not res[1], f"Should fail for {inp}")


if __name__ == "__main__":
    unittest.main()
