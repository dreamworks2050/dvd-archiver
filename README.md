# DVD Archiver

A professional cross-platform TUI (Text User Interface) tool for creating and managing archival-quality DVD images with error correction and integrity verification.

## Overview

DVD Archiver provides two complementary modes for different archival workflows:

- **🖥️ Imaging Mode (macOS)**: Direct capture from physical DVD drives with live progress tracking
- **📁 Copy Mode (Windows/macOS)**: Batch processing of existing DVD image files with automated organization

Both modes feature:
- ✅ Real-time progress visualization with speed metrics
- ✅ SHA-256 cryptographic checksums for integrity verification
- ✅ Optional RS02 error-correction parity (10% redundancy via dvdisaster)
- ✅ Persistent JSON metadata for archival tracking
- ✅ Resumable operations with state management
- ✅ Cross-platform Python implementation

## Features

### Imaging Mode (macOS only)

**Automated DVD Capture Workflow:**
- 🔍 **Smart Device Detection**: Auto-detects external DVD drives via `drutil` and `diskutil`
  - Validates physical external disks (3.5-9.5 GB capacity range)
  - Extracts disc number from volume label (supports 3+ digit patterns)
  - Displays device path, BSD name, and capacity

- 📀 **Dual Imaging Methods**:
  - **ddrescue mode** (default): Two-pass strategy (fast `-n` + retry `-r3`)
    - 2048-byte blocks, 16384-byte cluster size
    - Live speed tracking with 20-sample rolling average
    - Detailed `.log` file for recovery tracking
  - **hdiutil mode**: macOS native UDTO format with `.cdr` → `.iso` conversion

- 🔒 **Data Integrity**:
  - SHA-256 checksum computed via `shasum -a 256`
  - Saves `.sha256` sidecar file for verification
  - Optional RS02 parity (10% overhead) for bit-rot protection

- 🎯 **Live TUI Dashboard**:
  - Color-coded step status (pending/running/done/error/skipped)
  - Real-time speed (MB/s) and progress percentage
  - Elapsed time tracking
  - Interactive sudo credential caching

- 📤 **Auto-Eject**: Safely ejects disc after completion

- 📊 **Comprehensive Metadata**: JSON archive at `~/DVD_Archive/archive_log.json` includes:
  - Device paths, BSD names, capacities
  - ISO paths, checksums, parity paths
  - ddrescue statistics (rescued bytes, errors, run time)
  - Timestamps for each operation

### Copy Mode (Windows/macOS)

**Batch DVD Image Processing:**

- 🗂️ **Intelligent Folder Discovery**:
  - Scans multiple source paths (comma-separated in `.env`)
  - Extracts disc numbers from folder names (e.g., `042 Title`, `100-Series`)
  - Automatically finds lowest-numbered unprocessed folder
  - Supports resumable operations via `copy_state.json`

- 🔢 **Multi-Disc Set Support**:
  - Detects all DVD image files in folder (`.iso`, `.img`, `.cdr`, `.mdx`)
  - Single disc: `{number}.iso` (e.g., `0042.iso`)
  - Multi-disc: `{number}_disc{N}.iso` (e.g., `0042_disc1.iso`, `0042_disc2.iso`)
  - Preserves original file extensions

- ✨ **Smart Naming Convention**:
  - Target folders: `{4-digit-number}_{title}` (e.g., `0042_Movie_Title`)
  - Zero-padded numbers for consistent sorting
  - Underscores replace spaces for compatibility

- 🔐 **Cross-Platform Checksums**:
  - **Windows**: Pure Python `hashlib.sha256()` with 8MB chunked reading
  - **macOS/Linux**: Native `shasum -a 256` command
  - SHA-256 `.sha256` sidecar files for each image

- 🛡️ **Error-Correction Parity**:
  - Windows-compatible dvdisaster integration (speed47 fork)
  - RS02 method with 10% redundancy
  - Graceful fallback if dvdisaster unavailable

- 💾 **State Persistence**:
  - Atomic JSON commits to `copy_state.json`
  - Tracks processed files, checksums, parity paths, timestamps
  - Folder-level status (completed/failed)
  - Per-source-path statistics (folders processed, discs count)
  - Resume capability if interrupted

- 📈 **Progress Tracking**:
  - Real-time TUI with step-by-step status
  - User confirmation prompts before processing
  - Detailed operation history

## Requirements

### Imaging Mode (macOS)

**Operating System:**
- macOS 10.15+ (tested on Apple Silicon M1/M2/M3)
- External USB DVD drive (USB 3.0 recommended for speed)

