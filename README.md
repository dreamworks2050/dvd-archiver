# DVD Archiver (macOS)

A guided, color TUI for creating archival images of factory video DVDs at high speed with resilience, plus checksums, optional error-correction parity, and a persistent JSON archive of metadata.

## Features

- Per-disc guided workflow with color statuses and step checkboxes
- Device detection and safe unmount
- Fast ddrescue imaging (fast pass + limited retries) with live speed and progress updates
- SHA-256 checksum and saved `.sha256` file
- Optional `dvdisaster` parity (`.ecc`) for long-term data resilience
- Eject on completion
- JSON archive at `~/DVD_Archive/archive_log.json` with per-disc metadata
- Outputs per disc in `~/DVD_Archive/disc_<number>/`

## Requirements

- macOS (tested on Apple Silicon)
- Tools:
  - `ddrescue` (GNU ddrescue)
  - `drutil` and `diskutil` (built-in with macOS)
  - `dvdisaster` (optional, for parity)
  - `shasum` (built-in)
- Python 3.9+ with `rich`

Install external tools via Homebrew:
```bash
brew install ddrescue dvdisaster
```

## Setup (with uv)

```bash
brew install uv
cd /Users/macbook/MCP_test
uv venv dvdarchiver
source dvdarchiver/bin/activate
uv pip install -r requirements.txt
```

## Usage

1) Insert a DVD.
2) Optional: set a custom output base path and mode via `.env` in the project root:
```bash
echo "DVD_ARCHIVE_BASE=/Users/macbook/DVD_Archive" > .env
echo "DVD_MODE=hdiutil" >> .env   # or ddrescue (default)
```
3) Run the tool:
```bash
uv run --python dvdarchiver/bin/python python dvd_archiver.py
```
4) The TUI will guide you through detection, unmount, imaging, checksum, parity, and eject.

Tips:
- For maximum throughput, pre-authorize sudo so ddrescue can run without interruption:
```bash
sudo -v
```
- If the fast pass fails due to sudo permissions, the tool will retry interactively.
- Ensure you write to a fast internal SSD. Avoid network locations.

## Outputs per disc

`$DVD_ARCHIVE_BASE/disc_<number>/` will contain:
- `disc_<number>.iso` — full 2048-byte/sector image (user data)
- `disc_<number>.log` — ddrescue log
- `disc_<number>_info.txt` — `drutil status` snapshot
- `disc_<number>.iso.sha256` — checksum file
- `disc_<number>.iso.ecc` — dvdisaster parity (if dvdisaster is installed)

Global archive:
- `$DVD_ARCHIVE_BASE/archive_log.json` — JSON database of all processed discs, status, paths, checksums, and ddrescue summary lines

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
