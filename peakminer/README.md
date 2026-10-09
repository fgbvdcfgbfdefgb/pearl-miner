# PeakMiner v2.18.1 for Pearl (PRL) Offline Mining

This directory contains PeakMiner v2.18.1 binaries, release packages, and launcher scripts pre-configured for offline Pearl (PRL) mining through the local `miner.py` Stratum bridge.

## Quick Start

### 1. Ensure `miner.py` is running
On the offline mining machine:
```bash
python3 miner.py --stratum-only
```
*(Listens on `127.0.0.1:3333` and serves live Pearl jobs from `jobs.txt`)*.

### 2. Launch PeakMiner

#### Linux:
```bash
./start_peak_miner.sh
```
Or directly:
```bash
./peakminer/peakminer -o stratum+tcp://127.0.0.1:3333 -u krxYRPV4WQ.peak-gpu -p x -c pearl
```

#### Windows:
Double-click `start_peak_miner.bat` or run:
```cmd
peakminer\peakminer.exe -o stratum+tcp://127.0.0.1:3333 -u krxYRPV4WQ.peak-win-gpu -p x -c pearl
```

## Directory Structure
- `peakminer`: Extracted Linux x86_64 binary.
- `peakminer.exe`: Extracted Windows x86_64 binary.
- `start_peak_miner.sh`: Linux launcher script.
- `start_peak_miner.bat`: Windows launcher script.
- `v2.18.1/`: Upstream official release archives (`.tar.gz` and `.zip`) and SHA-256 verification sums.