**System Tools:**
- `ddrescue` (GNU ddrescue) - disk imaging with error recovery
- `drutil` - built-in macOS optical drive utility
- `diskutil` - built-in macOS disk management utility
- `shasum` - built-in cryptographic hash utility
- `dvdisaster` (optional) - error-correction parity generation

**Python Environment:**
- Python 3.9 or higher
- `rich` - TUI rendering and progress display
- `python-dotenv` - environment configuration

**Install External Tools via Homebrew:**
```bash
brew install ddrescue dvdisaster
```

### Copy Mode (Windows/macOS)

**Operating System:**
- Windows 10/11 (64-bit) or macOS 10.15+

**Python Environment:**
- Python 3.9+ with packages:
  - `rich` - TUI rendering
  - `python-dotenv` - configuration management

**Optional: dvdisaster for Error-Correction Parity**
- **Windows**:
  - Download from [speed47/dvdisaster releases](https://github.com/speed47/dvdisaster/releases)
  - Use `win32-portable.zip` for portable installation
  - **Note**: This project includes a bundled copy in `dvdisaster/` folder
- **macOS**:
  - Install via Homebrew: `brew install dvdisaster`
  - Or download .dmg from releases

**Disk Space:**
- Source DVDs: 3.5-9.5 GB per disc
- Target location: Same size + ~10% for parity files
- Temporary space for checksumming

## Installation & Setup

### Quick Start

#### macOS (with uv - recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer:

```bash
# Install uv
brew install uv

# Clone repository
git clone https://github.com/dreamworks2050/dvd-archiver.git
cd dvd-archiver

# Create virtual environment and install dependencies
uv venv dvdarchiver
source dvdarchiver/bin/activate
uv pip install -r requirements.txt

# Install system tools for imaging mode
brew install ddrescue dvdisaster
```

#### Windows

```bash
# Clone repository
git clone https://github.com/dreamworks2050/dvd-archiver.git
cd dvd-archiver

# Create virtual environment
python -m venv dvdarchiver
dvdarchiver\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt
```

**Note**: Windows users can use the bundled `dvdisaster.exe` in the `dvdisaster/` folder, or install separately.

#### macOS (without uv)

```bash
# Clone repository
git clone https://github.com/dreamworks2050/dvd-archiver.git
cd dvd-archiver

# Create virtual environment
python3 -m venv dvdarchiver
source dvdarchiver/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install system tools for imaging mode
brew install ddrescue dvdisaster
```

### dvdisaster Installation (Optional but Recommended)

Error-correction parity adds 10% redundancy to protect against bit rot and media degradation.

#### Windows
This project includes a bundled copy of dvdisaster in the `dvdisaster/` folder. The tool will automatically detect and use it.

**Alternative manual installation:**
1. Download `win32-portable.zip` from [speed47/dvdisaster releases](https://github.com/speed47/dvdisaster/releases/latest)
2. Extract to a folder (e.g., `C:\Program Files\dvdisaster\`)
3. Add to system PATH:
   - System Properties → Advanced → Environment Variables
   - Edit `Path` under System Variables
   - Add dvdisaster folder path
4. Verify: `dvdisaster --version`

#### macOS
```bash
brew install dvdisaster
```

Or download the `.dmg` from [releases](https://github.com/speed47/dvdisaster/releases).

## Configuration

Create a `.env` file in the project root directory. Use `.env.example` as a template:

```bash
cp .env.example .env
```

### Imaging Mode Configuration (macOS)

```bash
# Output directory for DVD archives
DVD_ARCHIVE_BASE=/Users/macbook/DVD_Archive

# Imaging method: ddrescue (default, recommended) or hdiutil
DVD_MODE=ddrescue
```

**Configuration Options:**

| Variable | Description | Default | Options |
|----------|-------------|---------|---------|
| `DVD_ARCHIVE_BASE` | Base directory for DVD archives | `~/DVD_Archive` | Any writable path |
| `DVD_MODE` | Imaging method | `ddrescue` | `ddrescue`, `hdiutil` |

**Imaging Method Comparison:**

- **ddrescue** (recommended):
  - Two-pass strategy: fast scan + targeted retry
  - Superior error recovery
  - Detailed logging for problem sectors
  - Live progress tracking

- **hdiutil**:
  - Native macOS tool (UDTO format)
  - No retry mechanism
  - Faster for pristine discs
  - Less verbose output

### Copy Mode Configuration (Windows/macOS)

```bash
# Comma-separated list of source directories containing DVD folders
SOURCE_PATHS=E:\SM_DVDS,F:\MORE_DVDS,G:\ARCHIVE

# Target directory for processed archives
TARGET_PATH=I:\DVD_Archive
```

**Configuration Options:**

| Variable | Description | Example |
|----------|-------------|---------|
| `SOURCE_PATHS` | Source folders with DVD images (comma-separated) | `E:\DVDS,F:\BACKUP` |
| `TARGET_PATH` | Destination for organized archives | `I:\Archive` |

**Path Requirements:**
- Source paths must be readable directories
- Target path must be writable
- Network paths supported but slower
- UNC paths supported on Windows (`\\server\share`)

## Usage

### Imaging Mode (macOS only)

**Step-by-Step Workflow:**

1. **Insert DVD** into external drive

2. **Pre-authorize sudo** (recommended to avoid interruptions):
   ```bash
   sudo -v
   ```

3. **Activate virtual environment**:
   ```bash
   source dvdarchiver/bin/activate
   ```

4. **Run the imaging tool**:
   ```bash
   # With uv:
   uv run --python dvdarchiver/bin/python python dvd_archiver.py

   # Or with activated venv:
   python dvd_archiver.py
   ```

5. **Follow TUI prompts**:
   - Device detection (auto-detects DVD drive)
   - Volume label extraction (disc number)
   - Disk unmounting
   - Imaging progress (live speed/percentage)
   - SHA-256 checksum calculation
   - Optional parity file creation
   - Auto-eject

**Performance Tips:**
- ✅ Use USB 3.0 or Thunderbolt DVD drives for maximum speed
- ✅ Write to internal SSD (avoid network/external drives)
- ✅ Close resource-intensive applications
- ✅ Clean discs with microfiber cloth before imaging
- ⚠️ Network destinations add significant overhead

**Troubleshooting:**
- **No DVD detected**: Verify disc insertion, check `drutil status` and `diskutil list`
- **Unmount failed**: macOS auto-mount is read-only, imaging proceeds anyway
- **Slow speeds**: Check USB connection, try different port, verify destination drive speed
- **Permission errors**: Run `sudo -v` before starting, or tool will prompt interactively
- **dvdisaster missing**: Install via `brew install dvdisaster` or skip parity step

### Copy Mode (Windows/macOS)

**Step-by-Step Workflow:**

1. **Configure `.env` file**:
   ```bash
   SOURCE_PATHS=E:\SM_DVDS,F:\MORE_DVDS
   TARGET_PATH=I:\DVD_Archive
   ```

2. **Activate virtual environment**:
   ```bash
   # Windows:
   dvdarchiver\Scripts\activate

   # macOS:
   source dvdarchiver/bin/activate
   ```

3. **Run copy mode**:
   ```bash
   # Windows:
   python dvd_archiver.py -c

   # macOS with uv:
   uv run --python dvdarchiver/bin/python python dvd_archiver.py -c

   # macOS with venv:
   python dvd_archiver.py -c
   ```

4. **Review and confirm**:
   - Tool scans source paths for numbered folders
   - Displays lowest unprocessed folder number
   - Shows folder contents preview
   - Prompts for confirmation

5. **Process continues automatically**:
   - Creates target folder with standardized naming
   - Copies all DVD image files
   - Generates SHA-256 checksums
   - Creates error-correction parity (if dvdisaster available)
   - Saves state to `copy_state.json`

6. **Process next folder**:
   - Run command again with `-c` flag
   - Tool automatically finds next unprocessed folder

**Copy Mode Behavior:**

| Source Folder Format | Extracted Number | Target Folder | File Naming |
|---------------------|------------------|---------------|-------------|
| `42 Movie Title` | `42` | `0042_Movie_Title` | `0042.iso` |
| `100-Series Name` | `100` | `0100_Series_Name` | `0100.iso` |
| `042 Multi (2 discs)` with `disc1.iso`, `disc2.iso` | `42` | `0042_Multi` | `0042_disc1.iso`, `0042_disc2.iso` |

**Supported File Extensions:**
- `.iso` - Standard ISO image
- `.img` - Raw disk image
- `.cdr` - macOS disk image
- `.mdx` - Media disc image

**State Management:**
- `copy_state.json` tracks all processed operations
- Atomic commits (only saved after full success)
- Resume capability if interrupted
- Per-folder retry tracking
- Source path statistics

**Advanced Usage:**
```bash
# Resume from previous state (automatic)
python dvd_archiver.py -c

# Start fresh (delete copy_state.json first)
del copy_state.json  # Windows
rm copy_state.json   # macOS
python dvd_archiver.py -c
```

## Output Structure

### Imaging Mode Outputs

**Per-Disc Directory:** `$DVD_ARCHIVE_BASE/disc_<number>/`

```
~/DVD_Archive/disc_042/
├── disc_042.iso              # Full DVD image (2048 bytes/sector)
├── disc_042.log              # ddrescue recovery log (ddrescue mode only)
├── disc_042_info.txt         # drutil status snapshot
├── disc_042.iso.sha256       # SHA-256 checksum file
└── disc_042.iso.ecc          # RS02 parity file (if dvdisaster installed)
```

**File Descriptions:**

| File | Purpose | Size | Required |
|------|---------|------|----------|
| `disc_042.iso` | Complete DVD image with all user data | 3.5-9.5 GB | ✅ |
| `disc_042.log` | ddrescue recovery log for damaged sectors | ~1 KB | ddrescue mode |
| `disc_042_info.txt` | Drive/disc metadata snapshot | ~1 KB | ✅ |
| `disc_042.iso.sha256` | Cryptographic checksum for verification | ~100 bytes | ✅ |
| `disc_042.iso.ecc` | Error-correction parity (10% of ISO size) | 350-950 MB | Optional |

**Global Archive Metadata:** `$DVD_ARCHIVE_BASE/archive_log.json`

JSON database containing:
- Device information (path, BSD name, capacity)
- ISO paths and checksums
- Parity file locations
- ddrescue statistics (rescued bytes, errors, runtime)
- Timestamps for all operations
- Step-by-step status history

**Example archive_log.json entry:**
```json
{
  "042": {
    "device_path": "/dev/disk4",
    "bsd_name": "disk4",
    "capacity_bytes": 4700000000,
    "iso_path": "/Users/user/DVD_Archive/disc_042/disc_042.iso",
    "checksum": "a1b2c3d4e5f6...",
    "parity_path": "/Users/user/DVD_Archive/disc_042/disc_042.iso.ecc",
    "ddrescue_rescued": "4.7 GB",
    "ddrescue_errors": "0",
    "ddrescue_run_time": "12m 34s",
    "timestamp": "2025-10-06T10:30:45Z"
  }
}
```

### Copy Mode Outputs

**Per-Folder Directory:** `$TARGET_PATH/{number}_{title}/`

**Single-Disc Example:**
```
I:\DVD_Archive\0042_Movie_Title/
├── 0042.iso                  # DVD image file
├── 0042.iso.sha256           # SHA-256 checksum
└── 0042.iso.ecc              # RS02 parity (if dvdisaster available)
```

**Multi-Disc Example:**
```
I:\DVD_Archive\0100_Series_Name/
├── 0100_disc1.iso            # Disc 1 image
├── 0100_disc1.iso.sha256     # Disc 1 checksum
├── 0100_disc1.iso.ecc        # Disc 1 parity
├── 0100_disc2.iso            # Disc 2 image
├── 0100_disc2.iso.sha256     # Disc 2 checksum
└── 0100_disc2.iso.ecc        # Disc 2 parity
```

**File Descriptions:**

| File Type | Purpose | Generated By |
|-----------|---------|--------------|
| `.iso/.img/.cdr/.mdx` | DVD image (copied from source) | shutil.copy2 |
| `.sha256` | Cryptographic integrity verification | shasum/hashlib |
| `.ecc` | Error-correction parity data | dvdisaster |

**State Tracking:** `copy_state.json` (in project folder)

Comprehensive operation history including:
- Processed files with source/target paths
- Per-file checksums and parity locations
- Folder metadata (number, title, status)
- Per-source-path statistics
- Timestamps for all operations
- Retry tracking and error history

**Example copy_state.json structure:**

*Note: Checksums are stored for all files and will be overwritten if folders are re-processed.*

```json
{
  "processed_files": {
    "0042.iso": {
      "source_path": "E:\\DVDS\\042 Movie\\movie.iso",
      "target_path": "I:\\DVD_Archive\\0042_Movie_Title\\0042.iso",
      "folder_number": "0042",
      "folder_title": "Movie_Title",
      "checksum": "sha256_hash_here",
      "parity_path": "I:\\DVD_Archive\\0042_Movie_Title\\0042.iso.ecc",
      "all_steps_completed": true,
      "timestamp": "2025-10-06T10:30:45Z"
    }
  },
  "folder_metadata": {
    "0042": {
      "title": "Movie_Title",
      "retry_count": 0,
      "last_error": null,
      "status": "completed"
    }
  },
  "path_statistics": {
    "E:\\DVDS": {
      "folders_processed": 15,
      "folders_failed": 0,
      "discs_processed": 18
    }
  }
}
```

## Technical Notes

### Archival Fidelity & DVD Structure

**What This Tool Preserves:**
- ✅ All user data (UDF/ISO9660 filesystem + VIDEO_TS/AUDIO_TS)
- ✅ Complete playback capability
- ✅ All video, audio, subtitle, and menu data
- ✅ Chapter markers and navigation structure
- ✅ 2048 bytes per sector (standard DVD-Video format)

**What Consumer Drives Cannot Capture:**
- ❌ CSS encryption keys (requires special hardware)
- ❌ Factory lead-in/out data (not accessible via commodity drives)
- ❌ Region code enforcement data (not needed for playback)
- ❌ Copy protection schemes (unrelated to content preservation)

**Industry Standard Approach:**
The 2048-byte/sector ISO image is the accepted archival method for commodity hardware. This preserves all playable content and is compatible with:
- VLC Media Player
- MakeMKV (for format conversion)
- Virtual drive software (ImgBurn, Daemon Tools, etc.)
- Hardware DVD players (after burning)

**Layer Break Preservation (Dual-Layer DVDs):**
For re-burning dual-layer discs with original layer break positions:
1. Use ImgBurn (Windows) to generate `.mds` or `.dvd` control files from the ISO
2. These small metadata files contain layer break information
3. Optional for archival; ISO remains the canonical master

### Error Correction & Data Resilience

**dvdisaster RS02 Parity:**
- Adds 10% redundancy using Reed-Solomon error correction
- Can recover from bit rot, scratches, and media degradation
- Works even if original disc is lost (parity + damaged ISO)
- Industry-proven method used in professional archives

**Checksum Verification:**
- SHA-256 provides cryptographic integrity validation
- Detects any bit-level corruption or modification
- Essential for long-term archival verification
- Used by digital preservation institutions worldwide

**Recommended Archival Strategy:**
1. Create ISO with this tool
2. Generate SHA-256 checksum ✓
3. Create RS02 parity file ✓
4. Store ISO + checksum + parity on multiple media:
   - Primary: High-quality HDD/SSD
   - Backup: External drive (different manufacturer)
   - Off-site: Cloud storage or physical backup
5. Verify checksums annually
6. Refresh media every 5-10 years

## Command Reference

### Imaging Mode Commands (macOS)

```bash
# Standard imaging with ddrescue
python dvd_archiver.py

# Using hdiutil instead
# Edit .env: DVD_MODE=hdiutil
python dvd_archiver.py

# With uv (recommended)
uv run --python dvdarchiver/bin/python python dvd_archiver.py

# Pre-authorize sudo (recommended)
sudo -v && python dvd_archiver.py
```

### Copy Mode Commands (Windows/macOS)

```bash
# Process next unprocessed folder
python dvd_archiver.py -c

# Start fresh (delete state first)
del copy_state.json && python dvd_archiver.py -c  # Windows
rm copy_state.json && python dvd_archiver.py -c   # macOS

# Check configuration
cat .env  # macOS
type .env  # Windows
```

### Verification Commands

```bash
# Verify checksum (macOS/Linux)
shasum -a 256 -c disc_042.iso.sha256

# Verify checksum (Windows PowerShell)
$expected = (Get-Content disc_042.iso.sha256).Split()[0]
$actual = (Get-FileHash disc_042.iso -Algorithm SHA256).Hash
if ($expected -eq $actual) { "✓ Checksum valid" } else { "✗ Checksum mismatch" }

# Verify all files from copy_state.json (PowerShell)
$state = Get-Content copy_state.json | ConvertFrom-Json
foreach ($file in $state.processed_files.PSObject.Properties) {
    $checksum = $file.Value.checksum
    $path = $file.Value.target_path
    $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($checksum -eq $actual) {
        Write-Host "✓ $($file.Name)" -ForegroundColor Green
    } else {
        Write-Host "✗ $($file.Name) - MISMATCH!" -ForegroundColor Red
    }
}

# Verify all files from copy_state.json (Bash)
jq -r '.processed_files | to_entries[] | "\(.value.checksum)  \(.value.target_path)"' copy_state.json | while read hash path; do
    actual=$(shasum -a 256 "$path" | cut -d' ' -f1)
    if [ "$hash" = "$actual" ]; then
        echo "✓ $(basename "$path")"
    else
        echo "✗ $(basename "$path") - MISMATCH!"
    fi
done

# Check parity file status
dvdisaster -t disc_042.iso

# Test parity recovery
dvdisaster -t disc_042.iso --ecc disc_042.iso.ecc
```

## Troubleshooting

### Imaging Mode (macOS)

| Issue | Cause | Solution |
|-------|-------|----------|
| No DVD detected | Disc not inserted or drive issue | Check `drutil status` and `diskutil list`; verify external connection |
| Unmount failed | macOS auto-mounted read-only | Imaging proceeds anyway; this is normal behavior |
| Slow speeds (<5 MB/s) | USB 2.0 or slow drive | Use USB 3.0/Thunderbolt; write to internal SSD |
| Permission denied | Sudo credentials expired | Run `sudo -v` before imaging, or approve interactive prompt |
| ddrescue not found | Tool not installed | `brew install ddrescue` |
| dvdisaster missing | Optional tool not installed | `brew install dvdisaster` or skip parity step |
| Disc read errors | Scratched/damaged media | ddrescue will retry; check `.log` file for error map |

### Copy Mode (Windows/macOS)

| Issue | Cause | Solution |
|-------|-------|----------|
| Source path not found | Invalid path in .env | Verify `SOURCE_PATHS` are accessible; use absolute paths |
| Target path access denied | Write permission issue | Check folder permissions; run as administrator (Windows) |
| No folders found | Empty source or all processed | Check `copy_state.json`; verify folder naming convention |
| dvdisaster not working (Windows) | Not in PATH | Use bundled `dvdisaster/dvdisaster.exe` or add to PATH |
| Checksum mismatch | File corruption during copy | Re-run operation; check disk health |
| Out of disk space | Target drive full | Free up space or change `TARGET_PATH` |
| Unicode folder names | Special characters issue | Ensure UTF-8 support; avoid problematic characters |

### General Issues

```bash
# Check Python version (3.9+ required)
python --version

# Verify virtual environment
which python  # macOS
where python  # Windows

# Re-install dependencies
pip install --force-reinstall -r requirements.txt

# Check disk space
df -h  # macOS/Linux
wmic logicaldisk get size,freespace,caption  # Windows

# Test dvdisaster installation
dvdisaster --version

# View detailed logs
tail -f copy_state.json  # macOS
type copy_state.json  # Windows
```

## FAQ

**Q: Can I use this on Linux?**
A: Copy mode works on Linux. Imaging mode requires macOS-specific tools (drutil, diskutil).

**Q: How long does imaging take?**
A: 10-15 minutes per disc at USB 3.0 speeds (~8 MB/s). Damaged discs may take longer.

**Q: Can I pause and resume?**
A: Copy mode supports resume via `copy_state.json`. Imaging mode requires completion per disc.

**Q: What if dvdisaster fails?**
A: Tool continues without parity. You can manually create `.ecc` files later using `dvdisaster -i disc.iso -mRS02 -c`.

**Q: Are checksums portable across systems?**
A: Yes, SHA-256 is cross-platform. Verify with `shasum -a 256` (Unix) or PowerShell (Windows).

**Q: Can I archive Blu-rays?**
A: Not currently. This tool is optimized for DVD-Video discs (4.7/8.5 GB).

**Q: How do I verify parity files?**
A: `dvdisaster -t disc.iso --ecc disc.iso.ecc` tests recovery capability.

**Q: What's the difference between .iso, .img, and .cdr?**
A: All are raw disk images. Copy mode preserves original extensions; they're functionally identical for DVDs.

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request with detailed description

**Development setup:**
```bash
git clone https://github.com/dreamworks2050/dvd-archiver.git
cd dvd-archiver
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- **GNU ddrescue**: Robust data recovery tool
- **dvdisaster (speed47 fork)**: Windows-compatible error correction
- **Rich**: Beautiful TUI rendering
- **python-dotenv**: Configuration management

## Project Status

**Active Development**
- ✅ Imaging mode (macOS) - stable
- ✅ Copy mode (Windows/macOS) - stable
- 🚧 Linux imaging support - planned
- 🚧 Blu-ray support - under consideration

**Tested Configurations:**
- macOS 13+ (Apple Silicon & Intel)
- Windows 10/11 (64-bit)
- Python 3.9, 3.10, 3.11, 3.12
