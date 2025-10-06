# DVD Archiver

A cross-platform guided TUI for DVD archival with two modes:
- **Imaging Mode (macOS)**: Create archival images from physical DVD drives
- **Copy Mode (Windows/macOS)**: Process existing DVD image files with checksums and parity

Both modes provide live progress updates, SHA-256 checksums, optional error-correction parity, and persistent JSON metadata.

## Features

### Imaging Mode (macOS only)
- Per-disc guided workflow with color statuses and step checkboxes
- Device detection and safe unmount
- Fast ddrescue imaging (fast pass + limited retries) with live speed and progress updates
- SHA-256 checksum and saved `.sha256` file
- Optional `dvdisaster` parity (`.ecc`) for long-term data resilience
- Eject on completion
- JSON archive at `~/DVD_Archive/archive_log.json` with per-disc metadata
- Outputs per disc in `~/DVD_Archive/disc_<number>/`

### Copy Mode (Windows/macOS)
- Processes existing DVD image files from source folders
- Automatically finds lowest-numbered unprocessed folder
- Multi-disc set support (multiple files per folder)
- SHA-256 checksum generation for each file
- Optional `dvdisaster` parity creation
- Resumable operations with state persistence
- Smart folder naming: `{4-digit-number}_{title}`
- State tracking in `copy_state.json`

## Requirements

### Imaging Mode (macOS)
- macOS (tested on Apple Silicon)
- Tools:
  - `ddrescue` (GNU ddrescue)
  - `drutil` and `diskutil` (built-in with macOS)
  - `dvdisaster` (optional, for parity)
  - `shasum` (built-in)
- Python 3.9+ with `rich` and `python-dotenv`

Install external tools via Homebrew:
```bash
brew install ddrescue dvdisaster
```

### Copy Mode (Windows/macOS)
- Windows 10+ or macOS
- Python 3.9+
- `dvdisaster` (optional, for parity creation)
  - **Windows**: Download from [speed47/dvdisaster releases](https://github.com/speed47/dvdisaster/releases) (use `win32-portable.zip`)
  - **macOS**: `brew install dvdisaster` or download .dmg from releases
- Python packages: `rich`, `python-dotenv`

## Setup

### macOS (with uv - recommended)

```bash
brew install uv
cd /path/to/dvd-archiver
uv venv dvdarchiver
source dvdarchiver/bin/activate
uv pip install -r requirements.txt
```

### Windows (or macOS without uv)

```bash
cd C:\path\to\dvd-archiver
python -m venv dvdarchiver
# Windows:
dvdarchiver\Scripts\activate
# macOS/Linux:
source dvdarchiver/bin/activate

pip install -r requirements.txt
```

#### Optional: Install dvdisaster on Windows

For error-correction parity file creation:

1. Download the latest `win32-portable.zip` from [dvdisaster releases](https://github.com/speed47/dvdisaster/releases/latest)
2. Extract the zip file to a folder (e.g., `C:\Program Files\dvdisaster\`)
3. Add the folder to your system PATH:
   - Open System Properties → Advanced → Environment Variables
   - Edit the `Path` variable under System Variables
   - Add the path to the dvdisaster folder
4. Verify installation: `dvdisaster --version`

**Alternative**: Place `dvdisaster.exe` in the same folder as `dvd_archiver.py`

## Configuration

Create a `.env` file in the project root (copy from `.env.example`):

### For Imaging Mode (macOS)
```bash
DVD_ARCHIVE_BASE=/Users/macbook/DVD_Archive
DVD_MODE=ddrescue  # or hdiutil
```

### For Copy Mode (Windows/macOS)
```bash
# Comma-separated source paths
SOURCE_PATHS=E:\SM_DVDS,F:\MORE_DVDS

# Target destination
TARGET_PATH=I:\
```

## Usage

### Imaging Mode (macOS only)

1) Insert a DVD.
2) Pre-authorize sudo for uninterrupted operation:
```bash
sudo -v
```
3) Run the tool:
```bash
# With uv:
uv run --python dvdarchiver/bin/python python dvd_archiver.py

# Or with activated venv:
python dvd_archiver.py
```
4) The TUI will guide you through detection, unmount, imaging, checksum, parity, and eject.

Tips:
- Ensure you write to a fast internal SSD. Avoid network locations.
- If the fast pass fails due to sudo permissions, the tool will retry interactively.

### Copy Mode (Windows/macOS)

1) Configure `SOURCE_PATHS` and `TARGET_PATH` in `.env`
2) Run with `-c` option:
```bash
# Windows:
python dvd_archiver.py -c

# macOS with uv:
uv run --python dvdarchiver/bin/python python dvd_archiver.py -c

# macOS with venv:
python dvd_archiver.py -c
```
3) The tool will:
   - Validate source paths
   - Find the lowest-numbered unprocessed folder
   - Prompt you to confirm processing
   - Copy files to target with checksums and parity
   - Save state to `copy_state.json`

4) To process next folder, simply run again with `-c`

**Copy Mode Workflow:**
- Source folders should be numbered (e.g., `042 Movie Title`, `100-Series Name`)
- The tool extracts the first numeric sequence as the disc number
- Target folders are created as `{4-digit-number}_{title}`
- Single-disc folders: `0042.iso`
- Multi-disc folders: `0042_disc1.iso`, `0042_disc2.iso`, etc.
- State is saved only after successful completion
- You can resume from where you left off if interrupted

## Output Structure

### Imaging Mode Outputs

`$DVD_ARCHIVE_BASE/disc_<number>/` will contain:
- `disc_<number>.iso` — full 2048-byte/sector image (user data)
- `disc_<number>.log` — ddrescue log
- `disc_<number>_info.txt` — `drutil status` snapshot
- `disc_<number>.iso.sha256` — checksum file
- `disc_<number>.iso.ecc` — dvdisaster parity (if dvdisaster is installed)

Global archive:
- `$DVD_ARCHIVE_BASE/archive_log.json` — JSON database of all processed discs, status, paths, checksums, and ddrescue summary lines

### Copy Mode Outputs

`$TARGET_PATH/{number}_{title}/` will contain:
- `{number}.iso` or `{number}_disc{N}.iso` — copied DVD image(s)
- `{number}.iso.sha256` or `{number}_disc{N}.iso.sha256` — checksum file(s)
- `{number}.iso.ecc` or `{number}_disc{N}.iso.ecc` — dvdisaster parity (if available)

State tracking:
- `copy_state.json` — tracks processed folders and operation history (in project folder)

## Notes on archival fidelity

- Consumer drives cannot reproduce CSS keys or factory lead-in/out data. A full 2048-byte/sector dump preserves all user data (UDF/ISO9660 + VIDEO_TS) used for playback and is the accepted archival approach with commodity hardware.
- For dual-layer re-burning with original layer break, you may later use ImgBurn (Windows) to generate a small `.mds`/`.dvd` control file from the ISO. This is optional for archival; ISO remains the canonical master here.

## Troubleshooting

- No DVD detected: ensure the disc is inserted; try `drutil status` and `diskutil list`.
- Unmount failed: macOS may auto-mount read-only; imaging can still proceed.
- Slow speeds: use a USB-3 drive and write to an internal SSD; close heavy I/O apps.
- dvdisaster missing: install with `brew install dvdisaster`, or proceed without parity.

## License

MIT
