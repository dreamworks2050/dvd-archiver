#!/usr/bin/env python3
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import platform
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Callable
from collections import deque
import shutil
import hashlib


# External tools expected: drutil, diskutil, ddrescue, shasum, dvdisaster (optional)
# This script orchestrates per-disc archival with a Rich-based TUI.

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn, TransferSpeedColumn
    from rich.prompt import Prompt, Confirm
    from dotenv import load_dotenv
except ImportError:
    print("Missing Python package 'rich'. Install with: pip install rich", file=sys.stderr)
    sys.exit(1)


# OS detection (must be before console setup)
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"

# Force UTF-8 encoding on Windows to handle Unicode characters in filenames
VT100_ENABLED = False
if IS_WINDOWS:
    try:
        # Set console to UTF-8 mode
        if sys.stdout.encoding != 'utf-8':
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr.encoding != 'utf-8':
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')

        # Enable ANSI escape codes on Windows 10+ (VT100 terminal mode)
        import ctypes
        import msvcrt
        import os

        # Check if stdout is a real console (not redirected)
        if os.isatty(sys.stdout.fileno()):
            kernel32 = ctypes.windll.kernel32
            # Get stdout handle
            stdout_handle = kernel32.GetStdHandle(-11)
            # Get current console mode
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
                # Enable VT processing (0x0004)
                ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                if kernel32.SetConsoleMode(stdout_handle, new_mode):
                    VT100_ENABLED = True
    except Exception:
        pass

# Don't force terminal mode - let Rich auto-detect capabilities
# This prevents ANSI codes from being printed when terminal doesn't support them
console = Console()
load_dotenv()

# Don't use colorama on Windows - it breaks after subprocess calls
# Just use plain text to avoid ANSI code issues entirely
USE_COLORS_ON_WINDOWS = False


def safe_print(text, markup: bool = True, **kwargs):
    """Print text, using plain text on Windows, Rich on other platforms

    Args:
        text: Text to print (may contain Rich markup) or Rich object (Panel, Text, etc.)
        markup: If False, disable markup parsing even on non-Windows platforms
        **kwargs: Additional arguments passed to console.print (highlight, etc.)
    """
    if IS_WINDOWS:
        # On Windows, strip all Rich markup and use plain text
        import re
        from io import StringIO

        # Handle Rich objects (Panel, Text, etc.) by rendering to plain text
        if not isinstance(text, str):
            try:
                from rich.console import Console as TempConsole
                temp_buffer = StringIO()
                temp_console = TempConsole(file=temp_buffer, force_terminal=False, legacy_windows=False, no_color=True, width=80)
                temp_console.print(text)
                clean_text = temp_buffer.getvalue().strip()
            except Exception:
                # Fallback: convert to string
                clean_text = str(text)
        else:
            # Strip Rich markup from strings
            clean_text = re.sub(r'\[/?[^\]]+\]', '', text)

        print(clean_text, flush=True)
    else:
        # On macOS/Linux, use Rich console with markup
        console.print(text, markup=markup, **kwargs)


def cprint(text: str, color: str = 'white'):
    """Simple colored print for Windows (used in static progress updates)

    Args:
        text: Text to print
        color: Color name (green, yellow, cyan, etc.)
    """
    if IS_WINDOWS:
        # Just use plain text on Windows to avoid ANSI issues with subprocess
        print(text, flush=True)
    else:
        # On macOS/Linux, use Rich
        style_map = {
            'green': 'green',
            'yellow': 'yellow',
            'cyan': 'cyan',
            'white': 'white',
            'dim': 'dim',
        }
        safe_print(text, style=style_map.get(color, 'white'))


@dataclass
class StepState:
    name: str
    status: str = "pending"  # pending|running|done|error|skipped
    message: str = ""


@dataclass
class DiscArchive:
    disc_number: str
    start_time: str
    end_time: Optional[str]
    device_disk: Optional[str]
    device_rdisk: Optional[str]
    iso_path: Optional[str]
    log_path: Optional[str]
    checksum_sha256: Optional[str]
    parity_path: Optional[str]
    ddrescue_stats: Dict[str, str]
    steps: Dict[str, StepState]
    success: bool = False


@dataclass
class CopyOperation:
    """Tracks a copy mode operation for a single DVD folder"""
    folder_number: str
    folder_name: str
    source_folder: str
    target_folder: str
    start_time: str
    end_time: Optional[str]
    files: List[Dict[str, str]]  # List of {source_path, target_path, checksum, parity_path}
    steps: Dict[str, StepState]
    success: bool = False


ARCHIVE_BASE = os.getenv("DVD_ARCHIVE_BASE", str(Path.home() / "DVD_Archive"))
# Normalize base path to absolute path (expand ~ and env vars)
ARCHIVE_BASE = os.path.abspath(os.path.expanduser(os.path.expandvars(ARCHIVE_BASE)))
DVD_MODE = os.getenv("DVD_MODE", "ddrescue").strip().lower()
ARCHIVE_DIR = Path(ARCHIVE_BASE)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
JSON_ARCHIVE = ARCHIVE_DIR / "archive_log.json"

# Copy mode configuration
SOURCE_PATHS = os.getenv("SOURCE_PATHS", "").strip()
TARGET_PATH = os.getenv("TARGET_PATH", "").strip()
COPY_STATE_JSON = Path("copy_state.json")  # Store in project folder


def load_archive_json() -> Dict:
    if JSON_ARCHIVE.exists():
        with open(JSON_ARCHIVE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}


def save_archive_json(data: Dict) -> None:
    tmp = JSON_ARCHIVE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(JSON_ARCHIVE)


