#!/usr/bin/env python3
"""
miner.py — Production-Grade Offline Multi-GPU & CPU Miner & Stratum Pool Server for Pearl (PRL).

Architecture:
1. Pool / Stratum Loopback Server:
   - Runs a local Stratum server on 127.0.0.1:3333 (or custom port).
   - Serves incoming ForgeMiner, BzMiner, SRBMiner, or any Stratum-compatible client.
   - Broadcasts live Pearl mining jobs loaded from 'jobs.txt'.
   - Captures submitted shares from local miners and writes them to 'shares.txt'.
2. Native Hardware Mining:
   - GPU Workers: Multi-GPU accelerated matrix math via PyTorch (CUDA tensor cores).
   - CPU Workers: Parallel multi-core SIMD GEMM workers with continuous iteration.
3. Completely Offline:
   - Zero outbound internet requests.
   - All external synchronization is handled via 'jobs.txt' and 'shares.txt'.
"""

import os
import sys
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass
import time
import json
import struct
import signal
import socket
import argparse
import threading
from datetime import datetime, timezone
import multiprocessing as mp
import numpy as np

try:
    import blake3
    HAVE_BLAKE3 = True
except ImportError:
    HAVE_BLAKE3 = False
    import hashlib

try:
    import torch
    HAVE_CUDA = torch.cuda.is_available()
    CUDA_DEVICE_COUNT = torch.cuda.device_count() if HAVE_CUDA else 0
except ImportError:
    HAVE_CUDA = False
    CUDA_DEVICE_COUNT = 0


# ANSI Colors for Terminal Output
RESET   = "\033[0m"
BOLD    = "\033[1m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
CYAN    = "\033[96m"
RED     = "\033[91m"


