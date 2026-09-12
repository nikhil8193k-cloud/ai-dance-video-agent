"""
worker.py
GitHub-side worker logic: polls queue, claims jobs, updates status.
Actual generation happens in production_worker.py on Kaggle.
"""

import json
import os
import time
import uuid
from datetime import datetime

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

QUEUE_PATH = "data/dance_queue.json"
WORKER_STATE_PATH = "data/worker_state.json"

WORKER_ID = f"worker-{str(uuid.uuid4())[:8]}"


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


def claim_job(queue_data: dict) -> dict | None:
    """Find and claim the next pending job."""
    for job in queue_data.get("queue", []):
        if job.get("status") == "pending" and job.get("retries", 0) < 3:
            job["status"] = "processing"
            job["worker_id"] = WORKER_ID
            job["updated_at"] = datetime.utcnow().isoformat()
            return job
    return None


def mark_job_complete(queue_data: dict, job_id: str, output_path: str) -> None:
    for job in queue_data.get("queue", []):
        if job.get("job_id") == job_id:
            job["status"] = "completed"
            job["output_path"] = output_path
            job["completed_at"] = datetime.utcnow().isoformat()
            job["updated_at"] = datetime.utcnow().isoformat()
            # Move to completed list
            queue_data.setdefault("completed", []).append(job)
            queue_data["queue"].remove(job)
            queue_data["metadata"]["total_generated"] = \
                queue_data["metadata"].get("total_generated", 0) + 1
            break


def mark_job_failed(queue_data: dict, job_id: str, error: str) -> None:
    for job in queue_data.get("queue", []):
        if job.get("job_id") == job_id:
            job["retries"] = job.get("retries", 0) + 1
            job["error"] = error
            job["updated_at"] = datetime.utcnow().isoformat()
            if job["retries"] >= 3:
                job["status"] = "failed"
                queue_data.setdefault("failed", []).append(job)
                queue_data["queue"].remove(job)
                queue_data["metadata"]["total_failed"] = \
                    queue_data["metadata"].get("total_failed", 0) + 1
            else:
                job["status"] = "pending"  # Retry
            break


def update_heartbeat(status: str = "working", current_job: str | None = None) -> None:
    ws_result = github_get(WORKER_STATE_PATH)
    if ws_result:
        ws = ws_result["data"]
        sha = ws_result["sha"]
    else:
        ws = {}
        sha = None
    ws.update({
        "status": status,
        "worker_id": WORKER_ID,
        "last_heartbeat": datetime.utcnow().isoformat(),
        "current_job_id": current_job,
    })
    github_put(WORKER_STATE_PATH, ws, sha, "Worker heartbeat")


def get_pending_job() -> tuple[dict | None, dict | None, str | None]:
    """Fetch queue and claim next pending job atomically."""
    result = github_get(QUEUE_PATH)
    if not result:
        return None, None, None
    queue_data = result["data"]
    sha = result["sha"]
    job = claim_job(queue_data)
    if job:
        queue_data["metadata"]["last_updated"] = datetime.utcnow().isoformat()
        success = github_put(QUEUE_PATH, queue_data, sha, f"Claim job {job['job_id'][:8]}")
        if success:
            return job, queue_data, sha
    return None, None, None


if __name__ == "__main__":
    print(f"[Worker] {WORKER_ID} checking queue...")
    job, queue_data, sha = get_pending_job()
    if job:
        print(f"[Worker] Claimed job: {job['job_id']}")
        print(f"[Worker] Concept: {job['concept'].get('concept_title', 'unknown')}")
        update_heartbeat("working", job["job_id"])
    else:
        print("[Worker] No pending jobs.")
        update_heartbeat("idle")