def load_copy_state() -> Dict:
    """Load copy mode state from JSON"""
    if COPY_STATE_JSON.exists():
        with open(COPY_STATE_JSON, "r") as f:
            try:
                data = json.load(f)
                # Migrate old format to new format if needed
                if "processed_folders" in data and "processed_discs" not in data:
                    # Old format - convert to new
                    return {
                        "processed_discs": {},
                        "folder_metadata": {},
                        "path_statistics": {},
                        "last_updated": data.get("last_updated", datetime.now(timezone.utc).isoformat())
                    }
                return data
            except Exception:
                return {
                    "processed_discs": {},
                    "folder_metadata": {},
                    "path_statistics": {},
                    "last_updated": datetime.now(timezone.utc).isoformat()
                }
    return {
        "processed_discs": {},
        "folder_metadata": {},
        "path_statistics": {},
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


def save_copy_state(data: Dict) -> None:
    """Save copy mode state to JSON with atomic write"""
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = COPY_STATE_JSON.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(COPY_STATE_JSON)


def is_disc_completed(state: Dict, target_filename: str, source_path: str = None) -> bool:
    """Check if a disc has been successfully processed

    Uses normalized matching to handle leading zeros (e.g., 675.cdr matches 0675.cdr)
    Also verifies the target file actually exists on disk and source matches
    """
    import re

    def normalize_filename(filename: str) -> str:
        """Strip leading zeros from numeric parts for matching"""
        stem = Path(filename).stem
        ext = Path(filename).suffix
        def strip_leading_zeros(match):
            num = match.group(0).lstrip('0')
            return num if num else '0'
        normalized_stem = re.sub(r'\d+', strip_leading_zeros, stem.lower())
        return normalized_stem + ext.lower()

    # Check exact match first
    disc_data = state.get("processed_discs", {}).get(target_filename)
    if disc_data and disc_data.get("all_steps_completed", False):
        # Verify the file actually exists on disk
        target_path = disc_data.get("target_path")
        if target_path and Path(target_path).exists():
            # If source_path provided, verify it matches the stored source
            if source_path:
                stored_source = disc_data.get("source_path", "")
                # Normalize both paths for comparison (handle MDX vs ISO)
                if normalize_filename(Path(source_path).name) != normalize_filename(Path(stored_source).name):
                    return False  # Different source file
            return True

    # Check normalized match
    normalized_target = normalize_filename(target_filename)
    for existing_filename in state.get("processed_discs", {}).keys():
        if normalize_filename(existing_filename) == normalized_target:
            disc_data = state["processed_discs"][existing_filename]
            if disc_data.get("all_steps_completed", False):
                # Verify the file actually exists on disk
                target_path = disc_data.get("target_path")
                if target_path and Path(target_path).exists():
                    # If source_path provided, verify it matches the stored source
                    if source_path:
                        stored_source = disc_data.get("source_path", "")
                        # Normalize both paths for comparison (handle MDX vs ISO)
                        if normalize_filename(Path(source_path).name) != normalize_filename(Path(stored_source).name):
                            return False  # Different source file
                    return True

    return False


def mark_disc_completed(state: Dict, target_filename: str, source_path: str, target_path: str,
                        folder_number: str, folder_title: str, checksum: str, parity_path: str):
    """Mark a disc as successfully processed"""
    if "processed_discs" not in state:
        state["processed_discs"] = {}

    state["processed_discs"][target_filename] = {
        "source_path": str(source_path),
        "target_path": str(target_path),
        "folder_number": folder_number,
        "folder_title": folder_title,
        "checksum": checksum,
        "parity_path": str(parity_path) if parity_path else "",
        "all_steps_completed": True,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def get_folder_retry_count(state: Dict, folder_number: str) -> int:
    """Get retry count for a folder"""
    return state.get("folder_metadata", {}).get(folder_number, {}).get("retry_count", 0)


def increment_folder_retry(state: Dict, folder_number: str, error_msg: str):
    """Increment retry count for a folder and record error"""
    if "folder_metadata" not in state:
        state["folder_metadata"] = {}
    if folder_number not in state["folder_metadata"]:
        state["folder_metadata"][folder_number] = {
            "title": "",
            "retry_count": 0,
            "last_error": None,
            "status": "pending"
        }

    state["folder_metadata"][folder_number]["retry_count"] += 1
    state["folder_metadata"][folder_number]["last_error"] = error_msg
    state["folder_metadata"][folder_number]["status"] = "failed"


def mark_folder_completed(state: Dict, folder_number: str, folder_title: str):
    """Mark a folder as successfully completed"""
    if "folder_metadata" not in state:
        state["folder_metadata"] = {}

    state["folder_metadata"][folder_number] = {
        "title": folder_title,
        "retry_count": 0,
        "last_error": None,
        "status": "completed"
    }


def update_folder_title_if_longer(state: Dict, folder_number: str, new_title: str) -> str:
    """Update folder title if new title is longer, return the final title"""
    if "folder_metadata" not in state:
        state["folder_metadata"] = {}

    if folder_number not in state["folder_metadata"]:
        state["folder_metadata"][folder_number] = {
            "title": new_title,
            "retry_count": 0,
            "last_error": None,
            "status": "pending"
        }
        return new_title

    existing_title = state["folder_metadata"][folder_number].get("title", "")
    if len(new_title) > len(existing_title):
        state["folder_metadata"][folder_number]["title"] = new_title
        return new_title
    return existing_title


def update_path_statistics(state: Dict, source_path: str, success: bool, discs_count: int = 1):
    """Update statistics for a source path"""
    if "path_statistics" not in state:
        state["path_statistics"] = {}

    if source_path not in state["path_statistics"]:
        state["path_statistics"][source_path] = {
            "folders_processed": 0,
            "folders_failed": 0,
            "discs_processed": 0
        }

    if success:
        state["path_statistics"][source_path]["folders_processed"] += 1
        state["path_statistics"][source_path]["discs_processed"] += discs_count
    else:
        state["path_statistics"][source_path]["folders_failed"] += 1


def clear_folder_state(state: Dict, folder_number: str):
    """Clear all disc state for a folder number (used when retrying after failure)"""
    if "processed_discs" not in state:
        return

    # Remove all discs belonging to this folder number
    keys_to_remove = [
        key for key, value in state["processed_discs"].items()
        if value.get("folder_number") == folder_number
    ]

    for key in keys_to_remove:
        del state["processed_discs"][key]


def run_cmd(cmd: str, check: bool = False, capture: bool = True, env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> Tuple[int, str, str]:
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd)
    out, err = proc.communicate()
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=out, stderr=err)
    return proc.returncode, out, err


def detect_dvd_device() -> Tuple[Optional[str], Optional[str]]:
    # Prefer drutil status, which typically reports the exact device name
    _, dout, _ = run_cmd("drutil status")
    dvd_disk: Optional[str] = None
    for line in dout.splitlines():
        if "Name:" in line and "/dev/disk" in line:
            # e.g. "Name: /dev/disk4"
            dvd_disk = line.split("Name:")[-1].strip()
            break

    # Validate via diskutil list to ensure it exists and is external physical
    if dvd_disk:
        rc, lout, _ = run_cmd("diskutil list")
        if dvd_disk in lout:
            return dvd_disk, dvd_disk.replace("/dev/disk", "/dev/rdisk")

    # Fallback: look for external, physical disk with ~4–9 GB capacity (typical DVD)
    rc, lout, _ = run_cmd("diskutil list")
    candidates: List[str] = []
    current_header = ""
    for line in lout.splitlines():
        if line.startswith("/dev/disk") and "external" in line and "physical" in line:
            current_header = line.split()[0].strip(":")
        elif current_header and (" *" in line or "\t*" in line or "*" in line):
            # size line; try to parse size in GB
            m = re.search(r"\*(\d+\.\d+|\d+)\s+GB", line)
            if m:
                size_gb = float(m.group(1))
                if 3.5 <= size_gb <= 9.5:
                    candidates.append(current_header)
            current_header = ""
    if candidates:
        d = candidates[0]
        return d, d.replace("/dev/disk", "/dev/rdisk")
    return None, None


def unmount_disk(disk: str) -> bool:
    rc, out, err = run_cmd(f"diskutil unmountDisk {shlex.quote(disk)}")
    return rc == 0


def get_total_bytes_for_device(rdisk: str) -> int:
    # Try diskutil info for the whole disk
    disk = rdisk.replace("/dev/rdisk", "/dev/disk")
    rc, out, _ = run_cmd(f"diskutil info {shlex.quote(disk)}")
    if rc == 0:
        # Total Size:               7.5 GB (7498065920 Bytes)
        m = re.search(r"Total Size:\s+.*\((\d+) Bytes\)", out)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    # Fallback to drutil status parsing of Space Used blocks (DVD 2048-byte sectors)
    rc2, dstat, _ = run_cmd("drutil status")
    if rc2 == 0:
        m2 = re.search(r"Space Used:.*?blocks:\s*(\d+)", dstat)
        if m2:
            try:
                return int(m2.group(1)) * 2048
            except Exception:
                pass
        # Another drutil format sometimes shows just blocks count before sizes
        m3 = re.search(r"blocks\s*:\s*(\d+)\s*/", dstat)
        if m3:
            try:
                return int(m3.group(1)) * 2048
            except Exception:
                pass
    return 0


def get_disc_label(disk: str) -> Optional[str]:
    # Prefer the actual volume (partition) name if mounted
    rc, out, _ = run_cmd(f"diskutil list {shlex.quote(disk)}")
    if rc == 0:
        part_ids: List[str] = []
        for line in out.splitlines():
            m = re.search(rf"^{re.escape(disk)}s(\d+)", line.strip())
            if m:
                part_ids.append(f"{disk}s{m.group(1)}")
        for pid in part_ids:
            rc2, info, _ = run_cmd(f"diskutil info {shlex.quote(pid)}")
            if rc2 == 0:
                vol = None
                for ln in info.splitlines():
                    if "Volume Name:" in ln:
                        vol = ln.split(":", 1)[1].strip()
                        break
                if vol:
                    return vol
    # Fallback to disk info, but avoid using drive model; try Media Name only if nothing else
    rc3, out3, _ = run_cmd(f"diskutil info {shlex.quote(disk)}")
    if rc3 == 0:
        for line in out3.splitlines():
            if "Volume Name:" in line:
                name = line.split(":", 1)[1].strip()
                if name:
                    return name
        for line in out3.splitlines():
            if "Media Name:" in line:
                name = line.split(":", 1)[1].strip()
                if name and not name.upper().startswith("HL-DT-ST"):
                    return name
    # Last resort: look at /Volumes
    try:
        vols = os.listdir("/Volumes")
        for v in vols:
            if v not in {"Macintosh HD", "Macintosh HD - Data"}:
                return v
    except Exception:
        pass
    return None

def ensure_sudo_cached() -> None:
    # Try non-interactive sudo cache; if it fails, prompt interactively
    rc, _, _ = run_cmd("sudo -n -v")
    if rc != 0:
        safe_print("[yellow]Requesting sudo to cache credentials...[/yellow]")
        subprocess.call("sudo -v", shell=True)


def ddrescue_fast_then_retry(
    rdisk: str,
    iso_path: Path,
    log_path: Path,
    refresh_cb: Optional[Callable[[], None]],
    steps: Dict[str, StepState],
) -> Dict[str, str]:
    # macOS often denies direct I/O; use large cluster size for performance.
    stats: Dict[str, str] = {}
    fast_cmd_nonint = f"sudo -n ddrescue -b 2048 -c 16384 -n {shlex.quote(rdisk)} {shlex.quote(str(iso_path))} {shlex.quote(str(log_path))}"
    retry_cmd_nonint = f"sudo -n ddrescue -b 2048 -c 16384 -r3 {shlex.quote(rdisk)} {shlex.quote(str(iso_path))} {shlex.quote(str(log_path))}"
    fast_cmd = fast_cmd_nonint.replace("-n ddrescue", "ddrescue")
    retry_cmd = retry_cmd_nonint.replace("-n ddrescue", "ddrescue")

    dd_line_re = re.compile(
        r"pct rescued:\s*(?P<pct>[0-9.]+)%|"
        r"rescued:\s*(?P<resc_val>[0-9.]+)\s*(?P<resc_unit>kB|MB|GB).*?current rate:\s*(?P<rate_val>[0-9.]+)\s*(?P<rate_unit>kB|MB|GB)/s.*?average rate:\s*(?P<avg_val>[0-9.]+)\s*(?P<avg_unit>kB|MB|GB)/s",
        re.IGNORECASE,
    )

    def _to_mb_per_s(val: float, unit: str) -> float:
        unit = unit.upper()
        if unit == "KB":
            return val / 1024.0
        if unit == "MB":
            return val
        if unit == "GB":
            return val * 1024.0
        return val

    def parse_and_render(line: str, phase: str) -> str:
        m = dd_line_re.search(line)
        if not m:
            steps[phase].message = line[-80:]
            if refresh_cb:
                refresh_cb()
            return line
        if m.group("pct"):
            pct = m.group("pct")
            steps[phase].message = f"{pct}% rescued"
            if refresh_cb:
                refresh_cb()
            return f"{pct}% rescued"
        else:
            rescued = f"{m.group('resc_val')} {m.group('resc_unit')}"
            rate_mb = _to_mb_per_s(float(m.group('rate_val')), m.group('rate_unit'))
            avg_mb = _to_mb_per_s(float(m.group('avg_val')), m.group('avg_unit'))
            rate = f"{rate_mb:.2f} MB/s"
            avg = f"{avg_mb:.2f} MB/s"
            msg = f"{rate} avg {avg} (rescued {rescued})"
            steps[phase].message = msg
            if refresh_cb:
                refresh_cb()
            return msg

    def run_ddrescue(cmd_try_nonint: str, cmd_interactive: str, phase: str) -> bool:
        steps[phase].status = "running"
        # Determine total bytes for % estimate
        total_bytes = get_total_bytes_for_device(rdisk)
        # First try without prompting for sudo
        proc = subprocess.Popen(cmd_try_nonint, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        last_line = ""
        samples = deque(maxlen=20)  # ~10s window at 0.5s interval
        while True:
            # Read any available output lines without blocking the loop too long
            if proc.stdout:
                line = proc.stdout.readline()
                if line:
                    last_line = line.strip()
                    parse_and_render(last_line, phase)
            # Poll size and compute speed/%
            try:
                if iso_path.exists():
                    sz = iso_path.stat().st_size
                    now = time.time()
                    samples.append((now, sz))
                    mbps = 0.0
                    if len(samples) >= 2:
                        # choose an anchor ~5s back if available
                        t0, s0 = samples[0]
                        for t, s in samples:
                            if now - t >= 5.0:
                                t0, s0 = t, s
                        dt = max(0.001, now - t0)
                        delta = max(0, sz - s0)
                        mbps = (delta / dt) / (1024.0 * 1024.0)
                    pct = (sz / total_bytes * 100.0) if total_bytes > 0 else 0.0
                    steps[phase].message = f"{mbps:.2f} MB/s, {pct:.2f}%"
                    if refresh_cb:
                        refresh_cb()
            except Exception:
                pass
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        if proc.returncode == 0:
            steps[phase].status = "done"
            steps[phase].message = last_line
            stats[phase] = last_line
            if refresh_cb:
                refresh_cb()
            return True
        # If sudo needed, fall back to interactive
        steps[phase].message = "Retrying with sudo (interactive)"
        if refresh_cb:
            refresh_cb()
        proc2 = subprocess.Popen(cmd_interactive, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        last_line = ""
        samples = deque(maxlen=20)
        try:
            while True:
                if proc2.stdout:
                    line = proc2.stdout.readline()
                    if line:
                        last_line = line.strip()
                        parse_and_render(last_line, phase)
                try:
                    if iso_path.exists():
                        sz = iso_path.stat().st_size
                        now = time.time()
                        samples.append((now, sz))
                        mbps = 0.0
                        if len(samples) >= 2:
                            t0, s0 = samples[0]
                            for t, s in samples:
                                if now - t >= 5.0:
                                    t0, s0 = t, s
                            dt = max(0.001, now - t0)
                            delta = max(0, sz - s0)
                            mbps = (delta / dt) / (1024.0 * 1024.0)
                        pct = (sz / total_bytes * 100.0) if total_bytes > 0 else 0.0
                        steps[phase].message = f"{mbps:.2f} MB/s, {pct:.2f}%"
                        if refresh_cb:
                            refresh_cb()
                except Exception:
                    pass
                if proc2.poll() is not None:
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            proc2.send_signal(signal.SIGINT)
            proc2.wait()
        steps[phase].status = "done" if proc2.returncode == 0 else "error"
        steps[phase].message = last_line
        stats[phase] = last_line
        if refresh_cb:
            refresh_cb()
        return proc2.returncode == 0

    ok_fast = run_ddrescue(fast_cmd_nonint, fast_cmd, "ddrescue_fast")
    ok_retry = run_ddrescue(retry_cmd_nonint, retry_cmd, "ddrescue_retry") if ok_fast else False
    return stats


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hash using system tools (macOS/Linux) or Python (Windows)"""
    if IS_WINDOWS:
        # Use Python's hashlib on Windows for reliability
        return compute_sha256_python(path)
    else:
        rc, out, err = run_cmd(f"shasum -a 256 {shlex.quote(str(path))}")
        if rc == 0 and out:
            return out.split()[0]
        return ""


def compute_sha256_python(path: Path) -> str:
    """Compute SHA-256 using Python hashlib (cross-platform)"""
    sha256_hash = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            # Read in 8MB chunks for efficiency
            for chunk in iter(lambda: f.read(8*1024*1024), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except Exception as e:
        safe_print(f"[red]Error computing checksum: {e}[/red]")
        return ""


def tool_available(name: str) -> bool:
    """Check if a command-line tool is available (cross-platform)"""
    if IS_WINDOWS:
        # Check if executable exists in script directory subfolder (common for Windows portable apps)
        script_dir = Path(__file__).parent.resolve()
        subfolder_exe = script_dir / name / f"{name}.exe"
        if subfolder_exe.exists():
            return True

        # Check for versioned subfolders (e.g., iat-0.1.7.win32)
        # Look for any folder starting with the tool name
        for folder in script_dir.iterdir():
            if folder.is_dir() and folder.name.lower().startswith(name.lower()):
                versioned_exe = folder / f"{name}.exe"
                if versioned_exe.exists():
                    return True

        # Check if executable exists in script directory (project root)
        script_exe = script_dir / f"{name}.exe"
        if script_exe.exists():
            return True

        # Also check current working directory
        local_exe = Path(f"{name}.exe").resolve()
        if local_exe.exists():
            return True

        # Finally check system PATH using 'where' command
        rc, out, _ = run_cmd(f"where {name}")
        return rc == 0 and out.strip() != ""
    else:
        # On Unix-like systems, use 'command -v'
        rc, out, _ = run_cmd(f"command -v {shlex.quote(name)}")
        return rc == 0 and out.strip() != ""


def find_tool_path(name: str) -> Optional[Path]:
    """Find the full path to a tool executable"""
    if IS_WINDOWS:
        script_dir = Path(__file__).parent.resolve()

        # Check subfolder with exact name
        subfolder_exe = script_dir / name / f"{name}.exe"
        if subfolder_exe.exists():
            return subfolder_exe

        # Check versioned subfolders
        for folder in script_dir.iterdir():
            if folder.is_dir() and folder.name.lower().startswith(name.lower()):
                versioned_exe = folder / f"{name}.exe"
                if versioned_exe.exists():
                    return versioned_exe

        # Check script directory root
        script_exe = script_dir / f"{name}.exe"
        if script_exe.exists():
            return script_exe

        # Check current directory
        local_exe = Path(f"{name}.exe").resolve()
        if local_exe.exists():
            return local_exe

    # Return just the name for PATH lookup
    return Path(name)


def convert_mdx_to_iso(mdx_path: Path, iso_path: Path) -> tuple[bool, str]:
    """Convert MDX file to ISO using IAT or fallback tools

    Returns (success: bool, message: str)
    """
    # Check for IAT (preferred tool)
    if tool_available("iat"):
        # Find the full path to iat executable
        iat_path = find_tool_path("iat")

        if IS_WINDOWS:
            # IAT has issues with paths containing escape sequences
            # Use temp folder on C: drive (NVME) for faster conversion
            import subprocess
            import tempfile
            import shutil

            # Create temp directory on C: drive (project root or system temp)
            temp_dir = Path(tempfile.mkdtemp(dir="C:/temp" if Path("C:/temp").exists() else None))

            try:
                # Copy MDX to temp folder
                temp_mdx = temp_dir / mdx_path.name
                shutil.copy2(str(mdx_path.resolve()), str(temp_mdx))

                # Convert in temp folder using relative paths
                iso_name = iso_path.name
                proc = subprocess.Popen(
                    [str(iat_path), "-i", mdx_path.name, "-o", iso_name, "--iso"],
                    cwd=str(temp_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                out, err = proc.communicate()
                rc = proc.returncode

                # Move the ISO to final target if successful
                if rc == 0:
                    temp_iso = temp_dir / iso_name
                    if temp_iso.exists():
                        # Validate file size - ISO should be similar to MDX (allow 5% variance)
                        mdx_size = mdx_path.stat().st_size
                        iso_size = temp_iso.stat().st_size
                        size_ratio = iso_size / mdx_size if mdx_size > 0 else 0

                        if size_ratio < 0.85:  # ISO is more than 15% smaller - likely corrupted
                            return False, f"✗ Converted ISO is too small ({iso_size / (1024**3):.2f}GB vs {mdx_size / (1024**3):.2f}GB MDX) - likely corrupted"

                        shutil.move(str(temp_iso), str(iso_path.resolve()))
            finally:
                # Clean up temp directory
                if temp_dir.exists():
                    shutil.rmtree(str(temp_dir), ignore_errors=True)
        else:
            cmd = f"{shlex.quote(str(iat_path))} -i {shlex.quote(str(mdx_path))} -o {shlex.quote(str(iso_path))} --iso"
            rc, out, err = run_cmd(cmd)

        if rc == 0 and iso_path.exists():
            # Validate file size for non-Windows too
            mdx_size = mdx_path.stat().st_size
            iso_size = iso_path.stat().st_size
            size_ratio = iso_size / mdx_size if mdx_size > 0 else 0

            if size_ratio < 0.85:  # ISO is more than 15% smaller
                iso_path.unlink()  # Delete corrupted ISO
                return False, f"✗ Converted ISO is too small ({iso_size / (1024**3):.2f}GB vs {mdx_size / (1024**3):.2f}GB MDX) - deleted"

            return True, f"✓ Converted: {mdx_path.name} → {iso_path.name}"
        else:
            # Show only the last error line to avoid spam
            if err:
                error_lines = err.strip().split('\n')
                last_error = error_lines[-1] if error_lines else "Unknown error"
                return False, f"✗ IAT conversion failed: {last_error}"
            elif out:
                # Check stdout for errors
                output_lines = out.strip().split('\n')
                last_output = output_lines[-1] if output_lines else "Unknown error"
                return False, f"✗ IAT conversion failed: {last_output}"
            return False, f"✗ IAT conversion failed for {mdx_path.name}"

    # Check for AnyToISO (commercial alternative with free tier)
    elif tool_available("anytoiso"):
        if IS_WINDOWS:
            cmd = f'anytoiso /convert "{mdx_path.resolve()}" "{iso_path.resolve()}"'
        else:
            cmd = f"anytoiso /convert {shlex.quote(str(mdx_path))} {shlex.quote(str(iso_path))}"

        rc, out, err = run_cmd(cmd)
        if rc == 0 and iso_path.exists():
            return True, f"✓ Conversion successful: {iso_path.name}"
        else:
            return False, "✗ AnyToISO conversion failed"

    else:
        return False, "✗ No conversion tool available (IAT or AnyToISO required)"


def run_dvdisaster(path: Path, parity_out: Path, percent: int = 10) -> bool:
    """Run dvdisaster to create RS02 parity file (cross-platform)"""
    if not tool_available("dvdisaster"):
        return False

    # Determine the executable path and working directory
    cwd = None
    if IS_WINDOWS:
        script_dir = Path(__file__).parent.resolve()

        # Check for subfolder version first (portable app with DLLs)
        subfolder_exe = script_dir / "dvdisaster" / "dvdisaster.exe"
        if subfolder_exe.exists():
            # Run from subfolder so DLLs can be found
            dvdisaster_cmd = "dvdisaster.exe"
            cwd = str(script_dir / "dvdisaster")
        # Check script directory
        elif (script_dir / "dvdisaster.exe").exists():
            # Run from script directory so DLLs can be found
            dvdisaster_cmd = "dvdisaster.exe"
            cwd = str(script_dir)
        # Then check current working directory
        elif Path("dvdisaster.exe").exists():
            dvdisaster_cmd = ".\\dvdisaster.exe"
        # Finally use system PATH
        else:
            dvdisaster_cmd = "dvdisaster"
        # Windows: double backslashes to avoid escape sequence issues
        # Note: RS03 with -o file creates separate .ecc files
        # -o file means "put ecc data in a file" (separate mode)
        # Double backslashes so they're not interpreted as escape sequences
        input_path = str(path.resolve()).replace('\\', '\\\\')
        output_path = str(parity_out.resolve()).replace('\\', '\\\\')
        cmd = f'{dvdisaster_cmd} -i "{input_path}" -e "{output_path}" -mRS03 -c -n{percent}% -o file'
    else:
        # Unix: use shlex.quote
        # RS03 with -o file creates separate .ecc files
        cmd = f"dvdisaster -i {shlex.quote(str(path))} -e {shlex.quote(str(parity_out))} -mRS03 -c -n{percent}% -o file"

    rc, out, err = run_cmd(cmd, cwd=cwd)

    # Check if it failed
    if rc != 0:
        return False

    # Verify the ecc file was created
    if not parity_out.exists():
        return False

    return True


def hdiutil_image(
    rdisk: str,
    out_prefix: Path,
    refresh_cb: Optional[Callable[[], None]],
    steps: Dict[str, StepState],
) -> bool:
    # hdiutil UDTO creates a .cdr file at the given prefix; we will rename to .iso
    steps["ddrescue_fast"].status = "running"
    cdr_path = Path(str(out_prefix) + ".cdr")
    if cdr_path.exists():
        try:
            cdr_path.unlink()
        except Exception:
            run_cmd(f"sudo rm -f {shlex.quote(str(cdr_path))}")
    # Use -puppetstrings for parseable progress output
    cmd = f"sudo hdiutil create -puppetstrings -srcdevice {shlex.quote(rdisk)} -format UDTO -o {shlex.quote(str(out_prefix))}"
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    total_bytes = get_total_bytes_for_device(rdisk)
    last_line = ""
    prev_samples: List[Tuple[float, int]] = []
    while True:
        if proc.stdout:
            line = proc.stdout.readline()
            if line:
                last_line = line.strip()
                # Parse puppetstrings progress like: PERCENT: 12.34
                m = re.search(r"PERCENT:\s*([0-9.]+)", last_line)
                if m:
                    try:
                        pct = float(m.group(1))
                        steps["ddrescue_fast"].message = f"{pct:.2f}%"
                        if refresh_cb:
                            refresh_cb()
                    except Exception:
                        pass
        # Poll size of .cdr while writing
        if cdr_path.exists():
            try:
                sz = cdr_path.stat().st_size
                now = time.time()
                prev_samples.append((now, sz))
                # keep last 20 samples (~10s)
                if len(prev_samples) > 20:
                    prev_samples = prev_samples[-20:]
                mbps = 0.0
                if len(prev_samples) >= 2:
                    t0, s0 = prev_samples[0]
                    for t, s in prev_samples:
                        if now - t >= 5.0:
                            t0, s0 = t, s
                    dt = max(0.001, now - t0)
                    delta = max(0, sz - s0)
                    mbps = (delta / dt) / (1024.0 * 1024.0)
                pct = (sz / total_bytes * 100.0) if total_bytes > 0 else 0.0
                steps["ddrescue_fast"].message = f"{mbps:.2f} MB/s, {pct:.2f}%"
                if refresh_cb:
                    refresh_cb()
            except Exception:
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    ok = proc.returncode == 0
    steps["ddrescue_fast"].status = "done" if ok else "error"
    steps["ddrescue_fast"].message = last_line
    if not ok:
        return False
    # Rename .cdr to .iso
    try:
        iso_path = Path(str(out_prefix) + ".iso")
        if iso_path.exists():
            iso_path.unlink()
        os.rename(cdr_path, iso_path)
    except Exception:
        return False
    return True


def validate_source_paths(source_paths: List[str]) -> List[str]:
    """Validate and return available source paths"""
    available = []
    for path_str in source_paths:
        path = Path(path_str.strip())
        if path.exists() and path.is_dir():
            available.append(str(path))
        else:
            safe_print(f"[yellow]Source path not available: {path_str}[/yellow]")
    return available


def find_all_numbered_folders(source_path: str) -> List[Tuple[str, str, str]]:
    """
    Find all numbered folders in source path.
    Returns list of (folder_number, folder_name, folder_path) sorted by number
    """
    source = Path(source_path)

    # Validate source path
    if not source.exists():
        safe_print(f"[yellow]Source path does not exist: {source_path}[/yellow]")
        return []

    if not source.is_dir():
        safe_print(f"[yellow]Source path is not a directory: {source_path}[/yellow]")
        return []

    folders = []

    for item in source.iterdir():
        if item.is_dir():
            # Extract numbers from folder name
            numbers = re.findall(r'\d+', item.name)
            if numbers:
                # Use first numeric sequence as folder number
                folder_num = numbers[0]
                folders.append((folder_num, item.name, str(item)))

    # Sort by numeric value (convert to int for proper numeric sorting)
    folders.sort(key=lambda x: int(x[0]))
    return folders


def extract_title_from_folder(folder_name: str) -> str:
    """Extract title from folder name, removing leading numbers and separators

    Returns sanitized title with spaces replaced by underscores
    """
    # Remove leading numbers and common separators
    title = re.sub(r'^\d+[\s\-_.]*', '', folder_name)
    if title:
        # Replace spaces with underscores and clean up multiple underscores
        title = title.replace(' ', '_')
        title = re.sub(r'_+', '_', title)  # Replace multiple underscores with single
        title = title.strip('_')  # Remove leading/trailing underscores
        return title
    # If nothing left, return the original folder name (sanitized)
    folder_name = folder_name.replace(' ', '_')
    folder_name = re.sub(r'_+', '_', folder_name)
    folder_name = folder_name.strip('_')
    return folder_name


def process_single_folder(folder_number: str, folder_name: str, folder_path: str,
                          target: Path, state: Dict, source_path: str, convert_mdx: bool = False) -> bool:
    """Process a single folder with DVD images using per-disc state tracking

    Args:
        folder_number: Extracted number from folder name
        folder_name: Full folder name
        folder_path: Full path to source folder
        target: Target base directory
        state: Shared state dictionary for per-disc tracking
        source_path: Source path this folder belongs to (for statistics)
        convert_mdx: Whether to convert MDX files to ISO

    Returns True if successful, False otherwise
    """
    # Find all image files in the folder (expanded formats)
    folder_p = Path(folder_path)

    image_files = []
    # Use case-insensitive patterns (glob is case-sensitive on some systems)
    # On Windows, file system is case-insensitive but glob behavior varies
    # Supported formats: ISO, IMG, CDR (macOS), MDF/MDS (Alcohol 120%), MDX (Media Data eXtended), NRG (Nero), BIN/CUE
    for ext in ['*.iso', '*.ISO', '*.img', '*.IMG', '*.cdr', '*.CDR', '*.mdf', '*.MDF', '*.mdx', '*.MDX', '*.nrg', '*.NRG', '*.bin', '*.BIN']:
        found = list(folder_p.glob(ext))
        image_files.extend(found)

    # Show all files in folder if no images found (helps diagnose issues)
    if not image_files:
        safe_print(f"[yellow]No image files found in: {folder_path}[/yellow]")
        safe_print("[yellow]Listing folder contents:[/yellow]")
        try:
            all_files = list(folder_p.iterdir())
            for f in all_files[:10]:  # Show first 10
                safe_print(f"  {f.name}")
            if len(all_files) > 10:
                safe_print(f"  ... and {len(all_files) - 10} more")
        except Exception as e:
            safe_print(f"[red]Error listing directory: {e}[/red]")

        safe_print(f"[red]No DVD image files found in {folder_path}[/red]")
        safe_print("[yellow]Supported formats: .iso, .img, .cdr, .mdf, .mdx, .nrg, .bin[/yellow]")
        return False

    # Remove true duplicates (same full path) but keep files with same name and different extensions
    # Example: both 700.mdx and 700.iso should be processed separately
    seen = set()
    unique_files = []
    for f in image_files:
        # Normalize full path for comparison (lowercase on Windows for case-insensitive filesystems)
        normalized = str(f.resolve()).lower() if IS_WINDOWS else str(f.resolve())
        if normalized not in seen:
            seen.add(normalized)
            unique_files.append(f)
    image_files = unique_files

    # Sort files by stem (base name without extension) then by extension for consistent ordering
    # This groups files like: 700.iso, 700.mdx, 700_1.iso, 700_1.mdx, etc.
    image_files.sort(key=lambda x: (x.stem.lower(), x.suffix.lower()))

    # If -conv is enabled, filter out ISO files that have a matching MDX file
    # This ensures we always convert from MDX source instead of copying existing ISO
    if convert_mdx:
        # Helper to normalize stem by stripping leading zeros for comparison
        def normalize_stem(stem: str) -> str:
            """Strip leading zeros from numeric parts and normalize separators for matching

            Examples:
                '770-1' -> '770_1'
                '770_1' -> '770_1'
                '0770-1' -> '770_1'
                '0696' -> '696'
                '702 V8' -> '702_v8'
                '702V8' -> '702v8'
            """
            import re
            # First normalize separators: convert spaces and dashes to underscores
            normalized = stem.lower().replace(' ', '_').replace('-', '_')

            # Then strip leading zeros from numeric sequences
            def strip_leading_zeros(match):
                num = match.group(0).lstrip('0')
                return num if num else '0'  # Keep at least one zero
            return re.sub(r'\d+', strip_leading_zeros, normalized)

        # Build a set of normalized stems that have MDX files
        mdx_stems = set()
        for f in image_files:
            if f.suffix.lower() == '.mdx':
                mdx_stems.add(normalize_stem(f.stem))

        # Filter out ISO files that have matching MDX
        filtered_files = []
        skipped_isos = []
        for f in image_files:
            if f.suffix.lower() == '.iso' and normalize_stem(f.stem) in mdx_stems:
                # Skip this ISO - we'll convert from MDX instead
                skipped_isos.append(f.name)
            else:
                filtered_files.append(f)

        image_files = filtered_files

        if skipped_isos:
            safe_print(f"\n[yellow]⊗ Skipping {len(skipped_isos)} ISO file(s) with matching MDX (will convert from MDX):[/yellow]")
            for name in skipped_isos:
                safe_print(f"  [dim]└─[/dim] [dim yellow]{name}[/dim yellow]")

    safe_print(f"\n[bold green]✓ Found {len(image_files)} unique image file(s) to process:[/bold green]")
    for f in image_files:
        ext_color = "cyan" if f.suffix.lower() == ".mdx" else "blue" if f.suffix.lower() == ".iso" else "magenta"
        safe_print(f"  [dim]└─[/dim] [{ext_color}]{f.name}[/{ext_color}]")

    # Update folder title if longer (title conflict resolution)
    title = extract_title_from_folder(folder_name)
    padded_number = folder_number.zfill(4)
    final_title = update_folder_title_if_longer(state, padded_number, title)

    # Create target folder name using final title
    target_folder_name = f"{padded_number}_{final_title}" if final_title and final_title != padded_number else padded_number
    target_folder = target / target_folder_name

    # Create target folder
    try:
        target_folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        safe_print(f"[bold red]✗ Failed to create target folder: {e}[/bold red]")
        return False

    safe_print(f"\n[bold white]Target:[/bold white] [bold green]{target_folder}[/bold green]")

    # Setup operation tracking
    steps = {
        "validate": StepState("Validate paths"),
        "copy": StepState("Copy files"),
        "checksum": StepState("Generate checksums"),
        "parity": StepState("Create parity files"),
    }

    operation = CopyOperation(
        folder_number=folder_number,
        folder_name=folder_name,
        source_folder=folder_path,
        target_folder=str(target_folder),
        start_time=datetime.now(timezone.utc).isoformat(),
        end_time=None,
        files=[],
        steps=steps,
        success=False,
    )

    # Create progress table
    def render_table() -> Table:
        t = Table(title=f"Processing: {folder_name}")
        t.add_column("Step")
        t.add_column("Status")
        t.add_column("Message")
        status_style = {
            "pending": "grey50",
            "running": "yellow",
            "done": "green",
            "error": "red",
            "skipped": "blue",
        }
        for key in ["validate", "copy", "checksum", "parity"]:
            s = steps[key]
            mark = {
                "pending": "[ ]",
                "running": "[~]",
                "done": "[x]",
                "error": "[!]",
                "skipped": "[-]",
            }[s.status]
            t.add_row(s.name, f"[{status_style[s.status]}]{mark} {s.status}[/]", s.message)
        return t

    # On Windows, use static output instead of Live updates to avoid ANSI code issues
    # Live updates work fine on macOS/Linux with proper terminal support
    use_live_updates = not IS_WINDOWS

    if use_live_updates:
        refresh_rate = 0.5 if IS_WINDOWS else 1
        live_context = Live(render_table(), refresh_per_second=refresh_rate, console=console, auto_refresh=False)
    else:
        # Dummy context manager for Windows
        from contextlib import nullcontext
        live_context = nullcontext()

    with live_context as live:
        # Helper to update and refresh in one call
        def update_live():
            if use_live_updates:
                live.update(render_table())
                live.refresh()
            else:
                # On Windows, print static status updates (plain text, no colors)
                # Don't print anything - we already print status at each step
                pass

        # Validate
        steps["validate"].status = "done"
        steps["validate"].message = f"{len(image_files)} file(s) found"
        if not use_live_updates:
            cprint(f"✓ {steps['validate'].name}: {steps['validate'].message}", 'green')

        # Copy files
        steps["copy"].status = "running"
        if not use_live_updates:
            cprint(f"⟳ {steps['copy'].name}...", 'yellow')

        copied_files = []

        # Group files by base name (stem) to handle same file in multiple formats (e.g., 700.mdx + 700.iso)
        # This ensures both get the same number suffix
        file_groups = {}
        for src_file in image_files:
            stem = src_file.stem
            # Normalize stem: remove spaces
            normalized_stem = stem.replace(' ', '_')
            # Extract any existing disc number from stem (e.g., "700_1" -> "1", "disc2" -> "2")
            disc_num_match = re.search(r'_(\d+)$|disc(\d+)$', normalized_stem, re.IGNORECASE)
            if disc_num_match:
                disc_num = disc_num_match.group(1) or disc_num_match.group(2)
                base_stem = re.sub(r'[_-]?\d+$|disc\d+$', '', normalized_stem, flags=re.IGNORECASE).strip('_-')
            else:
                disc_num = None
                base_stem = normalized_stem

            key = (base_stem.lower(), disc_num)
            if key not in file_groups:
                file_groups[key] = []
            file_groups[key].append(src_file)

        # Assign sequential disc numbers to groups that don't have explicit numbering
        sorted_groups = sorted(file_groups.items(), key=lambda x: (x[0][1] is None, x[0][1] if x[0][1] else '', x[0][0]))
        next_auto_num = 1
        for (base_stem, disc_num), files in sorted_groups:
            if disc_num is None and len(file_groups) > 1:
                # Assign auto number for multi-disc sets without explicit numbering
                file_groups[(base_stem, str(next_auto_num))] = file_groups.pop((base_stem, None))
                next_auto_num += 1

        for idx, src_file in enumerate(image_files, 1):
            stem = src_file.stem
            # Normalize stem: remove spaces and convert to underscores
            # E.g., "702 V8" -> "702_V8", "702V8" -> "702V8"
            normalized_stem = stem.replace(' ', '_')

            # Keep original extension for all formats
            # Different imaging software creates incompatible formats that should not be renamed:
            # - CDR (macOS hdiutil): needs proper conversion, not just renaming
            # - MDX (Daemon Tools): proprietary format, cannot be renamed to ISO
            # - MDF/MDS (Alcohol 120%): paired format, must keep as-is
            # - NRG (Nero): proprietary format, keep as-is
            # - BIN/CUE: paired format, keep as-is
            # - IMG: varies by creator, keep as-is
            normalized_ext = src_file.suffix

            # Find which group this file belongs to
            disc_num_match = re.search(r'_(\d+)$|disc(\d+)$', normalized_stem, re.IGNORECASE)
            if disc_num_match:
                disc_num = disc_num_match.group(1) or disc_num_match.group(2)
                base_stem = re.sub(r'[_-]?\d+$|disc\d+$', '', normalized_stem, flags=re.IGNORECASE).strip('_-')
            else:
                disc_num = None
                base_stem = normalized_stem

            # Extract non-numeric suffix after the folder number
            # E.g., "702_V8" with folder_number "702" -> suffix "_V8"
            #       "702V8" with folder_number "702" -> suffix "V8"
            #       "702 V8" normalized to "702_V8" -> suffix "_V8"
            suffix_match = re.match(rf'^{re.escape(folder_number)}(.*)$', normalized_stem, re.IGNORECASE)
            if suffix_match and suffix_match.group(1):
                # File has extra content after folder number (e.g., "V8", "_V8")
                file_suffix = suffix_match.group(1)
                # Keep the suffix as-is (already normalized from space to underscore above)
                # E.g., "702_V8" -> "_V8", "702V8" -> "V8"
            else:
                file_suffix = ''

            # Determine target filename
            if len(file_groups) == 1 and disc_num is None:
                # Single disc (or multiple formats of same disc) - use folder number + any suffix
                target_filename = f"{padded_number}{file_suffix}{normalized_ext}"
            else:
                # Multi-disc set or explicitly numbered - preserve/assign disc number
                if disc_num:
                    target_filename = f"{padded_number}_{disc_num}{normalized_ext}"
                else:
                    # Find assigned number from file_groups
                    for (bs, dn), files in file_groups.items():
                        if src_file in files and dn:
                            target_filename = f"{padded_number}_{dn}{normalized_ext}"
                            break
                    else:
                        # Fallback to sequential
                        target_filename = f"{padded_number}_disc{idx}{normalized_ext}"

            target_file = target_folder / target_filename

            # Determine final target filename (MDX converts to ISO)
            is_mdx = src_file.suffix.lower() == '.mdx'
            if is_mdx and convert_mdx:
                final_target_filename = Path(target_filename).stem + '.iso'
            else:
                final_target_filename = target_filename

            # Check if this disc is already completed in state
            # Also verify the source file matches (to avoid skipping MDX when ISO was processed)
            if is_disc_completed(state, final_target_filename, str(src_file)):
                safe_print(f"[dim cyan]⊙ Already processed:[/dim cyan] [cyan]{final_target_filename}[/cyan] [dim](skipping {src_file.name})[/dim]")
                continue

            # Process the disc
            if is_mdx and convert_mdx:
                # Convert MDX to ISO instead of copying
                iso_target = target_file.with_suffix('.iso')
                steps["copy"].message = f"Converting {src_file.name} to ISO ({idx}/{len(image_files)})"

                if not use_live_updates:
                    cprint(f"  Converting {src_file.name} to ISO...", 'dim')

                success, message = convert_mdx_to_iso(src_file, iso_target)

                if success:
                    steps["copy"].message = message
                    if not use_live_updates:
                        cprint(f"  {message}", 'green')  # Message already has ✓
                    # Only update after conversion completes
                    copied_files.append({
                        "source_path": str(src_file),
                        "target_path": str(iso_target),
                        "checksum": "",
                        "parity_path": "",
                    })
                else:
                    steps["copy"].status = "error"
                    steps["copy"].message = message
                    update_live()
                    return False
            else:
                # Regular copy for all other formats
                steps["copy"].message = f"Copying {src_file.name} ({idx}/{len(image_files)})"

                if not use_live_updates:
                    cprint(f"  Copying {src_file.name}...", 'dim')
                else:
                    update_live()

                try:
                    shutil.copy2(src_file, target_file)
                    copied_files.append({
                        "source_path": str(src_file),
                        "target_path": str(target_file),
                        "checksum": "",
                        "parity_path": "",
                    })
                except Exception as e:
                    steps["copy"].status = "error"
                    steps["copy"].message = f"Failed to copy {src_file.name}: {e}"
                    update_live()
                    return False

        steps["copy"].status = "done"
        steps["copy"].message = f"{len(copied_files)} file(s) copied"
        if not use_live_updates:
            cprint(f"✓ {steps['copy'].name}: {steps['copy'].message}", 'green')
        else:
            update_live()

        # Generate checksums
        steps["checksum"].status = "running"
        if not use_live_updates:
            cprint(f"⟳ {steps['checksum'].name}...", 'yellow')
        else:
            update_live()

        for idx, file_info in enumerate(copied_files, 1):
            target_file = Path(file_info["target_path"])
            steps["checksum"].message = f"Computing checksum for {target_file.name} ({idx}/{len(copied_files)})"

            if not use_live_updates:
                cprint(f"  Computing checksum for {target_file.name}...", 'dim')

            checksum = compute_sha256(target_file)
            if checksum:
                file_info["checksum"] = checksum
                # Save checksum to file
                checksum_path = target_file.with_suffix(target_file.suffix + ".sha256")
                try:
                    with open(checksum_path, "w") as f:
                        f.write(f"{checksum}  {target_file.name}\n")
                except Exception as e:
                    # Suppress - error message is in Live context
                    pass
            else:
                steps["checksum"].status = "error"
                steps["checksum"].message = f"Failed to compute checksum for {target_file.name}"
                update_live()
                return False

        steps["checksum"].status = "done"
        steps["checksum"].message = f"{len(copied_files)} checksum(s) created"
        if not use_live_updates:
            cprint(f"✓ {steps['checksum'].name}: {steps['checksum'].message}", 'green')
        else:
            update_live()

        # Create parity files
        steps["parity"].status = "running"
        if not use_live_updates:
            cprint(f"⟳ {steps['parity'].name}...", 'yellow')
        else:
            update_live()

        parity_created = 0
        for idx, file_info in enumerate(copied_files, 1):
            target_file = Path(file_info["target_path"])
            parity_path = target_file.with_suffix(target_file.suffix + ".ecc")

            steps["parity"].message = f"Creating parity for {target_file.name} ({idx}/{len(copied_files)})"

            if not use_live_updates:
                cprint(f"  Creating parity for {target_file.name}...", 'dim')
            else:
                update_live()

            if run_dvdisaster(target_file, parity_path):
                file_info["parity_path"] = str(parity_path)
                parity_created += 1
            else:
                # dvdisaster not available or failed, but don't error out
                steps["parity"].message = f"Parity creation skipped/failed for {target_file.name}"
                update_live()

        if parity_created > 0:
            steps["parity"].status = "done"
            steps["parity"].message = f"{parity_created}/{len(copied_files)} parity file(s) created"
        else:
            steps["parity"].status = "skipped"
            steps["parity"].message = "dvdisaster not available or failed"

        if not use_live_updates:
            if parity_created > 0:
                cprint(f"✓ {steps['parity'].name}: {steps['parity'].message}", 'green')
            else:
                cprint(f"− {steps['parity'].name}: {steps['parity'].message}", 'cyan')
        else:
            update_live()

        # Mark each disc as completed in state
        for file_info in copied_files:
            target_file_path = Path(file_info["target_path"])
            target_filename = target_file_path.name
            mark_disc_completed(
                state=state,
                target_filename=target_filename,
                source_path=file_info["source_path"],
                target_path=file_info["target_path"],
                folder_number=padded_number,
                folder_title=final_title,
                checksum=file_info["checksum"],
                parity_path=file_info.get("parity_path", "")
            )

    # Mark folder as completed and save state
    mark_folder_completed(state, padded_number, final_title)
    update_path_statistics(state, source_path, success=True, discs_count=len(copied_files))
    save_copy_state(state)

    safe_print(f"\n[bold green]{'━'*80}[/bold green]")
    safe_print(f"[bold green]✓ SUCCESS:[/bold green] [bold white]{folder_name}[/bold white]")
    safe_print(f"[bold white]  Output:[/bold white] [green]{target_folder}[/green]")
    safe_print(f"[bold white]  Files:[/bold white] [cyan]{len(copied_files)}[/cyan] disc(s) processed")
    safe_print(f"[bold green]{'━'*80}[/bold green]\n")

    return True


def print_final_summary(state: Dict, source_paths: List[str]):
    """Print final processing summary with statistics"""
    safe_print(f"\n[bold cyan]{'═'*80}[/bold cyan]")
    safe_print("[bold white on cyan]                         PROCESSING SUMMARY                          [/bold white on cyan]")
    safe_print(f"[bold cyan]{'═'*80}[/bold cyan]\n")

    path_stats = state.get("path_statistics", {})
    folder_metadata = state.get("folder_metadata", {})

    # Per-path statistics
    for idx, src_path in enumerate(source_paths, 1):
        stats = path_stats.get(src_path, {"folders_processed": 0, "folders_failed": 0, "discs_processed": 0})
        safe_print(f"[bold yellow]Path {idx}:[/bold yellow] [dim]{src_path}[/dim]")
        safe_print(f"  [bold green]✓ Folders processed:[/bold green] [white]{stats['folders_processed']}[/white]")
        safe_print(f"  [bold red]✗ Folders failed:[/bold red] [white]{stats['folders_failed']}[/white]")
        safe_print(f"  [bold cyan]◆ Discs processed:[/bold cyan] [white]{stats['discs_processed']}[/white]")

        # Show failed folders for this path
        failed_folders = [
            (num, meta) for num, meta in folder_metadata.items()
            if meta.get("status") == "failed" and meta.get("retry_count", 0) >= 5
        ]
        if failed_folders:
            safe_print(f"  [dim yellow]⚠ Failed: {', '.join([f[0] for f in failed_folders])}[/dim yellow]")
        safe_print()

    # Total statistics
    total_folders_processed = sum(s.get("folders_processed", 0) for s in path_stats.values())
    total_folders_failed = sum(s.get("folders_failed", 0) for s in path_stats.values())
    total_discs_processed = sum(s.get("discs_processed", 0) for s in path_stats.values())

    safe_print(f"[bold cyan]{'─'*80}[/bold cyan]")
    safe_print("[bold white]TOTAL RESULTS:[/bold white]")
    safe_print(f"  [bold green]✓ Folders processed:[/bold green] [bold white]{total_folders_processed}[/bold white]")
    safe_print(f"  [bold red]✗ Folders failed:[/bold red] [bold white]{total_folders_failed}[/bold white]")
    safe_print(f"  [bold cyan]◆ Discs processed:[/bold cyan] [bold white]{total_discs_processed}[/bold white]")

    # Show folders needing attention
    failed_folders_all = [
        (num, meta) for num, meta in folder_metadata.items()
        if meta.get("status") == "failed" and meta.get("retry_count", 0) >= 5
    ]

    if failed_folders_all:
        safe_print(f"\n[bold red on yellow] ⚠ FOLDERS NEEDING ATTENTION [/bold red on yellow]")
        for folder_num, meta in failed_folders_all:
            last_error = meta.get("last_error", "Unknown error")
            retry_count = meta.get("retry_count", 0)
            safe_print(f"  [bold red]✗[/bold red] [yellow]{folder_num}[/yellow] [dim]- Max retries ({retry_count})[/dim]")
            safe_print(f"    [dim red]└─ {last_error}[/dim red]")

    safe_print(f"\n[bold cyan]{'═'*80}[/bold cyan]\n")


def copy_mode_main(process_all: bool = False, convert_mdx: bool = False, start_from: str = None) -> int:
    """Main function for copy mode (-c option)

    Args:
        process_all: If True, process all folders without user confirmation
        convert_mdx: If True, convert MDX files to ISO during processing
        start_from: If provided, start processing from this folder number (e.g., '696' or '0696')
    """
    safe_print(Panel.fit("[bold cyan]DVD Archiver - Copy Mode[/bold cyan]"))

    # Debug: show terminal capabilities
    if IS_WINDOWS:
        import sys as _dbg_sys
        _is_tty = _dbg_sys.stdout.isatty() if hasattr(_dbg_sys.stdout, 'isatty') else False
        safe_print(f"[dim]Debug: VT100={VT100_ENABLED}, isatty={_is_tty}, legacy_windows={console.legacy_windows}, color_system={console.color_system}[/dim]")

    # Normalize start_from to 4-digit padded format
    start_from_padded = None
    if start_from:
        # Extract digits and pad to 4 digits
        import re
        match = re.search(r'\d+', start_from)
        if match:
            start_from_padded = match.group(0).zfill(4)
            safe_print(f"[bold yellow]Starting from folder: {start_from_padded}[/bold yellow]")

    # Clean up temp directories from previous runs
    import tempfile
    import shutil
    temp_base = Path("C:/temp") if Path("C:/temp").exists() else Path(tempfile.gettempdir())
    cleaned_count = 0
    for temp_dir in temp_base.glob("tmp*"):
        if temp_dir.is_dir():
            try:
                shutil.rmtree(str(temp_dir))
                cleaned_count += 1
            except Exception:
                pass  # Ignore locked/in-use temp folders
    if cleaned_count > 0:
        safe_print(f"[dim]Cleaned up {cleaned_count} temp folder(s)[/dim]")

    if process_all:
        safe_print("[bold yellow]Running in AUTO mode - will process all folders automatically[/bold yellow]")

    if convert_mdx:
        safe_print("[bold cyan]MDX Conversion enabled - will convert .mdx files to .iso[/bold cyan]")
        # Check if conversion tool is available
        if not tool_available("iat") and not tool_available("anytoiso"):
            safe_print("[yellow]WARNING: No conversion tool detected (IAT or AnyToISO)[/yellow]")
            safe_print("[yellow]Install IAT from: https://sourceforge.net/projects/iat.berlios/[/yellow]")
            safe_print("[yellow]Or install AnyToISO from: https://crystalidea.com/anytoiso[/yellow]")
            return 1

        # Clean up undersized/corrupted ISO files in source folders
        safe_print("[dim]Checking for corrupted ISO files in source folders...[/dim]")
        source_paths_str = os.getenv("SOURCE_PATHS", "")
        if source_paths_str:
            cleaned_isos = 0
            for path_str in source_paths_str.split(","):
                path_str = path_str.strip()
                source_path = Path(path_str).resolve()
                if source_path.exists() and source_path.is_dir():
                    # Find all MDX files and check for matching undersized ISOs
                    for mdx_file in source_path.rglob("*.mdx"):
                        # Look for matching ISO (with or without leading zeros)
                        mdx_stem = mdx_file.stem
                        mdx_size = mdx_file.stat().st_size

                        # Check same folder for ISOs
                        for iso_file in mdx_file.parent.glob("*.iso"):
                            # Check if ISO matches MDX name (with normalization)
                            import re
                            def normalize_name(name):
                                return re.sub(r'\d+', lambda m: m.group(0).lstrip('0') or '0', name.lower())

                            if normalize_name(iso_file.stem) == normalize_name(mdx_stem):
                                iso_size = iso_file.stat().st_size
                                size_ratio = iso_size / mdx_size if mdx_size > 0 else 0

                                if size_ratio < 0.85:  # Undersized
                                    try:
                                        iso_file.unlink()
                                        cleaned_isos += 1
                                        safe_print(f"[dim yellow]  ✗ Deleted undersized: {iso_file.name} ({iso_size / (1024**3):.2f}GB vs {mdx_size / (1024**3):.2f}GB)[/dim yellow]")
                                    except Exception:
                                        pass

            if cleaned_isos > 0:
                safe_print(f"[yellow]Cleaned up {cleaned_isos} corrupted ISO file(s)[/yellow]")
            else:
                safe_print("[dim]No corrupted ISOs found[/dim]")

    # Debug: Check dvdisaster availability
    dvdisaster_available = tool_available("dvdisaster")
    if dvdisaster_available:
        safe_print("[green]+ dvdisaster detected[/green]")
    else:
        safe_print("[yellow]! dvdisaster not found (parity files will be skipped)[/yellow]")

    # Load existing state (new per-disc tracking format)
    state = load_copy_state()

    # Show state summary if exists
    # If start_from is specified, clear state for that folder and all folders after it
    if start_from_padded:
        safe_print(f"[yellow]Clearing state for folder {start_from_padded} and all subsequent folders...[/yellow]")

        # Clear all discs belonging to folders >= start_from_padded
        discs_to_remove = []
        for disc_filename, disc_data in state.get("processed_discs", {}).items():
            folder_num = disc_data.get("folder_number", "")
            if folder_num >= start_from_padded:
                discs_to_remove.append(disc_filename)
                # Also delete the target file/folder
                target_path = disc_data.get("target_path", "")
                if target_path:
                    target_file = Path(target_path)
                    if target_file.exists():
                        try:
                            target_file.unlink()
                            safe_print(f"[dim]  Deleted: {target_file.name}[/dim]")
                        except Exception:
                            pass

        # Remove from state
        for disc_filename in discs_to_remove:
            del state["processed_discs"][disc_filename]

        # Clear folder metadata for folders >= start_from_padded
        folders_to_remove = [f for f in state.get("folder_metadata", {}).keys() if f >= start_from_padded]
        for folder_num in folders_to_remove:
            del state["folder_metadata"][folder_num]

        # Also delete target folders
        if TARGET_PATH:
            target_base = Path(TARGET_PATH)
            for folder_num in folders_to_remove:
                # Find matching folders with this number
                for target_folder in target_base.glob(f"{folder_num}_*"):
                    if target_folder.is_dir():
                        try:
                            shutil.rmtree(str(target_folder))
                            safe_print(f"[dim]  Deleted folder: {target_folder.name}[/dim]")
                        except Exception:
                            pass

        save_copy_state(state)
        safe_print(f"[green]Cleared {len(discs_to_remove)} disc(s) from state[/green]")

    processed_discs_count = len(state.get("processed_discs", {}))
    if processed_discs_count > 0:
        safe_print(f"[yellow]Found {processed_discs_count} previously processed disc(s).[/yellow]")
        if not process_all:
            if not Confirm.ask("Continue from previous state?", default=True):
                safe_print("[yellow]Starting fresh. Creating new state...[/yellow]")
                state = load_copy_state()  # Reset to empty state
        else:
            safe_print("[yellow]Continuing from previous state...[/yellow]")

    # Validate configuration
    if not SOURCE_PATHS:
        safe_print("[red]ERROR: SOURCE_PATHS not configured in .env[/red]")
        safe_print("Please set SOURCE_PATHS to comma-separated list of source directories")
        return 1

    if not TARGET_PATH:
        safe_print("[red]ERROR: TARGET_PATH not configured in .env[/red]")
        safe_print("Please set TARGET_PATH to the target directory")
        return 1

    # Parse and validate source paths
    source_paths_list = [p.strip() for p in SOURCE_PATHS.split(",") if p.strip()]
    available_paths = validate_source_paths(source_paths_list)

    if not available_paths:
        safe_print("[red]ERROR: No valid source paths found[/red]")
        return 1

    safe_print(f"[green]Found {len(available_paths)} valid source path(s)[/green]")

    # Validate target path
    target = Path(TARGET_PATH)
    if not target.exists():
        safe_print(f"[yellow]Target path does not exist: {TARGET_PATH}[/yellow]")
        if process_all or Confirm.ask("Create target directory?", default=True):
            try:
                target.mkdir(parents=True, exist_ok=True)
                safe_print(f"[green]Created target directory: {TARGET_PATH}[/green]")
            except Exception as e:
                safe_print(f"[red]Failed to create target directory: {e}[/red]")
                return 1
        else:
            return 1

    # Sequential path processing with retry logic
    MAX_RETRIES = 5

    # Process each source path sequentially
    for src_path in available_paths:
        safe_print(f"\n{'='*80}")
        safe_print(f"[bold cyan]Processing source path: {src_path}[/bold cyan]")
        safe_print(f"{'='*80}\n")

        # Find all folders in this source path
        all_folders = find_all_numbered_folders(src_path)

        if not all_folders:
            safe_print(f"[yellow]No numbered folders found in {src_path}[/yellow]")
            continue

        safe_print(f"[green]Found {len(all_folders)} numbered folder(s) in this path[/green]\n")

        # Process each folder
        for folder_number, folder_name, folder_path in all_folders:
            safe_print(f"\n[bold cyan]{'─'*80}[/bold cyan]")
            safe_print(f"[bold white]Processing folder:[/bold white] [bold yellow]{folder_name}[/bold yellow]")
            safe_print(f"[bold white]Number:[/bold white] [cyan]{folder_number}[/cyan]")
            safe_print(f"[bold white]Path:[/bold white] [dim]{folder_path}[/dim]")
            safe_print(f"[bold cyan]{'─'*80}[/bold cyan]")

            # Zero-pad folder number for consistency
            padded_number = folder_number.zfill(4)

            # If start_from is specified, skip folders before it
            if start_from_padded and padded_number < start_from_padded:
                safe_print(f"[dim cyan]⊙ Skipping folder {padded_number} (before start point {start_from_padded})[/dim cyan]\n")
                continue

            # Check retry limit
            retry_count = get_folder_retry_count(state, padded_number)
            if retry_count >= MAX_RETRIES:
                safe_print(f"[bold red]✗ Folder {padded_number} has reached max retries ({MAX_RETRIES}). SKIPPING.[/bold red]")
                last_error = state.get("folder_metadata", {}).get(padded_number, {}).get("last_error", "Unknown")
                safe_print(f"[red]  └─ Last error: {last_error}[/red]\n")
                continue

            # Ask user to confirm (skip if process_all)
            if not process_all:
                if not Confirm.ask(f"\nProcess folder '{folder_name}'?", default=True):
                    safe_print("[yellow]Skipped by user[/yellow]")
                    break  # Exit this path's loop

            # Process the folder with error handling
            try:
                success = process_single_folder(
                    folder_number, folder_name, folder_path,
                    target, state, src_path, convert_mdx
                )

                if not success:
                    raise Exception("Folder processing returned False")

            except Exception as e:
                # Handle failure with cleanup and retry logic
                error_msg = str(e)
                safe_print(f"\n[bold red]{'━'*80}[/bold red]")
                safe_print(f"[bold red]✗ ERROR in folder {padded_number}:[/bold red] [red]{error_msg}[/red]")
                safe_print(f"[bold red]{'━'*80}[/bold red]")

                # Get target folder to clean up
                title = extract_title_from_folder(folder_name)
                final_title = state.get("folder_metadata", {}).get(padded_number, {}).get("title", title)
                target_folder_name = f"{padded_number}_{final_title}" if final_title else padded_number
                target_folder = target / target_folder_name

                # Delete entire target folder
                if target_folder.exists():
                    safe_print(f"[yellow]⟳ Cleaning up target folder:[/yellow] [dim]{target_folder}[/dim]")
                    try:
                        shutil.rmtree(target_folder)
                        safe_print(f"[green]  ✓ Target folder deleted[/green]")
                    except Exception as cleanup_error:
                        safe_print(f"[red]  ✗ Failed to delete target folder: {cleanup_error}[/red]")

                # Clear all disc state for this folder
                clear_folder_state(state, padded_number)

                # Increment retry count
                increment_folder_retry(state, padded_number, error_msg)
                new_retry_count = get_folder_retry_count(state, padded_number)

                # Update statistics
                update_path_statistics(state, src_path, success=False)

                # Save state
                save_copy_state(state)

                safe_print(f"[bold yellow]⟳ Retry {new_retry_count}/{MAX_RETRIES} for folder {padded_number}[/bold yellow]\n")

            # If not in auto mode, stop after one folder
            if not process_all:
                break

    # Print final summary
    print_final_summary(state, available_paths)

    return 0

def main() -> int:
    # Auto-detect disc/device first to derive disc number from label
    disk, rdisk = detect_dvd_device()
    if not disk or not rdisk:
        safe_print("[red]No DVD detected. Insert a disc and try again.[/red]")
        return 2

    # Try to extract a numeric identifier from the disc label
    label = get_disc_label(disk) or "disc"
    m = re.search(r"(\d{3,})", label)
    disc_number = m.group(1) if m else label

    # Prepare output paths per disc number
    disc_dir = ARCHIVE_DIR / f"disc_{disc_number}"
    # Clean previous content each run to avoid stale files when restarting
    if disc_dir.exists():
        try:
            for p in disc_dir.iterdir():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        p.unlink()
                    except Exception:
                        run_cmd(f"sudo rm -f {shlex.quote(str(p))}")
        except Exception:
            run_cmd(f"sudo rm -rf {shlex.quote(str(disc_dir))}")
            disc_dir.mkdir(parents=True, exist_ok=True)
    else:
        disc_dir.mkdir(parents=True, exist_ok=True)
    iso_path = disc_dir / f"disc_{disc_number}.iso"
    log_path = disc_dir / f"disc_{disc_number}.log"
    info_path = disc_dir / f"disc_{disc_number}_info.txt"
    sha_path = disc_dir / f"disc_{disc_number}.iso.sha256"
    ecc_path = disc_dir / f"disc_{disc_number}.iso.ecc"

    steps: Dict[str, StepState] = {
        "detect": StepState("Detect DVD device"),
        "unmount": StepState("Unmount DVD"),
        "ddrescue_fast": StepState("Image (fast pass)"),
        "ddrescue_retry": StepState("Image (retries)"),
        "checksum": StepState("Compute SHA-256"),
        "parity": StepState("Create dvdisaster parity"),
        "eject": StepState("Eject disc"),
    }

    archive = DiscArchive(
        disc_number=disc_number,
        start_time=datetime.now(timezone.utc).isoformat(),
        end_time=None,
        device_disk=None,
        device_rdisk=None,
        iso_path=str(iso_path),
        log_path=str(log_path),
        checksum_sha256=None,
        parity_path=str(ecc_path),
        ddrescue_stats={},
        steps=steps,
        success=False,
    )

    table = Table(title=f"DVD Archiver - Disc {disc_number}")
    table.add_column("Step")
    table.add_column("Status")
    table.add_column("Message")

    def render_table() -> Table:
        t = Table(title=f"DVD Archiver - Disc {disc_number}")
        t.add_column("Step")
        t.add_column("Status")
        t.add_column("Message")
        status_style = {
            "pending": "grey50",
            "running": "yellow",
            "done": "green",
            "error": "red",
            "skipped": "blue",
        }
        for key in ["detect", "unmount", "ddrescue_fast", "ddrescue_retry", "checksum", "parity", "eject"]:
            s = steps[key]
            mark = {
                "pending": "[ ]",
                "running": "[~]",
                "done": "[x]",
                "error": "[!]",
                "skipped": "[-]",
            }[s.status]
            t.add_row(s.name, f"[{status_style[s.status]}]{mark} {s.status}[/]", s.message)
        return t

    # Use lower refresh rate on Windows to reduce ANSI code issues
    refresh_rate = 0.5 if IS_WINDOWS else 1
    with Live(render_table(), refresh_per_second=refresh_rate, console=console, auto_refresh=False) as live:
        # Helper to update and refresh in one call
        def update_live():
            live.update(render_table())
            live.refresh()

        # Detect (already done above)
        steps["detect"].status = "running"
        update_live()
        archive.device_disk = disk
        archive.device_rdisk = rdisk
        steps["detect"].status = "done"
        steps["detect"].message = f"{disk} ({label})"
        update_live()

        # Check tools availability
        missing_tools = []
        for tool in ["drutil", "diskutil", "ddrescue", "shasum"]:
            if not tool_available(tool):
                missing_tools.append(tool)
        if missing_tools:
            steps["detect"].status = "error"
            steps["detect"].message = f"Missing tools: {', '.join(missing_tools)}"
            update_live()
            safe_print(f"[red]Missing required tools: {', '.join(missing_tools)}. Install them and retry.[/red]")
            return 2

        # Save drutil status
        rc, drout, _ = run_cmd("drutil status")
        with open(info_path, "w") as f:
            f.write(drout)

        # Cache sudo to avoid stalls
        ensure_sudo_cached()

        # Unmount
        steps["unmount"].status = "running"
        update_live()
        ok_unmount = unmount_disk(disk)
        steps["unmount"].status = "done" if ok_unmount else "error"
        steps["unmount"].message = "unmounted" if ok_unmount else "failed"
        update_live()
        if not ok_unmount:
            safe_print("[yellow]Continuing even if unmount failed; macOS may auto-mount optical discs read-only.[/yellow]")

        # Imaging (mode-dependent)
        if DVD_MODE == "hdiutil":
            steps["ddrescue_fast"].status = "running"
            update_live()
            ok = hdiutil_image(rdisk, disc_dir / f"disc_{disc_number}", lambda: live.update(render_table()), steps)
            archive.ddrescue_stats = {}
            update_live()
            if not ok:
                safe_print("[red]Imaging failed with hdiutil.[/red]", highlight=False)
        else:
            steps["ddrescue_fast"].status = "running"
            update_live()
            dd_stats = ddrescue_fast_then_retry(rdisk, iso_path, log_path, lambda: live.update(render_table()), steps)
            archive.ddrescue_stats = dd_stats
            update_live()
            if steps["ddrescue_fast"].status == "error":
                safe_print("[red]Imaging failed during fast pass. Try 'sudo -v' before rerun to cache credentials.[/red]")

        if not (iso_path.exists() or (disc_dir / f"disc_{disc_number}.iso").exists()):
            safe_print("[red]ISO not created. Aborting subsequent steps.[/red]")
            archive.end_time = datetime.now(timezone.utc).isoformat()
            archive.success = False
            archive_db = load_archive_json()
            archive_db[disc_number] = {
                "disc": asdict(archive)
            }
            save_archive_json(archive_db)
            return 3

        # Ensure archive ownership (ddrescue may write as root)
        try:
            run_cmd(f"sudo chown -R {os.geteuid()}:{os.getegid()} {shlex.quote(str(disc_dir))}")
        except Exception:
            pass

        # checksum
        steps["checksum"].status = "running"
        update_live()
        checksum = compute_sha256(iso_path) if iso_path.exists() else ""
        if checksum:
            steps["checksum"].status = "done"
            steps["checksum"].message = checksum[:16] + "..."
            archive.checksum_sha256 = checksum
            with open(sha_path, "w") as f:
                f.write(checksum + "  " + str(iso_path.name) + "\n")
        else:
            steps["checksum"].status = "error"
            steps["checksum"].message = "failed"
        update_live()

        # parity (optional)
        steps["parity"].status = "running"
        update_live()
        parity_ok = run_dvdisaster(iso_path, ecc_path)
        if parity_ok:
            steps["parity"].status = "done"
            steps["parity"].message = "created"
        else:
            steps["parity"].status = "skipped"
            steps["parity"].message = "dvdisaster not available or failed"
        update_live()

        # eject
        steps["eject"].status = "running"
        update_live()
        rc, _, _ = run_cmd(f"diskutil eject {shlex.quote(disk)}")
        steps["eject"].status = "done" if rc == 0 else "error"
        steps["eject"].message = "ejected" if rc == 0 else "failed"
        update_live()

    archive.end_time = datetime.now(timezone.utc).isoformat()
    archive.success = all(steps[k].status == "done" for k in steps)

    # Persist to JSON archive
    archive_db = load_archive_json()
    archive_db[disc_number] = {
        "disc": asdict(archive)
    }
    save_archive_json(archive_db)

    safe_print(Panel.fit(Text(f"Disc {disc_number} {'archived successfully' if archive.success else 'completed with issues'}", style="green" if archive.success else "yellow")))
    safe_print(f"Outputs in: {disc_dir}")
    return 0 if archive.success else 3


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="DVD Archiver - Create archival images or copy existing DVD images with checksums and parity"
    )
    parser.add_argument(
        "-c", "--copy",
        action="store_true",
        help="Copy mode: Process existing DVD images from source folders (Windows-compatible)"
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="Process all folders automatically without user confirmation (use with -c)"
    )
    parser.add_argument(
        "-conv", "--convert",
        action="store_true",
        help="Convert MDX files to ISO during copy (requires IAT or AnyToISO tool, use with -c)"
    )
    parser.add_argument(
        "-start", "--start-from",
        type=str,
        default=None,
        help="Start processing from a specific folder number (e.g., 696 or 0696), clearing its state and overwriting files (use with -c and -a)"
    )

    args = parser.parse_args()

    try:
        if args.copy:
            sys.exit(copy_mode_main(process_all=args.all, convert_mdx=args.convert, start_from=args.start_from))
        else:
            # Check if running on Windows in imaging mode (not supported)
            if IS_WINDOWS:
                safe_print("[red]ERROR: DVD imaging mode is not supported on Windows.[/red]")
                safe_print("[yellow]Use -c/--copy mode to process existing DVD images instead.[/yellow]")
                safe_print("\nUsage: python dvd_archiver.py -c")
                sys.exit(1)
            sys.exit(main())
    except KeyboardInterrupt:
        safe_print("\n[red]Interrupted by user[/red]")
        sys.exit(130)