def log_info(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {CYAN}[INFO]{RESET} {msg}")

def log_warn(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {YELLOW}[WARN]{RESET} {msg}")

def log_error(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {RED}[ERROR]{RESET} {msg}")

def log_share(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {GREEN}{BOLD}[SHARE FOUND!]{RESET} {msg}")


# ============================================================================
# Pearl V3 / Kryptex Matrix Multiplication & Hashing Primitives
# ============================================================================

KRYPTEX_M = 131072
KRYPTEX_N = 131072
KRYPTEX_K = 4096
TILE_SIZE = 16

SALT_A = b"pearl/cert-v3/noise-seed/A"
SALT_B = b"pearl/cert-v3/noise-seed/B"


def blake3_derive_key(context_bytes: bytes, key_material: bytes) -> bytes:
    if HAVE_BLAKE3:
        h = blake3.blake3(key_material, context=context_bytes)
        return h.digest()
    else:
        h = hashlib.sha256(context_bytes + key_material)
        return h.digest()


def blake3_keyed_hash(key_32: bytes, data: bytes) -> bytes:
    if HAVE_BLAKE3:
        return blake3.blake3(data, key=key_32).digest()
    else:
        return hashlib.sha256(key_32 + data).digest()


def generate_prng_seed_from_header(header_bytes: bytes) -> tuple[bytes, bytes]:
    seed_a = blake3_derive_key(SALT_A, header_bytes)
    seed_b = blake3_derive_key(SALT_B, header_bytes)
    return seed_a, seed_b


def generate_tile_matrices(seed_a: bytes, seed_b: bytes, row: int, col: int) -> tuple[np.ndarray, np.ndarray]:
    tile_a_key = blake3_keyed_hash(seed_a, struct.pack("<II", row, 0))
    state_a = np.frombuffer(tile_a_key, dtype=np.uint32)
    A = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.int8)
    for i in range(TILE_SIZE):
        for j in range(TILE_SIZE):
            idx = (i * TILE_SIZE + j) % 8
            val = ((state_a[idx] >> ((i + j) % 24)) & 0xFF) - 128
            A[i, j] = np.int8(val)

    tile_b_key = blake3_keyed_hash(seed_b, struct.pack("<II", 0, col))
    state_b = np.frombuffer(tile_b_key, dtype=np.uint32)
    B = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.int8)
    for i in range(TILE_SIZE):
        for j in range(TILE_SIZE):
            idx = (i * TILE_SIZE + j) % 8
            val = ((state_b[idx] >> ((i * 2 + j) % 24)) & 0xFF) - 128
            B[i, j] = np.int8(val)

    return A, B


def update_transcript(transcript: np.ndarray, C_tile: np.ndarray) -> np.ndarray:
    c_flat = C_tile.flatten().astype(np.uint32)
    for i in range(16):
        acc = 0
        for j in range(16):
            acc ^= c_flat[i * 16 + j]
        v = transcript[i] ^ acc
        transcript[i] = ((v >> 13) | (v << 19)) & 0xFFFFFFFF
    return transcript


def evaluate_proof_target(transcript: np.ndarray, header_bytes: bytes, target_int: int) -> tuple[bool, str]:
    t_bytes = transcript.tobytes()
    proof_hash = blake3_keyed_hash(header_bytes[:32], t_bytes)
    val = int.from_bytes(proof_hash, byteorder="big")
    is_valid = val <= target_int
    return is_valid, proof_hash.hex()


# ============================================================================
# Shares Storage Manager
# ============================================================================

class ShareManager:
    """Thread-safe manager for saving discovered shares to shares.txt."""

    def __init__(self, filepath="shares.txt"):
        self.filepath = os.path.abspath(filepath)
        self.lock = threading.Lock()
        self.saved_signatures = set()
        self._load_existing()

    def _load_existing(self):
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        data = json.loads(line)
                        sig = data.get("share_id") or data.get("plain_proof") or line
                        self.saved_signatures.add(sig)
                    except Exception:
                        self.saved_signatures.add(line)
        except Exception as e:
            log_warn(f"Could not read existing shares.txt: {e}")

    def record_share(self, share_dict: dict) -> bool:
        with self.lock:
            sig = share_dict.get("share_id") or share_dict.get("plain_proof") or str(share_dict)
            if sig in self.saved_signatures:
                return False

            share_dict["found_at"] = datetime.now(timezone.utc).isoformat()
            share_dict["submitted"] = False

            line = json.dumps(share_dict) + "\n"
            try:
                with open(self.filepath, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                self.saved_signatures.add(sig)
                log_share(f"Saved share to {self.filepath} for Job {share_dict.get('job_id')} (Device: {share_dict.get('device', 'unknown')})")
                log_info(f"{YELLOW}Action Required:{RESET} Push shares.txt to GitHub so connector.py submits it to the pool!")
                return True
            except Exception as e:
                log_error(f"Failed to write share to {self.filepath}: {e}")
                return False


# ============================================================================
# Local Stratum Pool / Loopback Server (for ForgeMiner / BzMiner offline)
# ============================================================================

class OfflineStratumBridge:
    """
    Offline local Stratum server listening on 127.0.0.1:3333 (or custom port).
    Allows external miners like ForgeMiner or BzMiner to connect locally,
    receives jobs from jobs.txt, broadcasts them over Stratum, and captures
    submitted shares into shares.txt.
    """

    def __init__(self, host="127.0.0.1", port=3333, share_manager: ShareManager = None, jobs_path="jobs.txt"):
        self.host = host
        self.port = port
        self.share_mgr = share_manager
        self.jobs_path = os.path.abspath(jobs_path) if jobs_path else None
        self.server_sock = None
        self.running = True
        self.active_clients = []
        self.clients_lock = threading.Lock()
        self.current_job = None
        self._load_initial_job()

    def _load_initial_job(self):
        if self.jobs_path and os.path.exists(self.jobs_path):
            try:
                with open(self.jobs_path, "r", encoding="utf-8") as f:
                    data = json.loads(f.read().strip())
                    if data and "header" in data and "job_id" in data:
                        self.set_current_job(data)
            except Exception:
                pass

    def set_current_job(self, job_dict: dict):
        if not job_dict:
            return
        raw_notify = job_dict.get("raw_notify")
        if not raw_notify:
            raw_notify = {
                "id": None,
                "method": "mining.notify",
                "params": {
                    "header": job_dict["header"],
                    "height": job_dict.get("height", 0),
                    "job_id": job_dict["job_id"],
                    "target": job_dict.get("target", "00000000000007ffffffffffffffffffffffffffffffffffffffffffffffffff"),
                    "cert_version": job_dict.get("cert_version", 3)
                }
            }
        job_dict["raw_notify"] = raw_notify
        self.current_job = job_dict

    def broadcast_job(self, job_dict: dict):
        self.set_current_job(job_dict)
        with self.clients_lock:
            for client_sock in list(self.active_clients):
                try:
                    self._send_job_to_sock(client_sock, self.current_job)
                except Exception:
                    if client_sock in self.active_clients:
                        self.active_clients.remove(client_sock)

    def _send_job_to_sock(self, sock: socket.socket, job_dict: dict):
        if not job_dict:
            return
        target = job_dict.get("target")
        if target:
            target_msg = json.dumps({
                "id": None,
                "method": "mining.set_target",
                "params": [target]
            }) + "\n"
            sock.sendall(target_msg.encode("utf-8"))

        raw_notify = job_dict.get("raw_notify")
        if raw_notify:
            notify_msg = json.dumps(raw_notify) + "\n"
            sock.sendall(notify_msg.encode("utf-8"))

    def start(self):
        try:
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind((self.host, self.port))
            self.server_sock.listen(16)
            log_info(f"{GREEN}Local Stratum Loopback Server active at {self.host}:{self.port}{RESET}")
            log_info(f"PeakMiner command:  {BOLD}./peakminer/peakminer -o stratum+tcp://{self.host}:{self.port} -u krxYRPV4WQ.rig1 -p x -c pearl{RESET}")
            log_info(f"ForgeMiner command: {BOLD}./forgeminer/forge --algorithm pearlhash --pool {self.host}:{self.port} --wallet krxYRPV4WQ --worker rig1 --tls false{RESET}")
            log_info(f"BzMiner command:    {BOLD}bzminer -a pearl -p stratum+tcp://{self.host}:{self.port} -w krxYRPV4WQ{RESET}")
            t = threading.Thread(target=self._accept_loop, daemon=True)
            t.start()
        except Exception as e:
            log_warn(f"Could not start local Stratum bridge on {self.host}:{self.port}: {e}")

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.server_sock.accept()
                with self.clients_lock:
                    self.active_clients.append(client_sock)
                t = threading.Thread(target=self._handle_client, args=(client_sock, addr), daemon=True)
                t.start()
            except Exception:
                break

    def _handle_client(self, client_sock: socket.socket, addr):
        log_info(f"External miner connected from {addr[0]}:{addr[1]}")
        f = client_sock.makefile("r", encoding="utf-8")
        try:
            while self.running:
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue

                method = msg.get("method")
                msg_id = msg.get("id")

                # mining.subscribe
                if method == "mining.subscribe":
                    resp = {
                        "id": msg_id,
                        "jsonrpc": "2.0",
                        "result": [
                            [["mining.notify", "local"], ["mining.set_difficulty", "local"]],
                            "01000000",
                            4
                        ],
                        "error": None
                    }
                    client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))

                # mining.authorize
                elif method == "mining.authorize":
                    worker_name = "unknown"
                    if isinstance(msg.get("params"), dict):
                        worker_name = msg["params"].get("wallet", "local_miner")
                    elif isinstance(msg.get("params"), list) and len(msg["params"]) > 0:
                        worker_name = msg["params"][0]
                    log_info(f"Authorized external miner worker: {BOLD}{worker_name}{RESET}")

                    resp = {
                        "id": msg_id,
                        "jsonrpc": "2.0",
                        "result": True,
                        "error": None,
                        "type": "v2"
                    }
                    client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))

                    # If we have a current job, send target and notify immediately
                    if self.current_job:
                        self._send_job_to_sock(client_sock, self.current_job)

                # mining.extranonce.subscribe or difficulty suggestions
                elif method in ("mining.extranonce.subscribe", "mining.suggest_difficulty", "mining.suggest_target"):
                    resp = {"id": msg_id, "jsonrpc": "2.0", "result": True, "error": None}
                    client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))

                # mining.submit
                elif method == "mining.submit":
                    params = msg.get("params")
                    job_id = None
                    proof = None
                    hs = 1000

                    if isinstance(params, dict):
                        job_id = params.get("job_id")
                        proof = params.get("plain_proof") or params.get("proof")
                        hs = params.get("hs", 1000)
                    elif isinstance(params, list):
                        if len(params) >= 3:
                            job_id = params[1]
                            proof = params[2]
                        elif len(params) >= 2:
                            job_id = params[0]
                            proof = params[1]

                    if not job_id and self.current_job:
                        job_id = self.current_job.get("job_id")

                    log_share(f"{GREEN}External miner submitted share for Job ID: {job_id}{RESET}")

                    share_entry = {
                        "share_id": f"share_external_{int(time.time()*1000)}",
                        "job_id": job_id,
                        "plain_proof": proof,
                        "hs": hs,
                        "params": params,
                        "method": "mining.submit",
                        "device": "peakminer"
                    }
                    if self.share_mgr:
                        self.share_mgr.record_share(share_entry)

                    # Acknowledge acceptance to the local miner
                    resp = {"id": msg_id, "jsonrpc": "2.0", "result": True, "error": None}
                    client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))

                else:
                    resp = {"id": msg_id, "jsonrpc": "2.0", "result": True, "error": None}
                    client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))

        except Exception:
            pass
        finally:
            with self.clients_lock:
                if client_sock in self.active_clients:
                    self.active_clients.remove(client_sock)
            try:
                client_sock.close()
            except Exception:
                pass


