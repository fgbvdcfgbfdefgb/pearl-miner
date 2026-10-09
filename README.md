# Pearl (PRL) Offline Mining System & Stratum Bridge

A complete offline mining and bridge system designed for **Pearl (PRL)** cryptocurrency mining on Kryptex pool (`prl.kryptex.network:7048`) with wallet `krxYRPV4WQ`.

This setup bridges an **online connector machine** with internet access to an **isolated offline mining rig** (without internet access) using GitHub (`fgbvdcfgbfdefgb/pearl-miner`) as an asynchronous synchronization channel.

---

## Architecture Overview

```text
       +--------------------------------------------------------+
       |               Kryptex Pearl Stratum Pool               |
       |                (prl.kryptex.network:7048)              |
       +---------------------------+----------------------------+
                                   | Stratum TCP
                                   v
+----------------------------------------------------------------------+
|                     ONLINE MACHINE (Internet Access)                 |
|                                                                      |
|  connector.py                                                        |
|    - Connects to Kryptex Pearl Stratum (prl.kryptex.network:7048)    |
|    - Authorizes with wallet 'krxYRPV4WQ' (Kryptex V2 protocol)       |
|    - Uploads latest jobs to GitHub repository at 30-second intervals |
|    - Continuously polls shares.txt (1.0s) & submits shares instantly |
+----------------------------------+-----------------------------------+
                                   | Git Push / Pull (or GitHub API)
                                   v
+----------------------------------------------------------------------+
|               GITHUB REPOSITORY (fgbvdcfgbfdefgb/pearl-miner)        |
|                                                                      |
|  - jobs.txt   : Current active Pearl mining job from Kryptex pool    |
|  - shares.txt : Found shares with cryptographic proofs & statuses    |
|  - forgeminer/: Upstream ForgeMiner v1.8.5 binaries & release files  |
+----------------------------------+-----------------------------------+
                                   | Git UI Manual Pull / Push
                                   v
+----------------------------------------------------------------------+
|                     OFFLINE RIG (NO Internet Access)                 |
|                                                                      |
|  miner.py                                                            |
|    - Reads mining jobs from 'jobs.txt'                               |
|    - Hosts local Stratum pool server on 127.0.0.1:3333               |
|    - Broadcasts live jobs to local hardware miners (ForgeMiner)      |
|    - Captures submitted shares & appends to 'shares.txt'             |
|    - (Optional) Multi-GPU CUDA & Multi-core CPU native worker engine |
|                                                                      |
|  ForgeMiner (forge / forge.exe)                                      |
|    - Connects to 127.0.0.1:3333 (miner.py local Stratum bridge)      |
|    - Mines PearlHash on NVIDIA GPUs offline                          |
|    - Submits valid proof shares to local miner.py                    |
+----------------------------------------------------------------------+
```

---

## 1. ForgeMiner Integration

The repository includes pre-downloaded, verified **ForgeMiner v1.8.5** binaries and upstream release archives:

- **Linux x86_64 binary**: `forgeminer/forge` (chmod +x)
- **Windows x86_64 binary**: `forgeminer/forge.exe`
- **Linux Release Archive**: `forgeminer/v1.8.5/ForgeMiner-1.8.5-linux.tar.gz`
- **Windows Release Archive**: `forgeminer/v1.8.5/ForgeMiner-1.8.5-windows.zip`
- **Checksums**: `forgeminer/v1.8.5/SHA256SUMS`

### Launching ForgeMiner

Once `miner.py` is running on the offline machine (listening on `127.0.0.1:3333`):

#### On Linux:
```bash
./start_forge_miner.sh
```
Or directly:
```bash
./forgeminer/forge --algorithm pearlhash --pool 127.0.0.1:3333 --wallet krxYRPV4WQ --worker forge-gpu --tls false
```

#### On Windows:
Double-click `start_forge_miner.bat` or run in CMD / PowerShell:
```cmd
forgeminer\forge.exe --algorithm pearlhash --pool 127.0.0.1:3333 --wallet krxYRPV4WQ --worker forge-win-gpu --tls false
```

