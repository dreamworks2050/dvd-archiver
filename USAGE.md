# DVD Archiver - Detailed Usage Guide

This guide provides comprehensive examples and workflows for using DVD Archiver.

## Table of Contents

- [Quick Start](#quick-start)
- [Imaging Mode Examples](#imaging-mode-examples)
- [Copy Mode Examples](#copy-mode-examples)
- [Common Workflows](#common-workflows)
- [Advanced Usage](#advanced-usage)
- [Best Practices](#best-practices)

## Quick Start

### First-Time Setup

```bash
# Clone and setup (macOS with uv)
git clone https://github.com/dreamworks2050/dvd-archiver.git
cd dvd-archiver
brew install uv ddrescue dvdisaster
uv venv dvdarchiver
source dvdarchiver/bin/activate
uv pip install -r requirements.txt

# Create configuration
cp .env.example .env
# Edit .env with your preferences

# Test installation
python dvd_archiver.py --help
```

### Windows First-Time Setup

```powershell
# Clone and setup
git clone https://github.com/dreamworks2050/dvd-archiver.git
cd dvd-archiver
python -m venv dvdarchiver
dvdarchiver\Scripts\activate
pip install -r requirements.txt

# Configuration
copy .env.example .env
# Edit .env with your source and target paths

# Test (uses bundled dvdisaster)
python dvd_archiver.py -c
```

## Imaging Mode Examples

### Example 1: Basic DVD Imaging (macOS)

**Scenario:** Archive a single DVD to your home directory

```bash
# 1. Configure .env
echo "DVD_ARCHIVE_BASE=$HOME/DVD_Archive" > .env
echo "DVD_MODE=ddrescue" >> .env

# 2. Insert DVD and authorize sudo
sudo -v

# 3. Activate environment and run
source dvdarchiver/bin/activate
python dvd_archiver.py
```

**Output:**
```
~/DVD_Archive/
└── disc_042/
    ├── disc_042.iso        (4.7 GB)
    ├── disc_042.log        (1 KB - ddrescue log)
    ├── disc_042_info.txt   (1 KB - drive info)
    ├── disc_042.iso.sha256 (100 bytes)
    └── disc_042.iso.ecc    (470 MB - 10% parity)
```

### Example 2: Fast Imaging with hdiutil

**Scenario:** Archive pristine discs quickly using macOS native tools

```bash
# Configure for hdiutil
cat > .env << EOF
DVD_ARCHIVE_BASE=$HOME/DVD_Archive
DVD_MODE=hdiutil
EOF

# Run imaging
sudo -v
python dvd_archiver.py
```

**Performance:**
- ddrescue: ~10-15 min (includes retry passes)
- hdiutil: ~8-10 min (no retries, faster for clean discs)

### Example 3: Batch Imaging Multiple DVDs

```bash
#!/bin/bash
# batch_image.sh - Process multiple DVDs sequentially

source dvdarchiver/bin/activate

for i in {1..10}; do
    echo "=== Processing disc $i of 10 ==="
    echo "Insert disc and press Enter..."
    read

    sudo -v  # Refresh sudo
    python dvd_archiver.py

    echo "Disc $i complete. Remove disc."
    sleep 3
done

echo "All discs processed!"
```

### Example 4: Archive to External Drive

```bash
# Configure for external drive
cat > .env << EOF
DVD_ARCHIVE_BASE=/Volumes/Archive/DVD_Collection
DVD_MODE=ddrescue
EOF

# Create directory structure
mkdir -p /Volumes/Archive/DVD_Collection

# Run imaging
python dvd_archiver.py
```

## Copy Mode Examples

### Example 1: Basic Copy Operation (Windows)

**Scenario:** Process DVD images from external drive to network storage

```powershell
# Configure .env
@"
SOURCE_PATHS=E:\DVD_IMAGES,F:\MORE_DVDS
TARGET_PATH=\\NAS\Archive\DVDs
"@ | Out-File -FilePath .env -Encoding UTF8

# Activate and run
dvdarchiver\Scripts\activate
python dvd_archiver.py -c
```

**Folder Structure:**
```
E:\DVD_IMAGES\
├── 042 Movie Title\
│   └── movie.iso
├── 100 Series Name\
│   ├── disc1.iso
│   └── disc2.iso
└── 200 Another Movie\
    └── film.cdr

→ Processes to →

\\NAS\Archive\DVDs\
├── 0042_Movie_Title\
│   ├── 0042.iso
│   ├── 0042.iso.sha256
│   └── 0042.iso.ecc
├── 0100_Series_Name\
│   ├── 0100_disc1.iso
│   ├── 0100_disc1.iso.sha256
│   ├── 0100_disc1.iso.ecc
│   ├── 0100_disc2.iso
│   ├── 0100_disc2.iso.sha256
│   └── 0100_disc2.iso.ecc
└── 0200_Another_Movie\
    ├── 0200.cdr
    ├── 0200.cdr.sha256
    └── 0200.cdr.ecc
```

### Example 2: Resume After Interruption

**Scenario:** Power outage interrupted processing, resume from where you left off

```bash
# Check what was last processed
cat copy_state.json | jq '.folder_metadata | keys | last'
# Output: "0100"

# Resume - automatically picks up at 0200
python dvd_archiver.py -c

# Tool shows:
# "Found lowest unprocessed folder: 0200 (Another_Movie)"
# "Continue? [Y/n]:"
```

### Example 3: Process Specific Range

**Scenario:** Process only discs 100-200 from a large collection

```bash
# 1. Backup existing state
cp copy_state.json copy_state.backup.json

# 2. Create fresh state for range
rm copy_state.json

# 3. Process until reaching 200
python dvd_archiver.py -c
# When prompted for folder 201, press Ctrl+C

# 4. Restore original state if needed
mv copy_state.backup.json copy_state.json
```

### Example 4: Multiple Source Paths

**Scenario:** Combine DVDs from three different external drives

```bash
# .env configuration
SOURCE_PATHS=/Volumes/Drive1/DVDS,/Volumes/Drive2/MORE,/Volumes/Drive3/ARCHIVE
TARGET_PATH=/Volumes/MainArchive/DVD_Collection

# Statistics are tracked per source path
python dvd_archiver.py -c

# View statistics
cat copy_state.json | jq '.path_statistics'
# Output:
# {
#   "/Volumes/Drive1/DVDS": {
#     "folders_processed": 50,
#     "discs_processed": 65
#   },
#   "/Volumes/Drive2/MORE": {
#     "folders_processed": 30,
#     "discs_processed": 35
#   },
#   ...
# }
```

### Example 5: Batch Process All Folders

```bash
#!/bin/bash
# process_all.sh - Process all folders automatically

source dvdarchiver/bin/activate

while true; do
    # Run copy mode and capture output
    python dvd_archiver.py -c <<< "Y" || break

    # Small delay between operations
    sleep 2
done

echo "All folders processed!"
```

## Common Workflows

### Workflow 1: Complete Archival Pipeline

```bash
# 1. Image physical DVDs (macOS)
for i in {1..50}; do
    echo "Insert disc $i and press Enter"
    read
    sudo -v
    python dvd_archiver.py
done

# 2. Transfer to Windows machine for organization
# Copy ~/DVD_Archive/* to E:\Raw_DVDs\

# 3. Process and organize (Windows)
# .env:
# SOURCE_PATHS=E:\Raw_DVDs
# TARGET_PATH=I:\Organized_Archive

python dvd_archiver.py -c

# 4. Verify all checksums
cd I:\Organized_Archive
Get-ChildItem -Recurse -Filter *.sha256 | ForEach-Object {
    $expected = (Get-Content $_.FullName).Split()[0]
    $isoPath = $_.FullName -replace '\.sha256$', ''
    $actual = (Get-FileHash $isoPath -Algorithm SHA256).Hash
    if ($expected -eq $actual) {
        Write-Host "✓ $isoPath" -ForegroundColor Green
    } else {
        Write-Host "✗ $isoPath - MISMATCH!" -ForegroundColor Red
    }
}
```

### Workflow 2: Disaster Recovery Testing

```bash
# 1. Simulate damaged ISO (delete random sectors)
dd if=/dev/zero of=disc_042.iso bs=2048 seek=1000 count=100 conv=notrunc

# 2. Test parity recovery
dvdisaster -t disc_042.iso --ecc disc_042.iso.ecc

# Output shows:
# "Image can be fully recovered from error correction data"

# 3. Perform recovery
dvdisaster --fix disc_042.iso --ecc disc_042.iso.ecc -o disc_042_recovered.iso

# 4. Verify checksum
shasum -a 256 -c disc_042.iso.sha256
```

### Workflow 3: Archive to Cloud Storage

```bash
# 1. Process locally
python dvd_archiver.py -c

# 2. Upload with rclone (preserving structure)
rclone sync ~/DVD_Archive remote:dvd-archive \
    --include "*.iso" \
    --include "*.sha256" \
    --include "*.ecc" \
    --progress

# 3. Verify remote checksums
rclone check ~/DVD_Archive remote:dvd-archive \
    --download --one-way
```

## Advanced Usage

### Custom Naming Schemes

```python
# Modify dvd_archiver.py for custom naming

# Original:
target_folder_name = f"{folder_number:04d}_{folder_title}"

# Custom examples:
# Include year: "2024_0042_Movie_Title"
target_folder_name = f"{year}_{folder_number:04d}_{folder_title}"

# Include genre: "Action_0042_Movie_Title"
target_folder_name = f"{genre}_{folder_number:04d}_{folder_title}"
```

### Integration with Media Servers

```bash
# Jellyfin/Plex structure
TARGET_PATH=/var/lib/jellyfin/movies

# Post-process: Extract to MKV
for iso in *.iso; do
    makemkv --minlength=60 "$iso" output/
done

# Create NFO files
for folder in */; do
    python generate_nfo.py "$folder"
done
```

### Automated Quality Checks

```bash
#!/bin/bash
# quality_check.sh - Verify all archives

for ecc in **/*.ecc; do
    iso="${ecc%.ecc}"

    echo "Checking: $iso"

    # Test parity
    dvdisaster -t "$iso" --ecc "$ecc" || {
        echo "ERROR: Parity test failed for $iso"
        exit 1
    }

    # Verify checksum
    shasum -a 256 -c "${iso}.sha256" || {
        echo "ERROR: Checksum failed for $iso"
        exit 1
    }
done

echo "✓ All quality checks passed"
```

### Performance Monitoring

```bash
# Monitor imaging speed
while true; do
    iso_size=$(stat -f%z ~/DVD_Archive/disc_*/disc_*.iso 2>/dev/null | tail -1)
    echo "$(date): ${iso_size} bytes ($(($iso_size/1024/1024)) MB)"
    sleep 5
done

# Log to file
python dvd_archiver.py 2>&1 | tee imaging_log_$(date +%Y%m%d).txt
```

## Best Practices

### Storage Strategy

**3-2-1 Backup Rule:**
1. **3 copies** of your data
2. **2 different media types** (HDD + Cloud, or HDD + Tape)
3. **1 off-site backup**

**Example Implementation:**
```bash
# Primary: Local SSD
TARGET_PATH=/mnt/ssd/dvd_archive

# Secondary: External HDD (different manufacturer)
rsync -av /mnt/ssd/dvd_archive/ /mnt/external_hdd/dvd_backup/

# Tertiary: Cloud
rclone sync /mnt/ssd/dvd_archive remote:backup/dvds
```

### Verification Schedule

```bash
# Annual verification script
#!/bin/bash
# verify_annual.sh

LOG="verification_$(date +%Y).log"

{
    echo "=== Annual Archive Verification: $(date) ==="

    # Checksum verification
    find . -name "*.sha256" -exec shasum -c {} \;

    # Parity testing
    find . -name "*.ecc" | while read ecc; do
        iso="${ecc%.ecc}"
        dvdisaster -t "$iso" --ecc "$ecc"
    done

    echo "=== Verification Complete ==="
} | tee "$LOG"
```

### Media Refresh Cycle

```bash
# Every 5-10 years, copy to new drives
#!/bin/bash
# refresh_media.sh

OLD_PATH="/mnt/old_archive"
NEW_PATH="/mnt/new_archive"

rsync -av --progress "$OLD_PATH/" "$NEW_PATH/"

# Verify all checksums on new media
cd "$NEW_PATH"
find . -name "*.sha256" -exec shasum -c {} \; > verification.log
```

### Handling Problematic Discs

```bash
# For discs with read errors
# 1. Clean disc thoroughly
# 2. Try different drive
# 3. Use ddrescue with more retries

# Modify ddrescue command in dvd_archiver.py:
# Original: -r3 (3 retries)
# Damaged: -r10 (10 retries)

# Or manual recovery:
ddrescue -n -b 2048 -c 16384 /dev/disk4 damaged_disc.iso damaged.log
ddrescue -d -r10 -b 2048 -c 16384 /dev/disk4 damaged_disc.iso damaged.log

# Check error map
cat damaged.log
```

### Metadata Management

```bash
# Export archive metadata to CSV
#!/usr/bin/env python3
import json
import csv

with open('archive_log.json') as f:
    data = json.load(f)

with open('archive_catalog.csv', 'w', newline='') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(['Disc Number', 'ISO Path', 'Checksum', 'Size', 'Date'])

    for num, info in data.items():
        writer.writerow([
            num,
            info['iso_path'],
            info['checksum'],
            info.get('capacity_bytes', 'N/A'),
            info['timestamp']
        ])
```

## Tips & Tricks

### Speed Optimization

```bash
# Use RAM disk for temporary operations (macOS)
diskutil erasevolume HFS+ "RAMDisk" `hdiutil attach -nomount ram://8388608`
# 4GB RAM disk at /Volumes/RAMDisk

# Configure for RAM disk
DVD_ARCHIVE_BASE=/Volumes/RAMDisk/temp

# Copy to permanent storage after imaging
rsync -av /Volumes/RAMDisk/temp/ /Volumes/MainArchive/
```

### Parallel Processing (Copy Mode)

```bash
# Process multiple folders in parallel (use with caution)
#!/bin/bash

# Get list of folders to process
folders=($(find $SOURCE_PATHS -type d -name "[0-9]*" | sort))

# Process 3 at a time
for folder in "${folders[@]}"; do
    (
        # Create isolated state file
        STATE="copy_state_${folder##*/}.json"
        python dvd_archiver.py -c --state-file "$STATE"
    ) &

    # Limit to 3 parallel jobs
    if [[ $(jobs -r -p | wc -l) -ge 3 ]]; then
        wait -n
    fi
done

wait
echo "All parallel processing complete"
```

### Network Performance

```bash
# For network destinations, use local staging
LOCAL_STAGING="/tmp/dvd_staging"
NETWORK_DEST="//NAS/Archive"

# Process to local first
TARGET_PATH="$LOCAL_STAGING" python dvd_archiver.py -c

# Then transfer in batch
rsync -av --progress "$LOCAL_STAGING/" "$NETWORK_DEST/"
```

This guide covers the most common usage scenarios. For additional help, see the main README.md or open an issue on GitHub.
