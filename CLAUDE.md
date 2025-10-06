# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DVD Archiver is a cross-platform TUI (Text User Interface) tool for creating archival images of factory video DVDs with resilience features. The tool supports two modes:

1. **Imaging Mode (macOS only)**: Creates archival images directly from physical DVD drives with live progress updates, checksums, optional error-correction parity, and persistent JSON metadata.
2. **Copy Mode (Windows/macOS)**: Processes existing DVD image files from source folders, copying them to target locations with checksums and parity files. Supports multi-disc sets and state persistence for resumable operations.

## Commands

### Environment Setup

#### macOS (Imaging Mode)
```bash
# Install dependencies
brew install uv ddrescue dvdisaster

# Create and activate virtual environment
uv venv dvdarchiver
source dvdarchiver/bin/activate
uv pip install -r requirements.txt
```

#### Windows (Copy Mode)
```bash
# Install dependencies (dvdisaster optional)
# Install Python 3.8+ and pip first

# Create virtual environment
python -m venv dvdarchiver
dvdarchiver\Scripts\activate
pip install -r requirements.txt

# Optional: Install dvdisaster for parity file creation
# Download Windows-compatible fork from: https://github.com/speed47/dvdisaster/releases
# Get win32-portable.zip, extract, and add to PATH or place dvdisaster.exe in project folder
```

### Running the Tool

#### Imaging Mode (macOS only)
```bash
# Cache sudo credentials to avoid interruptions
sudo -v

# Run with uv (recommended)
uv run --python dvdarchiver/bin/python python dvd_archiver.py

# Or with activated venv
source dvdarchiver/bin/activate
python dvd_archiver.py
```

#### Copy Mode (Windows/macOS)
```bash
# Windows
python dvd_archiver.py -c

# macOS
python dvd_archiver.py -c
# or with uv
uv run --python dvdarchiver/bin/python python dvd_archiver.py -c
```

## Configuration

The tool uses `.env` for configuration (create in project root):

### Imaging Mode (macOS)
- `DVD_ARCHIVE_BASE`: Output directory (default: `~/DVD_Archive`)
- `DVD_MODE`: Imaging method, either `ddrescue` (default) or `hdiutil`

### Copy Mode (Windows/macOS)
- `SOURCE_PATHS`: Comma-separated list of source directories containing DVD image folders (e.g., `E:\SM_DVDS,F:\MORE_DVDS`)
- `TARGET_PATH`: Target directory for processed DVD images (e.g., `I:\`)

## Architecture

### Platform Detection

The script detects the operating system at runtime:
- `IS_WINDOWS`: True if running on Windows
- `IS_MACOS`: True if running on macOS/Darwin

Imaging mode (default) is restricted to macOS only. Copy mode (-c) works on all platforms.

### Imaging Mode Workflow (macOS only)

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

### Copy Mode Workflow (Windows/macOS)

The copy mode (`-c` option) processes existing DVD image files with the following workflow:

1. **State Loading** (`load_copy_state`): Loads previously processed folders from `copy_state.json` in project folder
2. **Resume Prompt**: If previous state exists, asks user to continue or start fresh
3. **Path Validation** (`validate_source_paths`): Validates all SOURCE_PATHS are accessible
4. **Folder Discovery** (`find_lowest_numbered_folder`): Scans source paths for numbered folders, finds lowest unprocessed number
5. **User Confirmation**: Prompts user to confirm processing the selected folder
6. **File Discovery**: Finds all DVD image files (.iso, .img, .cdr) in the folder
7. **Target Folder Creation**: Creates target folder as `{number_4digits}_{title}` (e.g., `0042_MovieTitle`)
8. **File Copy**: Copies all image files with naming:
   - Single disc: `{number}.iso` (e.g., `0042.iso`)
   - Multi-disc: `{number}_disc{N}.iso` (e.g., `0042_disc1.iso`, `0042_disc2.iso`)
9. **Checksum Generation** (`compute_sha256`/`compute_sha256_python`): Creates SHA-256 checksum for each file
10. **Parity Creation** (`run_dvdisaster`): Optional RS02 parity files (if dvdisaster available)
11. **State Persistence** (`save_copy_state`): Saves completed operation to JSON (only after full success)

### Copy Mode State Management

- `CopyOperation`: Tracks per-folder operation with folder info, file list, checksums, parity paths, timestamps
- `copy_state.json`: Stores list of processed folders and operation history in project folder
- **Atomic commits**: Folders only added to processed list after all steps complete successfully
- **Resumable**: Can restart from same point if interrupted; prompts user to continue from previous state

### Cross-Platform Checksums

- **macOS/Linux**: Uses `shasum -a 256` command-line tool
- **Windows**: Uses Python's `hashlib.sha256()` with 8MB chunked reading for efficiency
- Automatic detection via `IS_WINDOWS` flag

### dvdisaster Integration

- **Tool detection**: Uses `where` on Windows, `command -v` on Unix-like systems
- **Command syntax**: Adapted for speed47/dvdisaster fork (Windows-compatible)
  - Windows: `dvdisaster -i "path" -mRS02 -c -n 10% -o "output"`
  - Unix: `dvdisaster -i path -mRS02 -c -n 10% -o output`
- **Graceful fallback**: If dvdisaster unavailable, parity step is skipped without error
- **Recommended source**: [speed47/dvdisaster](https://github.com/speed47/dvdisaster) - actively maintained fork with Windows support

## Key Implementation Details

### Imaging Mode
- **Device detection**: Primary via `drutil status` with fallback to `diskutil list` size heuristics
- **Disc numbering**: Extracted from volume label via regex `(\d{3,})`; disc directory cleaned on each run to avoid stale files
- **Progress rendering**: Live table updates at 4 Hz via Rich Live context
- **Error resilience**: ddrescue uses fast pass + limited retries; hdiutil has no retry mechanism
- **Archival format**: 2048-byte/sector ISO (UDF/ISO9660 + VIDEO_TS), standard for commodity hardware

### Copy Mode
- **Folder numbering**: Extracts first numeric sequence from folder name (e.g., "042 Movie Title" → "042")
- **Title extraction**: Removes leading numbers and separators from folder name
- **Multi-disc detection**: Multiple image files in one folder treated as multi-disc set
- **Naming convention**: 4-digit zero-padded numbers for consistent sorting
- **State persistence**: JSON atomic writes with .tmp intermediate file
- **File types supported**: .iso, .ISO, .img, .IMG, .cdr, .CDR
