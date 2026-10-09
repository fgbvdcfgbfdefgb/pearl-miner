#!/usr/bin/env bash
# ==============================================================================
# start_forge_miner.sh — Launch ForgeMiner locally connected to miner.py
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORGE_BIN=""

# Locate the forge binary
if [ -x "$SCRIPT_DIR/forge" ]; then
    FORGE_BIN="$SCRIPT_DIR/forge"
elif [ -x "$SCRIPT_DIR/forgeminer/forge" ]; then
    FORGE_BIN="$SCRIPT_DIR/forgeminer/forge"
elif [ -f "$SCRIPT_DIR/forgeminer/v1.8.5/ForgeMiner-1.8.5-linux.tar.gz" ]; then
    echo "[*] Extracting forge binary from ForgeMiner-1.8.5-linux.tar.gz..."
    tar -xzf "$SCRIPT_DIR/forgeminer/v1.8.5/ForgeMiner-1.8.5-linux.tar.gz" -C "$SCRIPT_DIR/forgeminer" forge
    chmod +x "$SCRIPT_DIR/forgeminer/forge"
    FORGE_BIN="$SCRIPT_DIR/forgeminer/forge"
fi

if [ -z "$FORGE_BIN" ] || [ ! -f "$FORGE_BIN" ]; then
    echo "[!] Error: could not find forge binary. Please ensure forgeminer/forge exists."
    exit 1
fi

chmod +x "$FORGE_BIN"

WALLET="${FORGE_WALLET:-krxYRPV4WQ}"
WORKER="${FORGE_WORKER:-forge-gpu}"
POOL="${FORGE_POOL:-127.0.0.1:3333}"

echo "===================================================="
echo "          FORGEMINER - PEARL (PRL) OFFLINE          "
echo "===================================================="
echo "Binary:  $FORGE_BIN"
echo "Pool:    $POOL (Local miner.py Stratum Bridge)"
echo "Wallet:  $WALLET"
echo "Worker:  $WORKER"
echo "===================================================="
echo ""

exec "$FORGE_BIN" --algorithm pearlhash --pool "$POOL" --wallet "$WALLET" --worker "$WORKER" --tls false "$@"
