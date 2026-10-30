"""
In-App GitHub Release Update Checker for Warcraft III Lounge.
Polls public GitHub Releases API asynchronously without blocking UI startup.
"""

import json
import threading
from typing import Callable, Optional, Tuple
import urllib.request
from .config import APP_VERSION, logger

GITHUB_REPO = "Blitzkaizer/Warcraft-3-Lounge-Project"

def parse_semver(v_str: str) -> Tuple[int, int, int]:
    """Extracts (major, minor, patch) integer tuple from version string (e.g. 'v1.0.1' or 'v2.0.0-beta' -> (2, 0, 0))."""
    cleaned = v_str.lower().lstrip("v").strip().split("-")[0].split("+")[0]
    parts = cleaned.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)
    except Exception:
        return (0, 0, 0)

def check_for_updates_async(
    on_update_available: Callable[[str, str], None],
    current_version: str = APP_VERSION
) -> None:
    """
    Checks GitHub Releases for new updates in a background thread.
    If a newer version exists, invokes callback on_update_available(latest_tag, release_url).
    """
    def _worker():
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": f"Warcraft3Lounge/{current_version}",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tag_name = data.get("tag_name", "").strip()
                html_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")

                if not tag_name:
                    return

                latest_tuple = parse_semver(tag_name)
                current_tuple = parse_semver(current_version)

                if latest_tuple > current_tuple:
                    logger.info(f"Newer release detected: {tag_name} (Current: v{current_version})")
                    on_update_available(tag_name, html_url)
                else:
                    logger.debug(f"Up to date (Current: v{current_version}, Latest: {tag_name})")

        except Exception as e:
            logger.debug(f"Update checker silent check finished (offline/rate-limited: {e})")

    threading.Thread(target=_worker, daemon=True, name="GitHubUpdateChecker").start()
