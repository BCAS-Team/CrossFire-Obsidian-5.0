import sys
import os

# Add vendor directory to sys.path
VENDOR_DIR = os.path.join(os.path.dirname(__file__), "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

import vendor.requests as vendored_requests
sys.modules['requests'] = vendored_requests

import json
import time
import concurrent.futures as _fut
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any


import requests  # Now automatically points to vendor.requests

from core.config import CROSSFIRE_CACHE
from core.logger import cprint
from core.execution import run_command
from core.progress import ProgressBar
from managers.detection import _detect_installed_managers



@dataclass
class SearchResult:
    name: str
    description: str
    version: str
    manager: str
    homepage: Optional[str] = None
    relevance_score: float = 0.0
    
    def to_dict(self):
        return asdict(self)


class RealSearchEngine:
    def __init__(self):
        self.cache_timeout = 3600  # 1 hour cache
        self.session = requests.Session()
        self.session.timeout = 30
    
    def search(self, query: str, manager: Optional[str] = None, limit: int = 20) -> List[SearchResult]:
        """Search across installed and OS-supported package managers."""
        cprint(f"Searching for '{query}' across available package managers...", "INFO")
        
        all_results = []
        installed = _detect_installed_managers()
        
        # Limit to user-specified manager, otherwise all installed + supported
        if manager:
            target_managers = [manager.lower()] if installed.get(manager.lower()) else []
        else:
            target_managers = [m for m, ok in installed.items() if ok]
        
        if not target_managers:
            cprint("No usable package managers available for searching.", "ERROR")
            return []
        
        # Map managers to their search functions
        manager_funcs = {
            "pip": self._search_pypi,
            "npm": self._search_npm,
            "brew": self._search_brew,
            "apt": self._search_apt,
            "dnf": self._search_dnf,
            "yum": self._search_yum,
            "pacman": self._search_pacman,
            "zypper": self._search_zypper,
            "apk": self._search_apk,
            "choco": self._search_choco,
            "winget": self._search_winget,
            "snap": self._search_snap,
            "flatpak": self._search_flatpak,
        }
        
        with _fut.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_manager = {}
            for mgr in target_managers:
                func = manager_funcs.get(mgr)
                if func:
                    future_to_manager[executor.submit(func, query)] = mgr
            
            progress = ProgressBar(len(future_to_manager), "Searching repositories", "repos")
            for future in _fut.as_completed(future_to_manager, timeout=120):
                mgr = future_to_manager[future]
                try:
                    results = future.result() or []
                    all_results.extend(results)
                    cprint(f"{mgr.upper()}: Found {len(results)} results", "SUCCESS")
                except Exception as e:
                    cprint(f"{mgr.upper()}: Search failed - {e}", "WARNING")
                finally:
                    progress.update()
            progress.finish()
        
        all_results.sort(key=lambda x: x.relevance_score, reverse=True)
        return all_results[:limit]

    # PyPI Search
    def _search_pypi(self, query: str) -> List[SearchResult]:
        try:
            url = f"https://pypi.org/pypi/{query}/json"
            r = self.session.get(url, timeout=10)
            if r.status_code == 200:
                return [self._parse_pypi_info(r.json())]
        except:
            pass
        return []

    def _parse_pypi_info(self, data: Dict[str, Any]) -> SearchResult:
        info = data.get("info", {})
        return SearchResult(
            name=info.get("name", ""),
            description=info.get("summary", "")[:200],
            version=info.get("version", "unknown"),
            manager="pip",
            homepage=info.get("home_page") or info.get("project_url"),
            relevance_score=50
        )

    # NPM Search
    def _search_npm(self, query: str) -> List[SearchResult]:
        try:
            url = "https://registry.npmjs.org/-/v1/search"
            r = self.session.get(url, params={"text": query, "size": 10}, timeout=15)
            if r.status_code != 200:
                return []
            data = r.json()
            results = []
            for obj in data.get("objects", []):
                pkg = obj.get("package", {})
                score = obj.get("score", {}).get("final", 0) * 100
                results.append(SearchResult(
                    name=pkg.get("name", ""),
                    description=pkg.get("description", "")[:200],
                    version=pkg.get("version", "unknown"),
                    manager="npm",
                    homepage=pkg.get("homepage") or pkg.get("repository", {}).get("url"),
                    relevance_score=score
                ))
            return results
        except:
            return []

    # Homebrew Search
    def _search_brew(self, query: str) -> List[SearchResult]:
        try:
            url = "https://formulae.brew.sh/api/formula.json"
            cache_file = CROSSFIRE_CACHE / "brew_formulae.json"
            if cache_file.exists() and (time.time() - cache_file.stat().st_mtime < self.cache_timeout):
                formulae = json.load(open(cache_file))
            else:
                r = self.session.get(url, timeout=20)
                if r.status_code != 200:
                    return []
                formulae = r.json()
                json.dump(formulae, open(cache_file, "w"))
            results = []
            for f in formulae:
                name, desc = f.get("name", ""), f.get("desc", "")
                score = 0
                if query.lower() in name.lower(): score += 50
                if query.lower() in desc.lower(): score += 30
                if score > 0:
                    results.append(SearchResult(
                        name=name, description=desc[:200],
                        version=f.get("versions", {}).get("stable", "unknown"),
                        manager="brew", homepage=f.get("homepage"), relevance_score=score))
            return sorted(results, key=lambda x: x.relevance_score, reverse=True)[:10]
        except:
            return []

    # Linux Package Manager Searches (CLI-based)
    def _search_apt(self, query: str): return self._cli_search(["apt-cache", "search", query], "apt")
    def _search_dnf(self, query: str): return self._cli_search(["dnf", "search", query], "dnf")
    def _search_yum(self, query: str): return self._cli_search(["yum", "search", query], "yum")
    def _search_pacman(self, query: str): return self._cli_search(["pacman", "-Ss", query], "pacman")
    def _search_zypper(self, query: str): return self._cli_search(["zypper", "search", query], "zypper")
    def _search_apk(self, query: str): return self._cli_search(["apk", "search", query], "apk")

    # Windows Package Manager Searches
    def _search_choco(self, query: str): return self._cli_search(["choco", "search", query], "choco")
    def _search_winget(self, query: str): return self._cli_search(["winget", "search", query], "winget")

    # Universal Package Manager Searches
    def _search_snap(self, query: str): return self._cli_search(["snap", "find", query], "snap")
    def _search_flatpak(self, query: str): return self._cli_search(["flatpak", "search", query], "flatpak")

    # Helper method for CLI-based searches
    def _cli_search(self, cmd: List[str], manager: str) -> List[SearchResult]:
        res = run_command(cmd, timeout=30)
        results = []
        if res.ok:
            for line in res.out.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) >= 1:
                    name = parts[0]
                    desc = parts[1] if len(parts) > 1 else ""
                    results.append(SearchResult(
                        name=name, description=desc[:200],
                        version="unknown", manager=manager, relevance_score=5))
        return results[:10]


# Global search engine instance
search_engine = RealSearchEngine()