---

## 2. Offline Miner (`miner.py`)

Runs on the machine without internet access. It reads `jobs.txt`, hosts the local Stratum bridge on `127.0.0.1:3333`, and records found shares to `shares.txt`.

### Usage Modes

#### A. Full Mining Mode (Stratum Server + Native GPU & CPU Mining):
```bash
python3 miner.py
```

#### B. Stratum Server Only (Dedicated bridge for ForgeMiner):
If you want ForgeMiner to handle all GPU mining with 0% CPU/GPU overhead from `miner.py`:
```bash
python3 miner.py --stratum-only
```

#### C. Customizing Hardware & Network:
```bash
python3 miner.py --stratum-port 3333 --cpu-threads 4 --no-gpu
```

### CLI Arguments:
- `--jobs`: Path to `jobs.txt` (default: `jobs.txt`).
- `--shares`: Path to `shares.txt` (default: `shares.txt`).
- `--stratum-host`: Local interface to bind Stratum server (default: `127.0.0.1`).
- `--stratum-port`: Local Stratum server port (default: `3333`).
- `--stratum-only`: Run only the local Stratum server without internal workers.
- `--no-cpu`: Disable internal CPU mining workers.
- `--no-gpu`: Disable internal GPU mining workers.

---

## 3. Online Connector (`connector.py`)

Runs on the machine with internet access. It connects to the live Kryptex pool, uploads the latest jobs to GitHub at intervals of 30 seconds, and continuously checks `shares.txt` for real-time submission back to the pool.

### Usage:

#### Synchronizing via GitHub API (Token-based):
```bash
python3 connector.py \
  --github-token "<YOUR_GITHUB_TOKEN>" \
  --github-repo "fgbvdcfgbfdefgb/pearl-miner" \
  --job-interval 30 \
  --share-poll-interval 1.0
```

#### Synchronizing via Local Git Working Tree:
```bash
python3 connector.py \
  --git-dir "." \
  --job-interval 30 \
  --share-poll-interval 1.0
```

### Features:
- **30-Second Job Upload Interval**: Gathers real-time jobs from Kryptex (`mining.notify`) and uploads the latest active job to GitHub every 30 seconds (avoids API rate limits while keeping the offline miner up to date).
- **Continuous Share Submission**: Polls `shares.txt` continuously (every 1.0s), instantly submits any new shares via Kryptex V2 `mining.submit`, and records the pool's verification confirmation.
- **Failover & Reconnection**: Auto-reconnects to `prl.kryptex.network:7048` if the TCP socket drops.

---

## 4. Workflow Step-by-Step

1. **Start Connector on Online Machine**:
   ```bash
   python3 connector.py --github-token <YOUR_GITHUB_TOKEN> --github-repo fgbvdcfgbfdefgb/pearl-miner
   ```
   *The connector authorizes with Kryptex pool and begins uploading the latest jobs to GitHub every 30 seconds.*

2. **Sync Repository on Offline Machine**:
   - Pull latest changes via your Git UI (or git clone/pull).
   - This pulls the latest `jobs.txt` and ForgeMiner files.

3. **Start Offline Miner**:
   ```bash
   python3 miner.py
   ```
   *The miner loads `jobs.txt` and opens the local Stratum server on `127.0.0.1:3333`.*

4. **Start ForgeMiner**:
   ```bash
   ./start_forge_miner.sh
   ```
   *ForgeMiner connects locally to `127.0.0.1:3333`, receives the active job, and begins mining PRL on your GPUs.*

5. **Submit Discovered Shares**:
   - As ForgeMiner (or internal workers) finds valid shares, `miner.py` immediately appends them to `shares.txt`.
   - On the offline machine, push `shares.txt` to GitHub using your Git UI.
   - The online `connector.py` instantly detects the new shares, submits them to Kryptex pool, and records acceptance back to the repository.
