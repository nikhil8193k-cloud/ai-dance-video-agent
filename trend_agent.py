import os
import json
import base64
import time
import requests
from datetime import datetime, timezone

from google import genai


# ============================================================
# CONFIG
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

SETTINGS_PATH = "config/settings.json"
QUEUE_PATH = "data/dance_queue.json"
HISTORY_PATH = "data/trend_history.json"

GEMINI_MODEL = "gemini-3.6-flash"

MAX_TRENDS = 10
MAX_CONCEPTS = 7

GITHUB_API = "https://api.github.com"


# ============================================================
# LOGGING
# ============================================================

def log(message):
    print(f"[TrendAgent] {message}", flush=True)


# ============================================================
# TIME
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# GEMINI
# ============================================================

def create_gemini_client():
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing")

    return genai.Client(api_key=GEMINI_API_KEY)


def gemini_generate(client, prompt):
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    if not response or not response.text:
        raise RuntimeError("Gemini returned an empty response")

    return response.text.strip()


# ============================================================
# GITHUB HEADERS
# ============================================================

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ============================================================
# GITHUB GET FILE
# ============================================================

def github_get(path):
    if not GITHUB_TOKEN:
        log("ERROR: GITHUB_TOKEN is missing")
        return None

    if not GITHUB_REPO:
        log("ERROR: GITHUB_REPO is missing")
        return None

    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"

    try:
        r = requests.get(
            url,
            headers=github_headers(),
            timeout=30,
        )
    except Exception as e:
        log(f"GitHub GET exception: {e}")
        return None

    if r.status_code == 200:
        try:
            body = r.json()
            content = base64.b64decode(body["content"]).decode("utf-8")

            return {
                "data": json.loads(content),
                "sha": body["sha"],
            }

        except Exception as e:
            log(f"GitHub GET parse error: {e}")
            return None

    if r.status_code == 404:
        return None

    log(f"GitHub GET FAILED: HTTP {r.status_code}")
    log(f"GitHub response: {r.text}")

    return None


# ============================================================
# GITHUB PUT FILE
# ============================================================

def github_put(path, data, sha=None, message="update"):
    if not GITHUB_TOKEN:
        log("ERROR: GITHUB_TOKEN is missing")
        return False

    if not GITHUB_REPO:
        log("ERROR: GITHUB_REPO is missing")
        return False

    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"

    content = base64.b64encode(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": message,
        "content": content,
    }

    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(
            url,
            headers=github_headers(),
            json=payload,
            timeout=30,
        )
    except Exception as e:
        log(f"GitHub PUT exception: {e}")
        return False

    if r.status_code in (200, 201):
        log(f"GitHub update successful: {path}")
        return True

    log(f"GitHub update FAILED: HTTP {r.status_code}")
    log(f"GitHub response: {r.text}")

    return False


# ============================================================
# LOAD SETTINGS
# ============================================================

def load_settings():
    defaults = {
        "dance_style": "Indian fusion",
        "video_duration": 5,
        "aspect_ratio": "9:16",
        "quality_threshold": 5,
    }

    try:
        result = github_get(SETTINGS_PATH)

        if result and isinstance(result.get("data"), dict):
            settings = defaults.copy()
            settings.update(result["data"])
            return settings

    except Exception as e:
        log(f"Settings load error: {e}")

    return defaults


# ============================================================
# TREND DISCOVERY
# ============================================================

def discover_trends(client):
    prompt = """
You are a viral short-video trend researcher.

Generate 30 current-style dance/video trend concepts suitable for
Instagram Reels, YouTube Shorts and TikTok.

Focus on:
- Indian dance
- Bollywood
- Garba
- Kathak
- Bharatanatyam
- Bhangra
- Sangeet
- Indian fusion
- Modern cinematic dance
- Fashion + dance
- High-energy choreography
- Visually interesting locations
- Viral short-form video concepts

Return ONLY a JSON array.

Each item must have:

{
  "trend": "short trend name",
  "style": "dance style",
  "hook": "viral hook",
  "score": 1-10
}

Do not include markdown.
"""

    text = gemini_generate(client, prompt)

    try:
        data = json.loads(text)
    except Exception:
        # Try extracting JSON if Gemini added extra text
        start = text.find("[")
        end = text.rfind("]")

        if start == -1 or end == -1:
            raise RuntimeError("Could not parse Gemini trend JSON")

        data = json.loads(text[start:end + 1])

    if not isinstance(data, list):
        raise RuntimeError("Gemini trends response was not a list")

    return data