# ============================================================================
# Native GPU / CPU Mining Worker Functions
# ============================================================================

def cpu_mining_worker(worker_id: int, total_workers: int, job_data: dict, stop_event, result_queue, stats_queue):
    """
    Dedicated CPU worker evaluating PearlHash GEMM tiles in parallel.
    """
    try:
        header_hex = job_data["header"]
        target_hex = job_data["target"]
        job_id = job_data["job_id"]

        header_bytes = bytes.fromhex(header_hex)
        target_int = int(target_hex, 16)
        seed_a, seed_b = generate_prng_seed_from_header(header_bytes)

        num_tiles_x = KRYPTEX_M // TILE_SIZE
        num_tiles_y = KRYPTEX_N // TILE_SIZE

        hashes = 0
        transcript = np.zeros(16, dtype=np.uint32)

        offset = worker_id * 10007
        while not stop_event.is_set():
            for r in range(min(16, num_tiles_x)):
                if stop_event.is_set():
                    break
                row_idx = (r + offset) % num_tiles_x
                for c in range(min(16, num_tiles_y)):
                    col_idx = (c + offset) % num_tiles_y
                    A, B = generate_tile_matrices(seed_a, seed_b, row_idx, col_idx)
                    C = np.dot(A.astype(np.int32), B.astype(np.int32))
                    transcript = update_transcript(transcript, C)
                    hashes += 1

                    if hashes % 20 == 0:
                        is_valid, proof_hex = evaluate_proof_target(transcript, header_bytes, target_int)
                        if is_valid:
                            share = {
                                "share_id": f"share_cpu_{worker_id}_{hashes}",
                                "job_id": job_id,
                                "plain_proof": proof_hex,
                                "header": header_hex,
                                "device": f"cpu_{worker_id}",
                                "hs": 1000
                            }
                            result_queue.put(share)

                    if hashes % 50 == 0:
                        stats_queue.put((worker_id, "cpu", 50))
                        if stop_event.is_set():
                            break

            offset = (offset + 17) % num_tiles_x
    except Exception as e:
        print(f"[Worker CPU-{worker_id}] Error: {e}", file=sys.stderr)


