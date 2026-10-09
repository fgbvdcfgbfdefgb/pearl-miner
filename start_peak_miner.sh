#!/usr/bin/env bash
# ==============================================================================
# start_peak_miner.sh — Launch PeakMiner locally connected to miner.py
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PEAK_BIN=""

# Locate the peakminer binary
if [ -x "$SCRIPT_DIR/peakminer" ]; then
    PEAK_BIN="$SCRIPT_DIR/peakminer"
elif [ -x "$SCRIPT_DIR/peakminer/peakminer" ]; then
    PEAK_BIN="$SCRIPT_DIR/peakminer/peakminer"
elif [ -f "$SCRIPT_DIR/peakminer/v2.18.1/peakminer-2.18.1.tar.gz" ]; then
    echo "[*] Extracting peakminer from peakminer-2.18.1.tar.gz..."
    tar -xzf "$SCRIPT_DIR/peakminer/v2.18.1/peakminer-2.18.1.tar.gz" -C "$SCRIPT_DIR/peakminer" peakminer
    chmod +x "$SCRIPT_DIR/peakminer/peakminer"
    PEAK_BIN="$SCRIPT_DIR/peakminer/peakminer"
fi

if [ -z "$PEAK_BIN" ] || [ ! -f "$PEAK_BIN" ]; then
    echo "[!] Error: could not find peakminer binary. Please ensure peakminer/peakminer exists."
    exit 1
fi

chmod +x "$PEAK_BIN"

WALLET="${PEAK_WALLET:-krxYRPV4WQ}"
WORKER="${PEAK_WORKER:-peak-gpu}"
POOL="${PEAK_POOL:-stratum+tcp://127.0.0.1:3333}"
PASS="${PEAK_PASSWORD:-x}"

echo "===================================================="
echo "          PEAKMINER - PEARL (PRL) OFFLINE           "
echo "===================================================="
echo "Binary:  $PEAK_BIN"
echo "Pool:    $POOL (Local miner.py Stratum Bridge)"
echo "Wallet:  $WALLET.$WORKER"
echo "Coin:    pearl (pearlhash)"
echo "===================================================="
echo ""

exec "$PEAK_BIN" -o "$POOL" -u "$WALLET.$WORKER" -p "$PASS" -c pearl "$@"