# ============================================================
# CONCEPT GENERATION
# ============================================================

def generate_concept(client, trend, settings):
    prompt = f"""
Create one production-ready AI dance video concept.

TREND:
{json.dumps(trend, ensure_ascii=False)}

SETTINGS:
{json.dumps(settings, ensure_ascii=False)}

Return ONLY valid JSON.

Required format:

{{
  "title": "short catchy title",
  "dance_style": "specific dance style",
  "visual_prompt": "detailed cinematic AI video prompt",
  "negative_prompt": "things to avoid",
  "duration": 5,
  "aspect_ratio": "9:16"
}}

The visual prompt must describe:
- one main dancer
- full body visible
- clear dance movement
- cinematic lighting
- attractive environment
- fashionable clothing
- realistic human anatomy
- dynamic camera
- vertical short-video composition
- no text
- no logos
"""

    text = gemini_generate(client, prompt)

    try:
        concept = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1:
            raise RuntimeError("Could not parse Gemini concept JSON")

        concept = json.loads(text[start:end + 1])

    return concept


# ============================================================
# HISTORY
# ============================================================

def load_history():
    result = github_get(HISTORY_PATH)

    if result and isinstance(result.get("data"), dict):
        return result["data"], result["sha"]

    history = {
        "used_trends": [],
        "updated_at": now_iso(),
    }

    return history, None


# ============================================================
# QUEUE
# ============================================================

def load_queue():
    result = github_get(QUEUE_PATH)

    if result:
        data = result.get("data")

        if isinstance(data, dict):
            return data, result.get("sha")

    queue = {
        "queue": [],
        "completed": [],
        "failed": [],
        "metadata": {
            "created_at": now_iso(),
            "last_updated": now_iso(),
            "total_generated": 0,
            "total_failed": 0,
        },
    }

    return queue, None


# ============================================================
# ADD JOBS TO QUEUE
# ============================================================

def add_jobs_to_queue(jobs):
    if not jobs:
        log("No jobs to add")
        return False

    # Retry in case another process changed the queue SHA
    for attempt in range(3):
        queue, sha = load_queue()

        if not isinstance(queue, dict):
            queue = {
                "queue": [],
                "completed": [],
                "failed": [],
                "metadata": {},
            }

        if "queue" not in queue:
            queue["queue"] = []

        if "completed" not in queue:
            queue["completed"] = []

        if "failed" not in queue:
            queue["failed"] = []

        if "metadata" not in queue:
            queue["metadata"] = {}

        for job in jobs:
            queue["queue"].append(job)

        queue["metadata"]["last_updated"] = now_iso()

        if "created_at" not in queue["metadata"]:
            queue["metadata"]["created_at"] = now_iso()

        success = github_put(
            QUEUE_PATH,
            queue,
            sha=sha,
            message=f"Add {len(jobs)} AI dance jobs",
        )

        if success:
            log(f"SUCCESS: {len(jobs)} jobs added to GitHub queue")
            return True

        log(f"Queue write attempt {attempt + 1}/3 failed")

        if attempt < 2:
            time.sleep(3)

    log("ERROR: Could not update dance queue after 3 attempts")
    return False


# ============================================================
# UPDATE HISTORY
# ============================================================

def update_history(trends):
    if not trends:
        return True

    history, sha = load_history()

    if not isinstance(history, dict):
        history = {}

    if "used_trends" not in history:
        history["used_trends"] = []

    for trend in trends:
        trend_name = trend.get("trend")

        if trend_name and trend_name not in history["used_trends"]:
            history["used_trends"].append(trend_name)

    # Keep history manageable
    history["used_trends"] = history["used_trends"][-500:]
    history["updated_at"] = now_iso()

    return github_put(
        HISTORY_PATH,
        history,
        sha=sha,
        message="Update dance trend history",
    )


# ============================================================
# MAIN
# ============================================================