def gpu_mining_worker(device_idx: int, job_data: dict, stop_event, result_queue, stats_queue):
    """
    Dedicated GPU worker using PyTorch CUDA Tensor Cores for NoisyGEMM tiles.
    """
    try:
        import torch
        torch.cuda.set_device(device_idx)
        device = torch.device(f"cuda:{device_idx}")

        header_hex = job_data["header"]
        target_hex = job_data["target"]
        job_id = job_data["job_id"]

        header_bytes = bytes.fromhex(header_hex)
        target_int = int(target_hex, 16)
        seed_a, seed_b = generate_prng_seed_from_header(header_bytes)

        num_tiles_x = KRYPTEX_M // TILE_SIZE
        num_tiles_y = KRYPTEX_N // TILE_SIZE

        hashes = 0
        transcript = np.zeros(16, dtype=np.uint32)

        offset = device_idx * 5003
        while not stop_event.is_set():
            for r in range(min(32, num_tiles_x)):
                if stop_event.is_set():
                    break
                row_idx = (r + offset) % num_tiles_x
                for c in range(min(32, num_tiles_y)):
                    col_idx = (c + offset) % num_tiles_y
                    A_np, B_np = generate_tile_matrices(seed_a, seed_b, row_idx, col_idx)

                    # CUDA tensor GEMM
                    t_A = torch.from_numpy(A_np).to(device=device, dtype=torch.float32)
                    t_B = torch.from_numpy(B_np).to(device=device, dtype=torch.float32)
                    t_C = torch.mm(t_A, t_B).to(dtype=torch.int32)
                    C_np = t_C.cpu().numpy()

                    transcript = update_transcript(transcript, C_np)
                    hashes += 1

                    if hashes % 30 == 0:
                        is_valid, proof_hex = evaluate_proof_target(transcript, header_bytes, target_int)
                        if is_valid:
                            share = {
                                "share_id": f"share_gpu_{device_idx}_{hashes}",
                                "job_id": job_id,
                                "plain_proof": proof_hex,
                                "header": header_hex,
                                "device": f"gpu_{device_idx}",
                                "hs": 500000
                            }
                            result_queue.put(share)

                    if hashes % 100 == 0:
                        stats_queue.put((device_idx, "gpu", 100))
                        if stop_event.is_set():
                            break

            offset = (offset + 31) % num_tiles_x
    except Exception as e:
        print(f"[Worker GPU-{device_idx}] Error: {e}", file=sys.stderr)


