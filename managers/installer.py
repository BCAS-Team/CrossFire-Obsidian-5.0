import re
import sys
from typing import Tuple, List, Optional

from core.config import MANAGER_SETUP, _os_type
from core.logger import cprint, LOG
from core.execution import run_command, RunResult
from core.database import package_db
from .detection import _detect_installed_managers, _manager_human, _ordered_install_manager_candidates
from .commands import INSTALL_HANDLERS, REMOVE_HANDLERS


def _extract_package_version(output: str, manager: str) -> str:
    """Extract version info from installation output."""
    try:
        if manager == "pip":
            # Look for "Successfully installed package-version"
            match = re.search(r"Successfully installed .* (\S+)-(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(2)
        elif manager == "npm":
            # Look for version in npm output
            match = re.search(r"@(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
        elif manager in ["apt", "dnf", "yum"]:
            # Look for version in package manager output
            match = re.search(r"(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
    except:
        pass
    return "installed"


def install_manager(manager: str) -> bool:
    """Attempt to install a package manager if supported on this OS."""
    manager = manager.lower()
    info = MANAGER_SETUP.get(manager)
    if not info:
        cprint(f"Manager '{manager}' not supported.", "ERROR")
        return False
    
    os_name = _os_type()
    if os_name not in info["os"]:
        cprint(f"{manager} is not supported on this OS ({os_name}).", "ERROR")
        return False
    
    # Check if already installed
    if _detect_installed_managers().get(manager, False):
        cprint(f"{_manager_human(manager)} is already installed.", "SUCCESS")
        return True
    
    cmd = info.get("install_cmd")
    install_msg = info.get("install")
    
    if cmd:
        cprint(f"Installing {_manager_human(manager)}...", "INFO")
        
        # Handle Python-based installations
        if manager == "pip":
            full_cmd = [sys.executable] + cmd
        else:
            full_cmd = cmd
            
        result = run_command(full_cmd, timeout=900, show_progress=True)
        if result.ok:
            cprint(f"Successfully installed {_manager_human(manager)}", "SUCCESS")
            return True
        else:
            cprint(f"Failed to install {_manager_human(manager)}: {result.err}", "ERROR")
            return False
    else:
        cprint(f"Manual installation required for {_manager_human(manager)}:", "WARNING")
        cprint(f"  {install_msg}", "INFO")
        return False


def install_package(pkg: str, preferred_manager: Optional[str] = None) -> Tuple[bool, List[Tuple[str, RunResult]]]:
    """Install a package using available managers with enhanced progress tracking."""
    cprint(f"Preparing to install: {pkg}", "INFO")
    installed = _detect_installed_managers()
    
    if not any(installed.values()):
        cprint("No supported package managers are available on this system.", "ERROR")
        return (False, [])
    
    attempts: List[Tuple[str, RunResult]] = []
    candidates = _ordered_install_manager_candidates(pkg, installed)

    if preferred_manager:
        pm = preferred_manager.lower()
        if pm in INSTALL_HANDLERS and installed.get(pm):
            # Move preferred manager to front
            candidates = [pm] + [m for m in candidates if m != pm]
        else:
            available_managers = [m for m, avail in installed.items() if avail]
            cprint(f"Warning: --manager '{preferred_manager}' not available. Available: {', '.join(available_managers)}", "WARNING")

    if not candidates:
        cprint("No package managers available for installation.", "ERROR")
        return (False, [])

    cprint("Installation plan:", "CYAN")
    for i, m in enumerate(candidates, 1):
        cprint(f"  {i}. {_manager_human(m)}", "MUTED")

    for i, manager in enumerate(candidates, 1):
        cmd_builder = INSTALL_HANDLERS.get(manager)
        if not cmd_builder:
            continue
            
        try:
            cmd = cmd_builder(pkg)
            cprint(f"Attempt {i}/{len(candidates)}: Installing via {_manager_human(manager)}...", "INFO")
            
            # Use longer timeout for installations with progress tracking
            res = run_command(cmd, timeout=1800, retries=0, show_progress=True)
            attempts.append((manager, res))
            
            if res.ok:
                # Extract version and record installation
                version = _extract_package_version(res.out, manager)
                package_db.add_package(pkg, version, manager, ' '.join(cmd))
                
                cprint(f"Successfully installed '{pkg}' via {_manager_human(manager)}", "SUCCESS")
                return (True, attempts)
            else:
                # Show more helpful error messages
                err_msg = (res.err or res.out).strip()
                if err_msg:
                    # Get the last few lines of error output
                    error_lines = err_msg.splitlines()
                    relevant_error = error_lines[-1] if error_lines else "Unknown error"
                    if len(relevant_error) > 180:
                        relevant_error = relevant_error[:177] + "..."
                    cprint(f"{_manager_human(manager)} failed: {relevant_error}", "WARNING")
                else:
                    cprint(f"{_manager_human(manager)} failed with no error message", "WARNING")
                    
        except Exception as e:
            err_result = RunResult(False, -1, "", str(e))
            attempts.append((manager, err_result))
            cprint(f"{_manager_human(manager)} failed with exception: {str(e)}", "WARNING")

    cprint(f"Failed to install '{pkg}' with all available managers.", "ERROR")
    return (False, attempts)


def remove_package(pkg: str, manager: Optional[str] = None) -> Tuple[bool, List[Tuple[str, RunResult]]]:
    """Remove a package using available managers with enhanced UI."""
    cprint(f"Preparing to remove: {pkg}", "INFO")
    installed = _detect_installed_managers()
    
    if not any(installed.values()):
        cprint("No supported package managers are available on this system.", "ERROR")
        return (False, [])
    
    attempts: List[Tuple[str, RunResult]] = []
    
    if manager:
        if manager.lower() in REMOVE_HANDLERS and installed.get(manager.lower()):
            candidates = [manager.lower()]
        else:
            cprint(f"Manager '{manager}' not available for package removal", "ERROR")
            return (False, [])
    else:
        # Try managers in order of likelihood
        candidates = _ordered_install_manager_candidates(pkg, installed)
        # Filter to only those that support removal
        candidates = [m for m in candidates if m in REMOVE_HANDLERS]

    if not candidates:
        cprint("No package managers available for package removal.", "ERROR")
        return (False, [])

    for mgr in candidates:
        cmd_builder = REMOVE_HANDLERS.get(mgr)
        if not cmd_builder:
            continue
            
        try:
            cmd = cmd_builder(pkg)
            cprint(f"Attempting removal via {_manager_human(mgr)}...", "INFO")
            
            res = run_command(cmd, timeout=600, retries=0, show_progress=True)
            attempts.append((mgr, res))
            
            if res.ok:
                # Remove from database
                package_db.remove_package(pkg, mgr)
                
                cprint(f"Removed '{pkg}' via {_manager_human(mgr)}", "SUCCESS")
                return (True, attempts)
            else:
                err_msg = (res.err or res.out).strip()
                if err_msg:
                    error_lines = err_msg.splitlines()
                    relevant_error = error_lines[-1] if error_lines else "Unknown error"
                    if len(relevant_error) > 180:
                        relevant_error = relevant_error[:177] + "..."
                    cprint(f"{_manager_human(mgr)} failed: {relevant_error}", "WARNING")
                else:
                    cprint(f"{_manager_human(mgr)} failed with no error message", "WARNING")
                    
        except Exception as e:
            err_result = RunResult(False, -1, "", str(e))
            attempts.append((mgr, err_result))
            cprint(f"{_manager_human(mgr)} failed with exception: {str(e)}", "WARNING")

    cprint(f"Failed to remove '{pkg}' with all available managers.", "ERROR")
    return (False, attempts)