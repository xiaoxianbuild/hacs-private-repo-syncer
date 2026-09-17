"""Unit tests for repository URL parsing and formatting."""

import unittest
from urllib.parse import urlparse


def parse_repo_string(raw_text: str):
    """Mirror of _parse_repo_string logic."""
    lines = [line.strip() for line in raw_text.replace(",", "\n").split("\n")]
    repos = []
    seen = set()

    for line in lines:
        if not line or line.startswith("#"):
            continue

        url_str = line.strip()
        branch = ""
        repo_part = ""

        # SSH format: git@github.com:owner/repo.git
        if url_str.startswith("git@github.com:"):
            url_str = url_str[len("git@github.com:") :]
            if url_str.endswith(".git"):
                url_str = url_str[:-4]
            repo_part = url_str

        # HTTP/HTTPS format: https://github.com/owner/repo[/tree/branch]
        elif url_str.startswith("http://") or url_str.startswith("https://"):
            parsed = urlparse(url_str)
            path = parsed.path.strip("/")
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                owner = parts[0]
                repo = parts[1]
                if repo.endswith(".git"):
                    repo = repo[:-4]
                repo_part = f"{owner}/{repo}"
                if len(parts) >= 4 and parts[2] in ("tree", "blob"):
                    branch = "/".join(parts[3:])
            else:
                repo_part = path

        else:
            # Shorthand format: owner/repo or owner/repo@branch
            if url_str.startswith("github.com/"):
                url_str = url_str[len("github.com/") :]

            if "@" in url_str:
                repo_part, branch = url_str.split("@", 1)
            else:
                repo_part = url_str

            if repo_part.endswith(".git"):
                repo_part = repo_part[:-4]

        repo_part = repo_part.strip()
        branch = branch.strip()

        if "/" in repo_part:
            parts = [p for p in repo_part.split("/") if p]
            if len(parts) >= 2:
                clean_repo = f"{parts[0]}/{parts[1]}"
                unique_key = f"{clean_repo}@{branch}" if branch else clean_repo
                if unique_key not in seen:
                    seen.add(unique_key)
                    repos.append({"repo": clean_repo, "branch": branch})

    return repos


class TestUrlParsing(unittest.TestCase):
    """Test URL and shorthand repository parsing."""

    def test_full_https_urls(self):
        text = """
        https://github.com/xiaoxianbuild/hacs-private-repo-syncer
        https://github.com/xiaoxianbuild/my-mixed-repo/tree/dev
        https://github.com/another-owner/component.git
        """
        repos = parse_repo_string(text)
        self.assertEqual(len(repos), 3)
        self.assertEqual(repos[0], {"repo": "xiaoxianbuild/hacs-private-repo-syncer", "branch": ""})
        self.assertEqual(repos[1], {"repo": "xiaoxianbuild/my-mixed-repo", "branch": "dev"})
        self.assertEqual(repos[2], {"repo": "another-owner/component", "branch": ""})

    def test_shorthand_and_ssh(self):
        text = """
        xiaoxianbuild/test-repo@feature/v2
        git@github.com:xiaoxianbuild/ssh-repo.git
        github.com/xiaoxianbuild/without-proto
        """
        repos = parse_repo_string(text)
        self.assertEqual(len(repos), 3)
        self.assertEqual(repos[0], {"repo": "xiaoxianbuild/test-repo", "branch": "feature/v2"})
        self.assertEqual(repos[1], {"repo": "xiaoxianbuild/ssh-repo", "branch": ""})
        self.assertEqual(repos[2], {"repo": "xiaoxianbuild/without-proto", "branch": ""})


if __name__ == "__main__":
    unittest.main()