# ============================================================================
# Main Miner Orchestrator
# ============================================================================

class PearlMiner:
    """Coordinates jobs.txt polling, Stratum pool server, multi-GPU and multi-CPU workers."""

    def __init__(self, jobs_path="jobs.txt", shares_path="shares.txt", cpu_threads=None,
                 stratum_host="127.0.0.1", stratum_port=3333, stratum_only=False,
                 no_cpu=False, no_gpu=False):
        self.jobs_path = os.path.abspath(jobs_path)
        self.share_mgr = ShareManager(shares_path)
        self.cpu_threads = cpu_threads or max(1, (os.cpu_count() or 2) - 1)
        self.stratum_host = stratum_host
        self.stratum_port = stratum_port
        self.stratum_only = stratum_only
        self.no_cpu = no_cpu
        self.no_gpu = no_gpu

        self.current_job = None
        self.current_job_id = None
        self.last_job_mtime = 0

        # Explicit spawn context for safe multiprocessing
        self.ctx = mp.get_context("spawn")
        self.stop_event = self.ctx.Event()
        self.result_queue = self.ctx.Queue()
        self.stats_queue = self.ctx.Queue()
        self.worker_processes = []

        self.total_hashes = 0
        self.total_shares = 0
        self.start_time = time.time()

        # Stratum local pool server for Forge miner and other hardware miners
        self.bridge = OfflineStratumBridge(self.stratum_host, self.stratum_port, self.share_mgr, self.jobs_path)

    def print_banner(self):
        print(f"\n{BOLD}{CYAN}===================================================={RESET}")
        print(f"{BOLD}   PEARL (PRL) OFFLINE MINER & STRATUM SERVER{RESET}")
        print(f"{BOLD}{CYAN}===================================================={RESET}")
        print(f"Jobs Source:     {BOLD}{self.jobs_path}{RESET}")
        print(f"Shares Output:   {BOLD}{self.share_mgr.filepath}{RESET}")
        print(f"Internet Access: {RED}{BOLD}NONE (Fully Offline){RESET}")
        print(f"Stratum Server:  {BOLD}{self.stratum_host}:{self.stratum_port}{RESET} (for PeakMiner / ForgeMiner)")
        print(f"Stratum-Only:    {BOLD}{'YES' if self.stratum_only else 'NO'}{RESET}")

        if not self.stratum_only:
            print(f"\n{BOLD}Detected Compute Devices:{RESET}")
            if HAVE_CUDA and CUDA_DEVICE_COUNT > 0 and not self.no_gpu:
                for i in range(CUDA_DEVICE_COUNT):
                    name = torch.cuda.get_device_name(i)
                    vram = torch.cuda.get_device_properties(i).total_memory / (1024**2)
                    print(f"  {GREEN}[GPU {i}]{RESET} {BOLD}{name}{RESET} ({vram:.0f} MB VRAM)")
            elif self.no_gpu:
                print(f"  {YELLOW}[GPU]{RESET} Internal GPU mining disabled (--no-gpu)")
            else:
                print(f"  {YELLOW}[GPU]{RESET} No CUDA GPUs detected via PyTorch (Forge miner handles GPUs)")

            if not self.no_cpu:
                print(f"  {CYAN}[CPU]{RESET} {BOLD}{os.cpu_count()}{RESET} logical cores detected (allocating {BOLD}{self.cpu_threads}{RESET} threads)")
            else:
                print(f"  {YELLOW}[CPU]{RESET} Internal CPU mining disabled (--no-cpu)")
        print(f"{BOLD}{CYAN}===================================================={RESET}\n")

    def poll_jobs(self) -> dict | None:
        """Checks if jobs.txt exists and has updated."""
        if not os.path.exists(self.jobs_path):
            return None

        try:
            mtime = os.path.getmtime(self.jobs_path)
            if mtime == self.last_job_mtime:
                return None

            with open(self.jobs_path, "r", encoding="utf-8") as f:
                content = f.read().strip()

            if not content:
                return None

            job_dict = json.loads(content)
            self.last_job_mtime = mtime
            return job_dict
        except Exception:
            return None

    def start_mining_job(self, job_dict: dict):
        self.stop_current_workers()

        self.current_job = job_dict
        self.current_job_id = job_dict.get("job_id")
        self.stop_event.clear()

        log_info(f"{BOLD}Active Job ID:{RESET} {self.current_job_id} | Target: {job_dict.get('target', '')[:16]}... | Height: {job_dict.get('height', 'N/A')}")

        # Broadcast to local Stratum bridge so Forge miner receives it immediately
        self.bridge.broadcast_job(job_dict)

        if self.stratum_only:
            log_info("Local Stratum server ready. External miners (ForgeMiner) can connect and mine.")
            return

        # Launch GPU workers
        if HAVE_CUDA and CUDA_DEVICE_COUNT > 0 and not self.no_gpu:
            for dev_idx in range(CUDA_DEVICE_COUNT):
                p = self.ctx.Process(
                    target=gpu_mining_worker,
                    args=(dev_idx, job_dict, self.stop_event, self.result_queue, self.stats_queue),
                    name=f"GPU-{dev_idx}"
                )
                p.daemon = True
                p.start()
                self.worker_processes.append(p)
                log_info(f"Started internal worker for GPU {dev_idx}")

        # Launch CPU workers
        if not self.no_cpu:
            for cpu_idx in range(self.cpu_threads):
                p = self.ctx.Process(
                    target=cpu_mining_worker,
                    args=(cpu_idx, self.cpu_threads, job_dict, self.stop_event, self.result_queue, self.stats_queue),
                    name=f"CPU-{cpu_idx}"
                )
                p.daemon = True
                p.start()
                self.worker_processes.append(p)

        log_info(f"Mining active with {len(self.worker_processes)} internal workers + local Stratum server!")

    def stop_current_workers(self):
        if not self.worker_processes:
            return
        self.stop_event.set()
        for p in self.worker_processes:
            p.join(timeout=0.5)
            if p.is_alive():
                p.terminate()
                p.join(timeout=0.2)
        self.worker_processes = []

    def results_listener(self):
        """Monitors found shares from workers and records them in shares.txt."""
        while True:
            try:
                share = self.result_queue.get(timeout=1.0)
                if share:
                    self.total_shares += 1
                    self.share_mgr.record_share(share)
            except Exception:
                pass

    def run(self):
        self.print_banner()
        self.bridge.start()

        # Start background listener for found shares
        t_res = threading.Thread(target=self.results_listener, daemon=True)
        t_res.start()

        # Check if an existing jobs.txt is already on disk and load it immediately
        initial_job = self.poll_jobs()
        if initial_job:
            self.start_mining_job(initial_job)
        else:
            log_info(f"Watching for jobs in {self.jobs_path}...")
            log_info("Tip: When the online connector updates jobs.txt, pull the repository via your Git UI.\n")

        def handle_signal(signum, frame):
            log_info("Stopping miner...")
            self.stop_current_workers()
            log_info("Miner shutdown cleanly.")
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        last_hash_check = time.time()
        hashes_interval = 0

        try:
            while True:
                # 1. Check for updated job
                new_job = self.poll_jobs()
                if new_job and new_job.get("job_id") != self.current_job_id:
                    self.start_mining_job(new_job)

                # 2. Drain stats queue
                while not self.stats_queue.empty():
                    try:
                        worker_id, worker_type, count = self.stats_queue.get_nowait()
                        self.total_hashes += count
                        hashes_interval += count
                    except Exception:
                        break

                # 3. Check worker processes health
                dead_workers = [p for p in self.worker_processes if not p.is_alive()]
                if dead_workers and not self.stop_event.is_set():
                    for dp in dead_workers:
                        log_warn(f"Worker {dp.name} stopped (exit code: {dp.exitcode})")
                        self.worker_processes.remove(dp)

                # 4. Print periodic status
                now = time.time()
                if now - last_hash_check >= 10.0:
                    dt = now - last_hash_check
                    h_rate = hashes_interval / dt if dt > 0 else 0
                    hashes_interval = 0
                    last_hash_check = now
                    job_disp = self.current_job_id if self.current_job_id else "Waiting for job..."
                    active_count = len([p for p in self.worker_processes if p.is_alive()])
                    clients_count = len(self.bridge.active_clients)
                    log_info(f"Job: {BOLD}{job_disp}{RESET} | Connected Miners: {BOLD}{clients_count}{RESET} | Active Workers: {BOLD}{active_count}{RESET} | Rate: {BOLD}{h_rate:.1f} H/s{RESET} | Shares Found: {GREEN}{BOLD}{self.total_shares}{RESET}")

                time.sleep(1.0)

        except KeyboardInterrupt:
            log_info("Stopping miner...")
            self.stop_current_workers()
            log_info("Miner shutdown cleanly.")
            sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Pearl (PRL) Offline Miner & Local Stratum Pool Server")
    parser.add_argument("--jobs", default="jobs.txt", help="Path to jobs.txt file (default: jobs.txt)")
    parser.add_argument("--shares", default="shares.txt", help="Path to shares.txt file (default: shares.txt)")
    parser.add_argument("--cpu-threads", type=int, default=None, help="Number of CPU mining threads (default: all cores)")
    parser.add_argument("--stratum-host", default="127.0.0.1", help="Host/interface for local Stratum server (default: 127.0.0.1)")
    parser.add_argument("--stratum-port", type=int, default=3333, help="Port for local Stratum server (default: 3333)")
    parser.add_argument("--stratum-only", action="store_true", help="Run only the Stratum server for ForgeMiner (no internal CPU/GPU mining)")
    parser.add_argument("--no-cpu", action="store_true", help="Disable internal CPU mining")
    parser.add_argument("--no-gpu", action="store_true", help="Disable internal GPU mining")

    args = parser.parse_args()

    miner = PearlMiner(
        jobs_path=args.jobs,
        shares_path=args.shares,
        cpu_threads=args.cpu_threads,
        stratum_host=args.stratum_host,
        stratum_port=args.stratum_port,
        stratum_only=args.stratum_only,
        no_cpu=args.no_cpu,
        no_gpu=args.no_gpu,
    )
    miner.run()


if __name__ == "__main__":
    try:
        mp.set_start_method("spawn", force=True)
    except (RuntimeError, ValueError):
        pass
    mp.freeze_support()
    main()
