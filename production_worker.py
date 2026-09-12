"""
production_worker.py  (Kaggle)
Full production worker: polls GitHub queue → generates video via ComfyUI/Wan2.1
→ converts to 9:16 → QC → uploads to Telegram → marks job complete on GitHub.

Run this notebook cell on Kaggle (GPU T4 x2 recommended).
"""

import base64
import glob
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
import websocket
from datetime import datetime
from pathlib import Path

import requests

# ── Kaggle Secrets ───────────────────────────────────────────────────────────
from kaggle_secrets import UserSecretsClient
secrets = UserSecretsClient()

GITHUB_TOKEN    = secrets.get_secret("GITHUB_TOKEN")
GITHUB_REPO     = secrets.get_secret("GITHUB_REPO")       # e.g. "user/ai-dance-video-agent"
GEMINI_API_KEY  = secrets.get_secret("GEMINI_API_KEY")
TELEGRAM_TOKEN  = secrets.get_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT   = secrets.get_secret("TELEGRAM_CHAT_ID")

os.environ["GITHUB_TOKEN"]       = GITHUB_TOKEN
os.environ["GITHUB_REPO"]        = GITHUB_REPO
os.environ["GEMINI_API_KEY"]     = GEMINI_API_KEY
os.environ["TELEGRAM_BOT_TOKEN"] = TELEGRAM_TOKEN
os.environ["TELEGRAM_CHAT_ID"]   = TELEGRAM_CHAT

# ── Paths ────────────────────────────────────────────────────────────────────
COMFYUI_HOST    = "127.0.0.1"
COMFYUI_PORT    = 8188
COMFYUI_URL     = f"http://{COMFYUI_HOST}:{COMFYUI_PORT}"
COMFYUI_OUTPUT  = "/kaggle/working/ComfyUI/output"
SHORTS_OUTPUT   = "/kaggle/working/shorts_output"
WORKFLOW_FILE   = "/kaggle/working/wan_api.json"
STATE_FILE      = "/kaggle/working/production_state.json"
CONTROL_FILE    = "/kaggle/working/agent_control.json"

QUEUE_PATH      = "data/dance_queue.json"
HISTORY_PATH    = "data/trend_history.json"
WORKER_PATH     = "data/worker_state.json"
SETTINGS_PATH   = "config/settings.json"

WORKER_ID = f"kaggle-{str(uuid.uuid4())[:6]}"

Path(SHORTS_OUTPUT).mkdir(parents=True, exist_ok=True)


# ── GitHub helpers ───────────────────────────────────────────────────────────

def gh_get(path: str) -> dict | None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        data = base64.b64decode(r.json()["content"]).decode()
        return {"data": json.loads(data), "sha": r.json()["sha"]}
    return None


