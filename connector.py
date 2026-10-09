#!/usr/bin/env python3
"""
connector.py — Online Stratum Pool & GitHub Bridge for Pearl (PRL) Mining.

Features:
- Connects to Kryptex Pearl Stratum Pool (prl.kryptex.network:7048).
- Authorizes using wallet 'krxYRPV4WQ' (Kryptex V2 protocol).
- Receives live mining jobs in real time and uploads latest jobs to GitHub at intervals of 30 seconds.
- Continuous share submission: continuously monitors 'shares.txt' (sub-second / 1.0s interval),
  submits found shares to Stratum immediately, and pushes verified status back to GitHub.
- Supports GitHub REST API (token-based) and local Git CLI (commit/push/pull).
"""

import os
import sys
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass
import time
import json
import socket
import argparse
import base64
import threading
import urllib.request as url_request
import urllib.error as url_error
import subprocess
from datetime import datetime, timezone


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
    print(f"[{ts}] {CYAN}[INFO]{RESET} {msg}", flush=True)

def log_stratum(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {BLUE}{BOLD}[STRATUM]{RESET} {msg}", flush=True)

def log_github(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {MAGENTA}{BOLD}[GITHUB]{RESET} {msg}", flush=True)

def log_success(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {GREEN}{BOLD}[SUCCESS]{RESET} {msg}", flush=True)

def log_warn(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {YELLOW}[WARN]{RESET} {msg}", flush=True)

def log_error(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {RED}[ERROR]{RESET} {msg}", flush=True)


class GitHubSyncManager:
    """Handles bidirectional sync with GitHub repo via REST API or local Git CLI."""

    def __init__(self, mode="local", token=None, repo=None, branch="main", git_dir="."):
        self.mode = mode
        self.token = token
        self.repo = repo
        self.branch = branch
        self.git_dir = os.path.abspath(git_dir)
        self.jobs_file = os.path.join(self.git_dir, "jobs.txt")
        self.shares_file = os.path.join(self.git_dir, "shares.txt")
        self.sha_cache = {}

    def _api_request(self, endpoint, data=None, method="GET"):
        url = f"https://api.github.com/repos/{self.repo}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "PearlConnector/1.0",
        }
        req_data = json.dumps(data).encode("utf-8") if data is not None else None
        req = url_request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with url_request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except url_error.HTTPError as e:
            if e.code == 404:
                return None
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {e.code}: {body}")

    def push_jobs(self, job_dict: dict) -> bool:
        """Uploads jobs.txt locally and pushes the latest active job to GitHub."""
        content_str = json.dumps(job_dict, indent=2) + "\n"
        # Always write locally first
        try:
            with open(self.jobs_file, "w", encoding="utf-8") as f:
                f.write(content_str)
        except Exception as e:
            log_error(f"Failed to write local jobs.txt: {e}")

        if self.mode == "local":
            log_github(f"Saved local jobs.txt for Job {job_dict.get('job_id')}")
            return True

        if self.mode == "api":
            try:
                # Fetch fresh SHA to avoid 409 conflict
                existing = self._api_request(f"contents/jobs.txt?ref={self.branch}")
                if existing and "sha" in existing:
                    self.sha_cache["jobs.txt"] = existing["sha"]

                encoded_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
                payload = {
                    "message": f"Update mining job: {job_dict.get('job_id')}",
                    "content": encoded_content,
                    "branch": self.branch,
                }
                if "jobs.txt" in self.sha_cache and self.sha_cache["jobs.txt"]:
                    payload["sha"] = self.sha_cache["jobs.txt"]

                res = self._api_request("contents/jobs.txt", data=payload, method="PUT")
                if res and "content" in res and "sha" in res["content"]:
                    self.sha_cache["jobs.txt"] = res["content"]["sha"]
                log_github(f"Pushed latest jobs.txt to GitHub repo {self.repo} (branch: {self.branch})")
                return True
            except Exception as e:
                log_error(f"GitHub API push jobs.txt failed: {e}")
                self.sha_cache.pop("jobs.txt", None)
                return False

        elif self.mode == "git":
            try:
                subprocess.run(["git", "add", "jobs.txt"], cwd=self.git_dir, check=True, capture_output=True)
                msg = f"Update mining job: {job_dict.get('job_id')}"
                subprocess.run(["git", "commit", "-m", msg], cwd=self.git_dir, check=True, capture_output=True)
                subprocess.run(["git", "push", "origin", self.branch], cwd=self.git_dir, check=True, capture_output=True)
                log_github(f"Git pushed jobs.txt to origin/{self.branch}")
                return True
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
                log_error(f"Git push jobs.txt failed: {stderr}")
                return False

        return False

    def pull_shares(self) -> list:
        """Pulls shares.txt from GitHub / local file and returns list of share entries."""
        content_str = None
        if self.mode == "api":
            try:
                res = self._api_request(f"contents/shares.txt?ref={self.branch}")
                if res and "content" in res:
                    self.sha_cache["shares.txt"] = res.get("sha")
                    content_str = base64.b64decode(res["content"]).decode("utf-8")
                    # Update local file as well
                    with open(self.shares_file, "w", encoding="utf-8") as f:
                        f.write(content_str)
            except Exception:
                pass

        elif self.mode == "git":
            try:
                subprocess.run(["git", "pull", "--rebase", "origin", self.branch], cwd=self.git_dir, capture_output=True, check=False)
            except Exception as e:
                log_error(f"Git pull shares.txt error: {e}")

        # Read local file (either just pulled or local mode)
        if content_str is None and os.path.exists(self.shares_file):
            try:
                with open(self.shares_file, "r", encoding="utf-8") as f:
                    content_str = f.read()
            except Exception as e:
                log_error(f"Failed to read local shares.txt: {e}")

        if not content_str:
            return []

        shares = []
        for line_num, line in enumerate(content_str.strip().splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                share_obj = json.loads(line)
                shares.append(share_obj)
            except Exception:
                shares.append({"raw": line, "line_num": line_num})
        return shares

    def push_shares(self, shares: list) -> bool:
        """Updates shares.txt locally and pushes verification status to GitHub."""
        content_str = "\n".join(json.dumps(s) for s in shares) + "\n"
        try:
            with open(self.shares_file, "w", encoding="utf-8") as f:
                f.write(content_str)
        except Exception as e:
            log_error(f"Failed to write local shares.txt: {e}")

        if self.mode == "local":
            return True

        if self.mode == "api":
            try:
                existing = self._api_request(f"contents/shares.txt?ref={self.branch}")
                if existing and "sha" in existing:
                    self.sha_cache["shares.txt"] = existing["sha"]

                encoded_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
                payload = {
                    "message": "Update shares verification status",
                    "content": encoded_content,
                    "branch": self.branch,
                }
                if "shares.txt" in self.sha_cache and self.sha_cache["shares.txt"]:
                    payload["sha"] = self.sha_cache["shares.txt"]

                res = self._api_request("contents/shares.txt", data=payload, method="PUT")
                if res and "content" in res and "sha" in res["content"]:
                    self.sha_cache["shares.txt"] = res["content"]["sha"]
                log_github(f"Pushed updated shares.txt to GitHub repo {self.repo}")
                return True
            except Exception as e:
                log_error(f"GitHub API push shares.txt failed: {e}")
                self.sha_cache.pop("shares.txt", None)
                return False

        elif self.mode == "git":
            try:
                subprocess.run(["git", "add", "shares.txt"], cwd=self.git_dir, check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "Update shares verification status"], cwd=self.git_dir, check=True, capture_output=True)
                subprocess.run(["git", "push", "origin", self.branch], cwd=self.git_dir, check=True, capture_output=True)
                log_github(f"Git pushed shares.txt to origin/{self.branch}")
                return True
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
                log_error(f"Git push shares.txt failed: {stderr}")
                return False

        return False


class StratumBridgeClient:
    """Manages the live Stratum TCP connection to prl.kryptex.network:7048."""

    def __init__(self, host: str, port: int, wallet: str, worker: str, sync_manager: GitHubSyncManager):
        self.host = host
        self.port = port
        self.wallet = wallet
        self.worker = worker
        self.sync_mgr = sync_manager

        self.sock = None
        self.sock_file = None
        self.connected = False
        self.req_id = 1
        self.lock = threading.Lock()

        self.current_job = None
        self.current_job_id = None
        self.last_uploaded_job_id = None
        self.last_upload_time = 0

        self.pending_submits = {}
        self.submitted_hashes = set()
        self.running = True

        self.shares_accepted = 0
        self.shares_rejected = 0

    def connect(self) -> bool:
        """Establishes TCP connection and authorizes with Kryptex Pearl pool."""
        try:
            log_stratum(f"Connecting to Stratum pool {self.host}:{self.port}...")
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(15.0)
            self.sock.connect((self.host, self.port))
            self.sock_file = self.sock.makefile("r", encoding="utf-8")

            # Kryptex V2 mining.authorize format
            auth_login = f"{self.wallet}.{self.worker}"
            auth_req = {
                "jsonrpc": "2.0",
                "id": self.req_id,
                "method": "mining.authorize",
                "params": {
                    "wallet": auth_login,
                    "agent": "pearl-miner/1.0.0",
                    "type": "v2"
                }
            }
            self.req_id += 1

            log_stratum(f"Authorizing as {BOLD}{auth_login}{RESET} (V2 Protocol)...")
            self.sock.sendall((json.dumps(auth_req) + "\n").encode("utf-8"))

            line = self.sock_file.readline()
            if not line:
                log_error("Pool closed connection during authorization.")
                self.cleanup()
                return False

            resp = json.loads(line)
            if resp.get("result") is True or resp.get("error") is None:
                log_stratum(f"{GREEN}Authorized successfully{RESET} as {BOLD}{auth_login}{RESET}")
                self.connected = True
                return True
            else:
                log_error(f"Authorization rejected by pool: {resp.get('error')}")
                return False

        except Exception as e:
            log_error(f"Connection failed: {e}")
            self.cleanup()
            return False

    def cleanup(self):
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self.sock_file = None

    def send_submit(self, share: dict, callback=None) -> int:
        """Sends mining.submit to the Kryptex Pearl pool."""
        if not self.connected or not self.sock:
            log_error("Cannot submit share: not connected to Stratum pool")
            return -1

        with self.lock:
            sub_id = self.req_id
            self.req_id += 1

            if callback:
                self.pending_submits[sub_id] = callback

            # Extract fields flexibly
            job_id = share.get("job_id", self.current_job_id)
            proof = share.get("plain_proof")

            # Check inside params if plain_proof not directly at top-level
            if not proof and isinstance(share.get("params"), dict):
                proof = share["params"].get("plain_proof") or share["params"].get("proof")
            elif not proof and isinstance(share.get("params"), list) and len(share["params"]) >= 3:
                proof = share["params"][2]

            hs = share.get("hs")
            if not hs and isinstance(share.get("params"), dict):
                hs = share["params"].get("hs", 1000)
            if not hs:
                hs = 1000

            # Kryptex Pearl pool requires object format: {"hs": ..., "job_id": ..., "plain_proof": ...}
            if proof:
                submit_req = {
                    "jsonrpc": "2.0",
                    "id": sub_id,
                    "method": "mining.submit",
                    "params": {
                        "hs": int(hs),
                        "job_id": str(job_id),
                        "plain_proof": str(proof)
                    }
                }
            elif isinstance(share.get("params"), dict):
                submit_req = {
                    "jsonrpc": "2.0",
                    "id": sub_id,
                    "method": "mining.submit",
                    "params": share["params"]
                }
            elif "nonce" in share:
                submit_req = {
                    "jsonrpc": "2.0",
                    "id": sub_id,
                    "method": "mining.submit",
                    "params": [
                        f"{self.wallet}.{self.worker}",
                        str(job_id),
                        share.get("extra_nonce2", "00000000"),
                        share.get("ntime", ""),
                        share.get("nonce", "")
                    ]
                }
            else:
                log_error(f"Unrecognized share format: {share}")
                return -1

            try:
                msg = json.dumps(submit_req) + "\n"
                log_stratum(f"Submitting share (ID: {sub_id}) for Job {job_id} to pool...")
                self.sock.sendall(msg.encode("utf-8"))
                return sub_id
            except Exception as e:
                log_error(f"Failed to send share over socket: {e}")
                return -1

    def handle_line(self, line: str):
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except Exception:
            return

        method = msg.get("method")
        msg_id = msg.get("id")

        # Inbound new job from pool
        if method == "mining.notify":
            params = msg.get("params", {})
            if isinstance(params, dict):
                job_id = params.get("job_id")
                header = params.get("header")
                target = params.get("target")
                height = params.get("height")
                cert_v = params.get("cert_version", 3)
            elif isinstance(params, list) and len(params) >= 8:
                job_id = params[0]
                header = params[1]
                target = params[6]
                height = 0
                cert_v = 3
            else:
                log_error(f"Unknown mining.notify params format: {params}")
                return

            if job_id != self.current_job_id:
                self.current_job_id = job_id
                self.current_job = {
                    "job_id": job_id,
                    "header": header,
                    "target": target,
                    "height": height,
                    "cert_version": cert_v,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "pool": f"{self.host}:{self.port}",
                    "wallet": self.wallet,
                    "worker": self.worker,
                    "raw_notify": msg,
                }
                log_stratum(f"{BOLD}New Job Received:{RESET} ID={job_id} | Height={height} | Target={target[:16]}...")
                # Write local jobs.txt immediately so local miners have instant access
                try:
                    with open(self.sync_mgr.jobs_file, "w", encoding="utf-8") as f:
                        f.write(json.dumps(self.current_job, indent=2) + "\n")
                except Exception:
                    pass

        # Response to mining.submit
        elif msg_id is not None and msg_id in self.pending_submits:
            cb = self.pending_submits.pop(msg_id)
            cb(msg)
        elif msg_id is not None:
            if msg.get("result") is True:
                self.shares_accepted += 1
                log_success(f"Share accepted by pool! Total accepted: {self.shares_accepted}")
            elif msg.get("result") is False or msg.get("error"):
                self.shares_rejected += 1
                err = msg.get("error")
                log_error(f"Share rejected by pool: {err} | Total rejected: {self.shares_rejected}")

    def stratum_listen_loop(self):
        """Continuously reads messages from Stratum server, reconnecting as needed."""
        while self.running:
            if not self.connected:
                if not self.connect():
                    time.sleep(5)
                    continue

            try:
                line = self.sock_file.readline()
                if not line:
                    log_error("Stratum connection dropped by server. Reconnecting...")
                    self.cleanup()
                    time.sleep(3)
                    continue

                self.handle_line(line)

            except socket.timeout:
                continue
            except Exception as e:
                log_error(f"Socket read error: {e}")
                self.cleanup()
                time.sleep(3)

    def job_upload_loop(self, interval=30):
        """
        Periodically uploads the latest received mining job to GitHub repository
        at intervals of 30 seconds.
        """
        log_info(f"Job GitHub sync loop active (upload interval: {interval}s)")

        while self.running:
            try:
                if self.connected and self.current_job:
                    # Upload immediately on initial start, or every 30s interval
                    now = time.time()
                    should_upload = (self.last_uploaded_job_id is None) or (now - self.last_upload_time >= interval)

                    if should_upload:
                        job_to_push = dict(self.current_job)
                        job_id = job_to_push.get("job_id")
                        log_github(f"Uploading latest active job {BOLD}{job_id}{RESET} to GitHub (30s interval)...")
                        if self.sync_mgr.push_jobs(job_to_push):
                            self.last_uploaded_job_id = job_id
                            self.last_upload_time = time.time()
            except Exception as e:
                log_error(f"Job upload error: {e}")

            # Sleep briefly while checking running state
            for _ in range(int(interval * 2)):
                if not self.running:
                    break
                time.sleep(0.5)

    def shares_sync_loop(self, poll_interval=1.0):
        """
        Continuously monitors shares.txt (interval: 1.0s),
        submits pending shares to the pool immediately, and updates verification status.
        """
        log_info(f"Shares continuous monitor loop started (checking every {poll_interval}s)")

        while self.running:
            time.sleep(poll_interval)
            if not self.connected:
                continue

            try:
                shares = self.sync_mgr.pull_shares()
                if not shares:
                    continue

                shares_modified = False

                for share in shares:
                    if not isinstance(share, dict):
                        continue

                    # Unique share signature
                    sig = share.get("share_id") or share.get("plain_proof") or str(share.get("params")) or str(share)
                    if sig in self.submitted_hashes or share.get("submitted") is True:
                        continue

                    job_id = share.get("job_id", self.current_job_id)
                    log_info(f"Submitting pending share for Job {job_id}...")

                    event = threading.Event()
                    result_holder = {}

                    def on_response(resp):
                        result_holder["resp"] = resp
                        event.set()

                    sub_id = self.send_submit(share, callback=on_response)
                    if sub_id != -1:
                        # Wait for pool reply
                        if event.wait(timeout=5.0):
                            resp = result_holder.get("resp", {})
                            if resp.get("result") is True:
                                self.shares_accepted += 1
                                log_success(f"Share for Job {job_id} ACCEPTED by pool! (Total: {self.shares_accepted})")
                                share["submitted"] = True
                                share["status"] = "accepted"
                                share["verified_at"] = datetime.now(timezone.utc).isoformat()
                            else:
                                self.shares_rejected += 1
                                err = resp.get("error", "Unknown error")
                                log_error(f"Share for Job {job_id} REJECTED: {err}")
                                share["submitted"] = True
                                share["status"] = "rejected"
                                share["error"] = str(err)
                                share["verified_at"] = datetime.now(timezone.utc).isoformat()
                        else:
                            log_info(f"Submit sent (ID {sub_id}); awaiting pool confirmation asynchronously.")
                            share["submitted"] = True
                            share["status"] = "submitted_pending_ack"

                        self.submitted_hashes.add(sig)
                        shares_modified = True

                if shares_modified:
                    self.sync_mgr.push_shares(shares)

            except Exception as e:
                log_error(f"Shares sync error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Pearl (PRL) Stratum Pool & GitHub Connector")
    parser.add_argument("--pool", default="prl.kryptex.network:7048", help="Stratum pool endpoint (host:port)")
    parser.add_argument("--wallet", default="krxYRPV4WQ", help="Kryptex wallet / username")
    parser.add_argument("--worker", default="worker1", help="Worker name (default: worker1)")

    # GitHub synchronization options
    parser.add_argument("--github-token", default=os.getenv("GITHUB_TOKEN"), help="GitHub Personal Access Token")
    parser.add_argument("--github-repo", default=os.getenv("GITHUB_REPO"), help="GitHub repository (e.g. username/pearl-miner)")
    parser.add_argument("--github-branch", default=os.getenv("GITHUB_BRANCH", "main"), help="GitHub branch (default: main)")
    parser.add_argument("--git-dir", default=".", help="Local git clone path (default: current directory)")
    parser.add_argument("--local-only", action="store_true", help="Operate with local files only (no GitHub sync)")
    parser.add_argument("--job-interval", type=int, default=30, help="Interval in seconds to upload latest jobs to GitHub (default: 30)")
    parser.add_argument("--share-poll-interval", type=float, default=1.0, help="Interval in seconds for continuous share checking and submission (default: 1.0)")

    args = parser.parse_args()

    host, port_str = args.pool.split(":")
    port = int(port_str)

    # Determine GitHub sync mode
    if args.local_only:
        sync_mode = "local"
        log_info("Mode: LOCAL FILES ONLY (No Git/GitHub push or pull)")
    elif args.github_token and args.github_repo:
        sync_mode = "api"
        log_info(f"Mode: GITHUB REST API (Repo: {args.github_repo}, Branch: {args.github_branch})")
    elif os.path.exists(os.path.join(args.git_dir, ".git")):
        sync_mode = "git"
        log_info(f"Mode: LOCAL GIT CLI (Repo path: {os.path.abspath(args.git_dir)})")
    else:
        sync_mode = "local"
        log_info("Mode: LOCAL (Neither GitHub Token nor .git found; saving files locally in current directory)")
        log_info("Tip: Pass --github-token and --github-repo to auto-sync to GitHub!")

    sync_mgr = GitHubSyncManager(
        mode=sync_mode,
        token=args.github_token,
        repo=args.github_repo,
        branch=args.github_branch,
        git_dir=args.git_dir,
    )

    client = StratumBridgeClient(
        host=host,
        port=port,
        wallet=args.wallet,
        worker=args.worker,
        sync_manager=sync_mgr,
    )

    print(f"\n{BOLD}{CYAN}===================================================={RESET}")
    print(f"{BOLD}   PEARL STRATUM CONNECTOR & GITHUB BRIDGE{RESET}")
    print(f"{BOLD}{CYAN}===================================================={RESET}")
    print(f"Pool:          {BOLD}{args.pool}{RESET}")
    print(f"Wallet:        {BOLD}{args.wallet}{RESET}")
    print(f"Worker:        {BOLD}{args.worker}{RESET}")
    print(f"Job Interval:  {BOLD}{args.job_interval} seconds{RESET}")
    print(f"Share Poll:    {BOLD}{args.share_poll_interval} seconds (continuous){RESET}")
    print(f"Jobs File:     {os.path.abspath(sync_mgr.jobs_file)}")
    print(f"Shares File:   {os.path.abspath(sync_mgr.shares_file)}")
    print(f"{BOLD}{CYAN}===================================================={RESET}\n")

    t_stratum = threading.Thread(target=client.stratum_listen_loop, daemon=True)
    t_jobs = threading.Thread(target=client.job_upload_loop, args=(args.job_interval,), daemon=True)
    t_shares = threading.Thread(target=client.shares_sync_loop, args=(args.share_poll_interval,), daemon=True)

    t_stratum.start()
    t_jobs.start()
    t_shares.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_info("Shutting down connector...")
        client.running = False
        client.cleanup()
        sys.exit(0)


if __name__ == "__main__":
    main()
