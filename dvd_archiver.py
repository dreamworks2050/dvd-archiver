#!/usr/bin/env python3
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Callable
from collections import deque
import shutil


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


console = Console()
load_dotenv()


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


ARCHIVE_BASE = os.getenv("DVD_ARCHIVE_BASE", str(Path.home() / "DVD_Archive"))
# Normalize base path to absolute path (expand ~ and env vars)
ARCHIVE_BASE = os.path.abspath(os.path.expanduser(os.path.expandvars(ARCHIVE_BASE)))
DVD_MODE = os.getenv("DVD_MODE", "ddrescue").strip().lower()
ARCHIVE_DIR = Path(ARCHIVE_BASE)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
JSON_ARCHIVE = ARCHIVE_DIR / "archive_log.json"


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


def run_cmd(cmd: str, check: bool = False, capture: bool = True, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
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
        console.print("[yellow]Requesting sudo to cache credentials...[/yellow]")
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
    rc, out, err = run_cmd(f"shasum -a 256 {shlex.quote(str(path))}")
    if rc == 0 and out:
        return out.split()[0]
    return ""


def tool_available(name: str) -> bool:
    rc, out, _ = run_cmd(f"command -v {shlex.quote(name)}")
    return rc == 0 and out.strip() != ""


def run_dvdisaster(path: Path, parity_out: Path, percent: int = 10) -> bool:
    if not tool_available("dvdisaster"):
        return False
    rc, out, err = run_cmd(f"dvdisaster -i {shlex.quote(str(path))} -m RS02 -p {percent} -o {shlex.quote(str(parity_out))}")
    return rc == 0


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


def main() -> int:
    # Auto-detect disc/device first to derive disc number from label
    disk, rdisk = detect_dvd_device()
    if not disk or not rdisk:
        console.print("[red]No DVD detected. Insert a disc and try again.[/red]")
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

    with Live(render_table(), refresh_per_second=4, console=console) as live:
        # Detect (already done above)
        steps["detect"].status = "running"
        live.update(render_table())
        archive.device_disk = disk
        archive.device_rdisk = rdisk
        steps["detect"].status = "done"
        steps["detect"].message = f"{disk} ({label})"
        live.update(render_table())

        # Check tools availability
        missing_tools = []
        for tool in ["drutil", "diskutil", "ddrescue", "shasum"]:
            if not tool_available(tool):
                missing_tools.append(tool)
        if missing_tools:
            steps["detect"].status = "error"
            steps["detect"].message = f"Missing tools: {', '.join(missing_tools)}"
            live.update(render_table())
            console.print(f"[red]Missing required tools: {', '.join(missing_tools)}. Install them and retry.[/red]")
            return 2

        # Save drutil status
        rc, drout, _ = run_cmd("drutil status")
        with open(info_path, "w") as f:
            f.write(drout)

        # Cache sudo to avoid stalls
        ensure_sudo_cached()

        # Unmount
        steps["unmount"].status = "running"
        live.update(render_table())
        ok_unmount = unmount_disk(disk)
        steps["unmount"].status = "done" if ok_unmount else "error"
        steps["unmount"].message = "unmounted" if ok_unmount else "failed"
        live.update(render_table())
        if not ok_unmount:
            console.print("[yellow]Continuing even if unmount failed; macOS may auto-mount optical discs read-only.[/yellow]")

        # Imaging (mode-dependent)
        if DVD_MODE == "hdiutil":
            steps["ddrescue_fast"].status = "running"
            live.update(render_table())
            ok = hdiutil_image(rdisk, disc_dir / f"disc_{disc_number}", lambda: live.update(render_table()), steps)
            archive.ddrescue_stats = {}
            live.update(render_table())
            if not ok:
                console.print("[red]Imaging failed with hdiutil.[/red]", highlight=False)
        else:
            steps["ddrescue_fast"].status = "running"
            live.update(render_table())
            dd_stats = ddrescue_fast_then_retry(rdisk, iso_path, log_path, lambda: live.update(render_table()), steps)
            archive.ddrescue_stats = dd_stats
            live.update(render_table())
            if steps["ddrescue_fast"].status == "error":
                console.print("[red]Imaging failed during fast pass. Try 'sudo -v' before rerun to cache credentials.[/red]")

        if not (iso_path.exists() or (disc_dir / f"disc_{disc_number}.iso").exists()):
            console.print("[red]ISO not created. Aborting subsequent steps.[/red]")
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
        live.update(render_table())
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
        live.update(render_table())

        # parity (optional)
        steps["parity"].status = "running"
        live.update(render_table())
        parity_ok = run_dvdisaster(iso_path, ecc_path)
        if parity_ok:
            steps["parity"].status = "done"
            steps["parity"].message = "created"
        else:
            steps["parity"].status = "skipped"
            steps["parity"].message = "dvdisaster not available or failed"
        live.update(render_table())

        # eject
        steps["eject"].status = "running"
        live.update(render_table())
        rc, _, _ = run_cmd(f"diskutil eject {shlex.quote(disk)}")
        steps["eject"].status = "done" if rc == 0 else "error"
        steps["eject"].message = "ejected" if rc == 0 else "failed"
        live.update(render_table())

    archive.end_time = datetime.now(timezone.utc).isoformat()
    archive.success = all(steps[k].status == "done" for k in steps)

    # Persist to JSON archive
    archive_db = load_archive_json()
    archive_db[disc_number] = {
        "disc": asdict(archive)
    }
    save_archive_json(archive_db)

    console.print(Panel.fit(Text(f"Disc {disc_number} {'archived successfully' if archive.success else 'completed with issues'}", style="green" if archive.success else "yellow")))
    console.print(f"Outputs in: {disc_dir}")
    return 0 if archive.success else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted by user[/red]")
        sys.exit(130)


