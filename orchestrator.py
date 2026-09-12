"""
orchestrator.py
Controls queue/production logic: dispatches pending jobs,
recovers stuck jobs, and monitors overall production progress.
Runs on GitHub Actions or locally.
"""

import json
import os
import time
from datetime import datetime, timedelta

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

QUEUE_PATH = "data/dance_queue.json"
WORKER_STATE_PATH = "data/worker_state.json"
SETTINGS_PATH = "config/settings.json"


# ── GitHub helpers ───────────────────────────────────────────────────────────

def github_get(path: str) -> dict | None:
    import base64
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        content = base64.b64decode(r.json()["content"]).decode()
        return {"data": json.loads(content), "sha": r.json()["sha"]}
    return None


def github_put(path: str, data: dict, sha: str | None = None, message: str = "update") -> bool:
    import base64
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    content = base64.b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    payload: dict = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload, timeout=15)
    return r.status_code in (200, 201)


# ── Queue operations ─────────────────────────────────────────────────────────

def get_queue() -> tuple[dict, str | None]:
    result = github_get(QUEUE_PATH)
    if result:
        return result["data"], result["sha"]
    return {"queue": [], "completed": [], "failed": [], "metadata": {}}, None


def recover_stuck_jobs(queue_data: dict, timeout_minutes: int = 30) -> int:
    """Reset jobs that have been 'processing' for too long back to 'pending'."""
    recovered = 0
    cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
    for job in queue_data.get("queue", []):
        if job.get("status") == "processing":
            updated = job.get("updated_at", "")
            try:
                if updated and datetime.fromisoformat(updated) < cutoff:
                    job["status"] = "pending"
                    job["updated_at"] = datetime.utcnow().isoformat()
                    job["error"] = f"Recovered from stuck state after {timeout_minutes}min"
                    recovered += 1
            except ValueError:
                pass
    return recovered


def get_next_pending_job(queue_data: dict) -> dict | None:
    for job in queue_data.get("queue", []):
        if job.get("status") == "pending" and job.get("retries", 0) < 3:
            return job
    return None


def print_summary(queue_data: dict) -> None:
    q = queue_data.get("queue", [])
    completed = queue_data.get("completed", [])
    failed = queue_data.get("failed", [])

    pending = sum(1 for j in q if j.get("status") == "pending")
    processing = sum(1 for j in q if j.get("status") == "processing")
    done = sum(1 for j in q if j.get("status") == "completed")

    print("\n" + "="*50)
    print("📊 PRODUCTION SUMMARY")
    print("="*50)
    print(f"  Pending:     {pending}")
    print(f"  Processing:  {processing}")
    print(f"  Completed:   {done + len(completed)}")
    print(f"  Failed:      {len(failed)}")
    print(f"  Total jobs:  {len(q) + len(completed) + len(failed)}")
    meta = queue_data.get("metadata", {})
    print(f"  Last update: {meta.get('last_updated', 'unknown')}")
    print("="*50 + "\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    print("[Orchestrator] Starting...")

    settings_result = github_get(SETTINGS_PATH)
    settings = settings_result["data"] if settings_result else {}
    timeout = settings.get("production", {}).get("stuck_job_timeout_minutes", 30)
    target = settings.get("production", {}).get("target_videos", 10)

    queue_data, sha = get_queue()
    print_summary(queue_data)

    # Recover stuck jobs
    recovered = recover_stuck_jobs(queue_data, timeout)
    if recovered:
        print(f"[Orchestrator] ♻️  Recovered {recovered} stuck job(s)")
        github_put(QUEUE_PATH, queue_data, sha, f"Recover {recovered} stuck jobs")
        _, sha = get_queue()  # Refresh SHA

    # Check worker heartbeat
    ws_result = github_get(WORKER_STATE_PATH)
    if ws_result:
        ws = ws_result["data"]
        last_hb = ws.get("last_heartbeat", "")
        if last_hb:
            try:
                hb_dt = datetime.fromisoformat(last_hb)
                age = (datetime.utcnow() - hb_dt).total_seconds() / 60
                if age < 5:
                    print(f"[Orchestrator] ✅ Worker alive (heartbeat {age:.1f}min ago)")
                else:
                    print(f"[Orchestrator] ⚠️  Worker may be idle (heartbeat {age:.1f}min ago)")
            except ValueError:
                pass

    # Count totals
    q = queue_data.get("queue", [])
    completed_count = len([j for j in q if j.get("status") == "completed"]) + \
                      len(queue_data.get("completed", []))

    pending_count = len([j for j in q if j.get("status") == "pending"])

    if completed_count >= target:
        print(f"[Orchestrator] 🎉 Target of {target} videos reached!")
    elif pending_count == 0:
        print("[Orchestrator] ⚠️  No pending jobs. Run trend_agent.py to add more.")
    else:
        print(f"[Orchestrator] 🚀 {pending_count} jobs pending. Worker should pick them up.")

    print("[Orchestrator] Done.")


if __name__ == "__main__":
    run()
