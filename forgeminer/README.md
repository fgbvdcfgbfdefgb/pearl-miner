# ForgeMiner for Pearl (PRL) Offline Mining

This directory contains ForgeMiner v1.8.5 binaries, release archives, and launcher scripts configured to mine Pearl (PRL) through the local `miner.py` offline Stratum bridge.

## Quick Start

### 1. Ensure `miner.py` is running
On the offline mining machine, launch the local Stratum bridge:
```bash
python3 miner.py
```
*(By default, this listens on `127.0.0.1:3333` and serves jobs from `jobs.txt` while recording shares to `shares.txt`)*.

### 2. Launch ForgeMiner

#### Linux:
```bash
./start_forge_miner.sh
```
Or directly:
```bash
./forge --algorithm pearlhash --pool 127.0.0.1:3333 --wallet krxYRPV4WQ --worker forge-gpu --tls false
```

#### Windows:
Double-click `start_forge_miner.bat` or run:
```cmd
forge.exe --algorithm pearlhash --pool 127.0.0.1:3333 --wallet krxYRPV4WQ --worker forge-win-gpu --tls false
```

## Directory Structure
- `forge`: Extracted Linux x86_64 executable binary.
- `forge.exe`: Extracted Windows x86_64 executable binary.
- `start_forge_miner.sh`: Shell launcher script with auto-extraction fallback for Linux.
- `start_forge_miner.bat`: Batch launcher script for Windows.
- `v1.8.5/`: Upstream release archives (`.tar.gz` and `.zip`) and checksum verification files.