def run():
    log("Starting trend discovery...")

    # --------------------------------------------------------
    # Validate environment
    # --------------------------------------------------------

    if not GEMINI_API_KEY:
        log("ERROR: GEMINI_API_KEY is missing")
        return False

    if not GITHUB_TOKEN:
        log("ERROR: GITHUB_TOKEN is missing")
        return False

    if not GITHUB_REPO:
        log("ERROR: GITHUB_REPO is missing")
        return False

    log(f"GitHub repository: {GITHUB_REPO}")
    log(f"Gemini model: {GEMINI_MODEL}")

    # --------------------------------------------------------
    # Gemini client
    # --------------------------------------------------------

    try:
        client = create_gemini_client()
    except Exception as e:
        log(f"ERROR creating Gemini client: {e}")
        return False

    # --------------------------------------------------------
    # Settings
    # --------------------------------------------------------

    settings = load_settings()

    # --------------------------------------------------------
    # Discover trends
    # --------------------------------------------------------

    try:
        trends = discover_trends(client)
    except Exception as e:
        log(f"ERROR discovering trends: {e}")
        return False

    log(f"Found {len(trends)} raw trends")

    # --------------------------------------------------------
    # Load history
    # --------------------------------------------------------

    history, _ = load_history()

    used_trends = set(
        history.get("used_trends", [])
        if isinstance(history, dict)
        else []
    )

    unused = []

    for trend in trends:
        name = str(trend.get("trend", "")).strip()

        if name and name not in used_trends:
            unused.append(trend)

    log(f"{len(unused)} unused trends after history filter")

    # --------------------------------------------------------
    # Score/filter trends
    # --------------------------------------------------------

    scored = []

    for trend in unused:
        try:
            score = int(trend.get("score", 0))
        except Exception:
            score = 0

        if score >= 5:
            scored.append(trend)

    scored.sort(
        key=lambda x: int(x.get("score", 0)),
        reverse=True,
    )

    selected = scored[:MAX_TRENDS]

    log(f"{len(selected)} trends scored ≥5")

    if not selected:
        log("No suitable trends found")
        return True

    # --------------------------------------------------------
    # Generate concepts
    # --------------------------------------------------------

    jobs = []

    for index, trend in enumerate(selected):
        if len(jobs) >= MAX_CONCEPTS:
            break

        try:
            concept = generate_concept(
                client,
                trend,
                settings,
            )

            job_id = (
                datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                + f"-{index + 1}"
            )

            job = {
                "job_id": job_id,
                "created_at": now_iso(),
                "status": "queued",

                "trend": trend,

                "concept": concept,

                "title": concept.get(
                    "title",
                    trend.get("trend", "AI Dance"),
                ),

                "visual_prompt": concept.get(
                    "visual_prompt",
                    "",
                ),

                "negative_prompt": concept.get(
                    "negative_prompt",
                    "",
                ),

                "duration": concept.get(
                    "duration",
                    settings.get("video_duration", 5),
                ),

                "aspect_ratio": concept.get(
                    "aspect_ratio",
                    "9:16",
                ),
            }

            jobs.append(job)

            log(
                f"Concept {len(jobs)}/{MAX_CONCEPTS}: "
                f"{job['title']}"
            )

        except Exception as e:
            log(
                f"Concept generation failed for "
                f"{trend.get('trend', 'unknown')}: {e}"
            )

            # Stop if Gemini starts rate limiting us
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                log("Gemini rate limit reached — stopping concept generation")
                break

    log(f"Generated {len(jobs)} production jobs")

    # --------------------------------------------------------
    # Nothing generated
    # --------------------------------------------------------

    if not jobs:
        log("No jobs generated")
        return False

    # --------------------------------------------------------
    # Write queue FIRST
    # --------------------------------------------------------

    queue_success = add_jobs_to_queue(jobs)

    if not queue_success:
        log("ERROR: Jobs were generated but NOT written to GitHub")
        return False

    # --------------------------------------------------------
    # Update history AFTER successful queue write
    # --------------------------------------------------------

    history_success = update_history(
        [job["trend"] for job in jobs]
    )

    if not history_success:
        log(
            "WARNING: Queue was updated successfully, "
            "but trend history update failed"
        )

    # --------------------------------------------------------
    # Finished
    # --------------------------------------------------------

    log(
        f"SUCCESS: {len(jobs)} new jobs queued on GitHub"
    )

    log("Trend refresh complete")

    return True


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        success = run()

        if success:
            log("Done")
        else:
            log("FAILED")
            raise SystemExit(1)

    except KeyboardInterrupt:
        log("Interrupted")
        raise SystemExit(1)

    except Exception as e:
        log(f"FATAL ERROR: {e}")
        raise SystemExit(1)
