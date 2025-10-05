# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DVD Archiver is a macOS TUI (Text User Interface) tool for creating archival images of factory video DVDs with resilience features. The tool orchestrates per-disc archival with live progress updates, checksums, optional error-correction parity, and persistent JSON metadata.

## Commands

### Environment Setup
```bash
# Install dependencies
brew install uv ddrescue dvdisaster

# Create and activate virtual environment
uv venv dvdarchiver
source dvdarchiver/bin/activate
uv pip install -r requirements.txt
```

### Running the Tool
```bash
# Run with uv (recommended)
uv run --python dvdarchiver/bin/python python dvd_archiver.py

# Or with activated venv
source dvdarchiver/bin/activate
python dvd_archiver.py
```

### Pre-execution Setup
```bash
# Cache sudo credentials to avoid interruptions during imaging
sudo -v
```

## Configuration

The tool uses `.env` for configuration (create in project root):
- `DVD_ARCHIVE_BASE`: Output directory (default: `~/DVD_Archive`)
- `DVD_MODE`: Imaging method, either `ddrescue` (default) or `hdiutil`

## Architecture

### Core Workflow (`dvd_archiver.py`)

The script follows a sequential step-based workflow with live TUI updates:

1. **Device Detection** (`detect_dvd_device`): Auto-detects DVD device using `drutil status` and `diskutil list`, validates external physical disks with 3.5-9.5 GB capacity
2. **Disc Labeling** (`get_disc_label`): Extracts volume name to derive disc number from label patterns (looks for 3+ digit sequences)
3. **Unmounting** (`unmount_disk`): Unmounts device before imaging
4. **Imaging**: Two modes available:
   - **ddrescue mode** (`ddrescue_fast_then_retry`): Fast pass (-n) + retry pass (-r3) with 2048-byte blocks, 16384-byte cluster size; tracks speed/progress via file size polling
   - **hdiutil mode** (`hdiutil_image`): Creates UDTO format, renames .cdr to .iso; parses puppetstrings output for progress
5. **Checksumming** (`compute_sha256`): SHA-256 hash via `shasum -a 256`
6. **Parity** (`run_dvdisaster`): Optional RS02 error-correction parity (10% default)
7. **Ejection**: Uses `diskutil eject`
8. **Archival**: Persists metadata to `archive_log.json` with atomic writes

### State Management

- `StepState`: Tracks individual step status (pending|running|done|error|skipped) with messages
- `DiscArchive`: Per-disc metadata including device paths, ISO path, checksum, ddrescue stats, timestamps
- Steps are updated in real-time during execution with Rich Live updates

### Progress Tracking

Both imaging modes implement live speed/progress updates:
- File size polling at 0.5s intervals
- Rolling 20-sample window for speed calculation (5-10 second average)
- Percentage calculation using total device bytes from `diskutil info` or `drutil status` blocks

### Sudo Handling

The tool attempts non-interactive sudo first (`sudo -n`) and falls back to interactive prompts on permission denial. `ensure_sudo_cached()` validates/refreshes sudo cache before imaging to prevent mid-operation stalls.

### Output Structure

Per-disc outputs go to `$DVD_ARCHIVE_BASE/disc_<number>/`:
- `disc_<number>.iso`: Full 2048-byte/sector image
- `disc_<number>.log`: ddrescue log (ddrescue mode only)
- `disc_<number>_info.txt`: `drutil status` snapshot
- `disc_<number>.iso.sha256`: Checksum file
- `disc_<number>.iso.ecc`: dvdisaster parity (if available)

Global archive metadata: `$DVD_ARCHIVE_BASE/archive_log.json`

### Dependencies

- **Rich**: TUI rendering (Console, Panel, Table, Live, Text)
- **python-dotenv**: Environment variable loading
- **External tools**: ddrescue, drutil, diskutil, shasum, dvdisaster (optional)

## Key Implementation Details

- **Device detection**: Primary via `drutil status` with fallback to `diskutil list` size heuristics
- **Disc numbering**: Extracted from volume label via regex `(\d{3,})`; disc directory cleaned on each run to avoid stale files
- **Progress rendering**: Live table updates at 4 Hz via Rich Live context
- **Error resilience**: ddrescue uses fast pass + limited retries; hdiutil has no retry mechanism
- **Archival format**: 2048-byte/sector ISO (UDF/ISO9660 + VIDEO_TS), standard for commodity hardware