def gh_put(path: str, data: dict, sha: str | None, msg: str = "update") -> bool:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    content = base64.b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    payload: dict = {"message": msg, "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload, timeout=15)
    return r.status_code in (200, 201)


# ── State helpers ────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"completed": [], "failed": [], "session_start": datetime.utcnow().isoformat()}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_paused() -> bool:
    try:
        with open(CONTROL_FILE) as f:
            ctrl = json.load(f)
        return ctrl.get("paused", False)
    except Exception:
        return False


def heartbeat(job_id: str | None = None, status: str = "working") -> None:
    result = gh_get(WORKER_PATH)
    ws = result["data"] if result else {}
    sha = result["sha"] if result else None
    ws.update({
        "status": status,
        "worker_id": WORKER_ID,
        "last_heartbeat": datetime.utcnow().isoformat(),
        "current_job_id": job_id,
    })
    gh_put(WORKER_PATH, ws, sha, "Heartbeat")


# ── Queue operations ─────────────────────────────────────────────────────────

def claim_next_job() -> tuple[dict | None, dict | None, str | None]:
    result = gh_get(QUEUE_PATH)
    if not result:
        return None, None, None
    q_data = result["data"]
    sha = result["sha"]
    for job in q_data.get("queue", []):
        if job.get("status") == "pending" and job.get("retries", 0) < 3:
            job["status"] = "processing"
            job["worker_id"] = WORKER_ID
            job["updated_at"] = datetime.utcnow().isoformat()
            q_data["metadata"]["last_updated"] = datetime.utcnow().isoformat()
            if gh_put(QUEUE_PATH, q_data, sha, f"Claim {job['job_id'][:8]}"):
                return job, q_data, sha
            return None, None, None
    return None, None, None


def complete_job(job_id: str, output_path: str) -> None:
    result = gh_get(QUEUE_PATH)
    if not result:
        return
    q_data, sha = result["data"], result["sha"]
    for job in q_data.get("queue", [])[:]:
        if job["job_id"] == job_id:
            job.update({"status": "completed", "output_path": output_path,
                        "completed_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()})
            q_data.setdefault("completed", []).append(job)
            q_data["queue"].remove(job)
            q_data["metadata"]["total_generated"] = q_data["metadata"].get("total_generated", 0) + 1
            break
    gh_put(QUEUE_PATH, q_data, sha, f"Complete {job_id[:8]}")


def fail_job(job_id: str, error: str) -> None:
    result = gh_get(QUEUE_PATH)
    if not result:
        return
    q_data, sha = result["data"], result["sha"]
    for job in q_data.get("queue", [])[:]:
        if job["job_id"] == job_id:
            job["retries"] = job.get("retries", 0) + 1
            job["error"] = error
            job["updated_at"] = datetime.utcnow().isoformat()
            if job["retries"] >= 3:
                job["status"] = "failed"
                q_data.setdefault("failed", []).append(job)
                q_data["queue"].remove(job)
            else:
                job["status"] = "pending"
            break
    gh_put(QUEUE_PATH, q_data, sha, f"Fail/retry {job_id[:8]}")


# ── ComfyUI generation ───────────────────────────────────────────────────────

def load_workflow() -> dict:
    with open(WORKFLOW_FILE) as f:
        return json.load(f)


def patch_workflow(workflow: dict, concept: dict) -> dict:
    """Inject the concept prompt into the ComfyUI workflow."""
    import copy
    wf = copy.deepcopy(workflow)
    prompt = concept.get("comfyui_prompt", "A beautiful dancer performs gracefully")
    negative = concept.get("negative_prompt", "blurry, low quality, static")

    for node_id, node in wf.items():
        cls = node.get("class_type", "")
        inputs = node.get("inputs", {})
        if cls in ("CLIPTextEncode", "WanTextEncode"):
            text = inputs.get("text", "")
            if "negative" in str(text).lower() or "negative" in str(node.get("_meta", {}).get("title", "")).lower():
                inputs["text"] = negative
            else:
                inputs["text"] = prompt
    return wf


def queue_prompt(workflow: dict) -> str | None:
    """Submit workflow to ComfyUI and return prompt_id."""
    client_id = str(uuid.uuid4())
    payload = {"prompt": workflow, "client_id": client_id}
    try:
        r = requests.post(f"{COMFYUI_URL}/prompt", json=payload, timeout=15)
        if r.status_code == 200:
            return r.json().get("prompt_id")
        print(f"[ComfyUI] Queue error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[ComfyUI] Queue exception: {e}")
    return None


def wait_for_generation(prompt_id: str, timeout: int = 600) -> list[str]:
    """Poll ComfyUI history until the prompt is done. Returns output file paths."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
            if r.status_code == 200:
                history = r.json()
                if prompt_id in history:
                    outputs = []
                    for node_output in history[prompt_id].get("outputs", {}).values():
                        for video in node_output.get("videos", []):
                            filename = video.get("filename", "")
                            subfolder = video.get("subfolder", "")
                            full_path = str(Path(COMFYUI_OUTPUT) / subfolder / filename)
                            outputs.append(full_path)
                        for img in node_output.get("images", []):
                            filename = img.get("filename", "")
                            full_path = str(Path(COMFYUI_OUTPUT) / filename)
                            outputs.append(full_path)
                    if outputs:
                        return outputs
        except Exception as e:
            print(f"[ComfyUI] Poll error: {e}")
        time.sleep(10)
    return []


# ── Video processing ─────────────────────────────────────────────────────────

def convert_9x16(input_path: str, job_id: str) -> str | None:
    import subprocess
    output_path = str(Path(SHORTS_OUTPUT) / f"{job_id}_9x16.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=576:1024:force_original_aspect_ratio=increase,crop=576:1024",
        "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and Path(output_path).exists():
            return output_path
        print(f"[FFmpeg] Error: {result.stderr[-300:]}")
    except Exception as e:
        print(f"[FFmpeg] Exception: {e}")
    return None


def qc_check(video_path: str) -> bool:
    """Basic QC: file exists and > 500KB."""
    p = Path(video_path)
    if not p.exists():
        return False
    size_kb = p.stat().st_size / 1024
    return size_kb >= 500


# ── Telegram ─────────────────────────────────────────────────────────────────

def tg_send(text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def tg_send_video(path: str, caption: str) -> bool:
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                data={"chat_id": TELEGRAM_CHAT, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"video": f},
                timeout=120,
            )
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram] Video send error: {e}")
        return False


# ── Main production loop ─────────────────────────────────────────────────────

def process_job(job: dict) -> bool:
    job_id = job["job_id"]
    concept = job.get("concept", {})
    title = concept.get("concept_title", "Dance Video")
    print(f"\n{'='*60}")
    print(f"[Worker] 🎬 Processing: {title}")
    print(f"[Worker] Job ID: {job_id}")
    print(f"{'='*60}")

    tg_send(f"🎬 <b>Generating:</b> {title}\n📋 Job: {job_id[:8]}")

    # Load and patch workflow
    try:
        workflow = load_workflow()
    except Exception as e:
        print(f"[Worker] ❌ Workflow load failed: {e}")
        return False

    patched = patch_workflow(workflow, concept)

    # Submit to ComfyUI
    prompt_id = queue_prompt(patched)
    if not prompt_id:
        print("[Worker] ❌ ComfyUI queue failed")
        return False
    print(f"[Worker] ⏳ ComfyUI prompt_id: {prompt_id}")

    # Wait for output
    raw_files = wait_for_generation(prompt_id, timeout=600)
    if not raw_files:
        print("[Worker] ❌ No output from ComfyUI")
        return False
    print(f"[Worker] ✅ ComfyUI output: {raw_files}")

    # Find the video/image file
    raw_path = raw_files[0]
    if not Path(raw_path).exists():
        # Try glob in output dir
        found = glob.glob(str(Path(COMFYUI_OUTPUT) / "**" / "*.mp4"), recursive=True)
        found += glob.glob(str(Path(COMFYUI_OUTPUT) / "**" / "*.png"), recursive=True)
        if found:
            raw_path = max(found, key=os.path.getmtime)
        else:
            print("[Worker] ❌ Output file not found on disk")
            return False

    # Convert to 9:16
    final_path = convert_9x16(raw_path, job_id)
    if not final_path:
        print("[Worker] ❌ 9:16 conversion failed")
        return False

    # QC check
    if not qc_check(final_path):
        print("[Worker] ❌ QC failed")
        return False
    print(f"[Worker] ✅ QC passed: {final_path}")

    # Send via Telegram
    caption = (
        f"<b>🎬 {title}</b>\n"
        f"📈 {concept.get('trend_reference', '')}\n"
        f"💃 {concept.get('dance_style', '')}\n"
        f"✨ {concept.get('mood', '').capitalize()}\n"
        "#HindiDance #BollywoodShorts #AIVideo"
    )
    sent = tg_send_video(final_path, caption)
    if sent:
        print("[Worker] ✅ Sent to Telegram")
    else:
        print("[Worker] ⚠️  Telegram send failed (video still saved)")

    return True


def run():
    settings_result = gh_get(SETTINGS_PATH)
    settings = settings_result["data"] if settings_result else {}
    target = settings.get("production", {}).get("target_videos", 10)

    state = load_state()
    tg_send(f"🤖 <b>Worker Online</b>\n🆔 {WORKER_ID}\n🎯 Target: {target} videos")

    print(f"[Worker] {WORKER_ID} starting. Target: {target} videos")
    consecutive_empty = 0

    while True:
        if is_paused():
            print("[Worker] ⏸  Paused. Waiting...")
            time.sleep(30)
            continue

        # Check if target reached
        if len(state.get("completed", [])) >= target:
            print(f"[Worker] 🎉 Target of {target} reached!")
            tg_send(f"🎉 <b>Target Reached!</b>\n✅ {target} videos generated successfully!")
            heartbeat(status="complete")
            break

        # Claim next job
        job, _, _ = claim_next_job()
        if not job:
            consecutive_empty += 1
            print(f"[Worker] 💤 No jobs available ({consecutive_empty}/5)")
            if consecutive_empty >= 5:
                print("[Worker] Shutting down — no work available")
                heartbeat(status="idle")
                break
            time.sleep(30)
            continue

        consecutive_empty = 0
        job_id = job["job_id"]
        heartbeat(job_id=job_id, status="working")

        try:
            success = process_job(job)
        except Exception as e:
            print(f"[Worker] ❌ Unexpected error: {e}")
            success = False

        if success:
            final_path = str(Path(SHORTS_OUTPUT) / f"{job_id}_9x16.mp4")
            complete_job(job_id, final_path)
            state.setdefault("completed", []).append({
                "job_id": job_id,
                "completed_at": datetime.utcnow().isoformat(),
                "path": final_path,
            })
            save_state(state)
            print(f"[Worker] ✅ Job {job_id[:8]} complete ({len(state['completed'])}/{target})")
        else:
            fail_job(job_id, "Processing failed")
            state.setdefault("failed", []).append({"job_id": job_id, "failed_at": datetime.utcnow().isoformat()})
            save_state(state)
            print(f"[Worker] ❌ Job {job_id[:8]} failed")

        heartbeat(status="working")
        time.sleep(5)  # Brief pause between jobs


if __name__ == "__main__":
    run()
