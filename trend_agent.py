import os
import json
import base64
import re
from datetime import datetime, timezone

import requests
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

# IMPORTANT:
# One Gemini request per workflow run.
MAX_CONCEPTS = 7

# Number of raw trend candidates to ask Gemini to evaluate.
MAX_TRENDS = 30


# ============================================================
# LOGGING
# ============================================================

PREFIX = "[TrendAgent]"


def log(message):
    print(f"{PREFIX} {message}", flush=True)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# FILE HELPERS
# ============================================================

def load_json_file(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        log(f"Could not read {path}: {e}")
        return default


def save_json_file(path, data):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# GITHUB API
# ============================================================

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_get(path):
    """
    Read a JSON file from the GitHub repository.

    Returns:
        {
            "data": parsed_json,
            "sha": github_file_sha
        }

    or None if the file does not exist / cannot be read.
    """

    if not GITHUB_TOKEN:
        log("ERROR: GITHUB_TOKEN is missing")
        return None

    if not GITHUB_REPO:
        log("ERROR: GITHUB_REPO is missing")
        return None

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

    try:
        r = requests.get(
            url,
            headers=github_headers(),
            timeout=30
        )

        if r.status_code == 200:
            body = r.json()

            content = base64.b64decode(
                body["content"]
            ).decode("utf-8")

            return {
                "data": json.loads(content),
                "sha": body["sha"]
            }

        if r.status_code == 404:
            log(f"GitHub file not found: {path}")
            return None

        log(f"GitHub GET failed: {path}")
        log(f"HTTP {r.status_code}")
        log(f"Response: {r.text}")

        return None

    except Exception as e:
        log(f"GitHub GET exception for {path}: {e}")
        return None


def github_put(path, data, sha=None, message="update"):
    """
    Create or update a JSON file in GitHub.
    """

    if not GITHUB_TOKEN:
        log("ERROR: GITHUB_TOKEN is missing")
        return False

    if not GITHUB_REPO:
        log("ERROR: GITHUB_REPO is missing")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

    content = base64.b64encode(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        ).encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": message,
        "content": content
    }

    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(
            url,
            headers=github_headers(),
            json=payload,
            timeout=30
        )

        if r.status_code in (200, 201):
            log(f"GitHub update successful: {path}")
            return True

        log(f"GitHub update FAILED: {path}")
        log(f"GitHub status: {r.status_code}")
        log(f"GitHub response: {r.text}")

        return False

    except Exception as e:
        log(f"GitHub PUT exception for {path}: {e}")
        return False


# ============================================================
# SETTINGS
# ============================================================

def load_settings():
    default_settings = {
        "max_jobs_per_run": MAX_CONCEPTS,
        "min_trend_score": 5
    }

    settings = load_json_file(
        SETTINGS_PATH,
        default_settings
    )

    if not isinstance(settings, dict):
        return default_settings

    return settings


# ============================================================
# TREND DISCOVERY
# ============================================================

def get_candidate_trends():
    """
    Collect trend candidates.

    This intentionally does NOT call Gemini.

    Gemini is called only once later, after we have the
    candidate list.
    """

    candidates = [
        "Indian wedding dance",
        "bride vs groom dance battle",
        "sangeet dance",
        "Garba fusion",
        "modern Garba",
        "Kathak fusion",
        "Bollywood dance",
        "classical Indian dance fusion",
        "Indian festival dance",
        "Navratri dance",
        "Dandiya dance",
        "viral Indian dance",
        "couple dance",
        "group dance",
        "wedding crew battle",
        "traditional dance modern remix",
        "Indian fashion dance",
        "cinematic Indian dance",
        "royal Indian dance",
        "street Indian dance",
        "high energy Bollywood dance",
        "female solo Indian dance",
        "male solo Indian dance",
        "dance transition",
        "dance challenge",
        "festival performance",
        "bridal dance",
        "groom dance",
        "sangeet performance",
        "Indian music dance"
    ]

    return candidates[:MAX_TRENDS]


# ============================================================
# HISTORY
# ============================================================

def load_history():
    history = load_json_file(
        HISTORY_PATH,
        []
    )

    if isinstance(history, dict):
        history = history.get("history", [])

    if not isinstance(history, list):
        history = []

    return history


def history_names(history):
    names = set()

    for item in history:
        if isinstance(item, str):
            names.add(item.lower().strip())

        elif isinstance(item, dict):
            for key in (
                "title",
                "concept",
                "name",
                "trend"
            ):
                value = item.get(key)

                if value:
                    names.add(
                        str(value).lower().strip()
                    )

    return names


# ============================================================
# GEMINI
# ============================================================

def create_gemini_client():
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is missing"
        )

    return genai.Client(
        api_key=GEMINI_API_KEY
    )


def extract_json(text):
    """
    Safely extract JSON from Gemini output.

    Handles:
      {...}
      ```json
      {...}
      ```
    """

    if not text:
        raise ValueError("Gemini returned empty response")

    text = text.strip()

    # Remove markdown fences.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    # Try direct JSON first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find first JSON object/array.
    object_start = text.find("{")
    array_start = text.find("[")

    starts = [
        x for x in (
            object_start,
            array_start
        )
        if x >= 0
    ]

    if not starts:
        raise ValueError(
            "No JSON object or array found in Gemini response"
        )

    start = min(starts)

    object_end = text.rfind("}")
    array_end = text.rfind("]")

    end = max(
        object_end,
        array_end
    )

    if end < start:
        raise ValueError(
            "Could not locate complete JSON in Gemini response"
        )

    return json.loads(
        text[start:end + 1]
    )


def generate_concepts_with_gemini(
    trends,
    history_names_set
):
    """
    ONE Gemini request.

    Gemini receives all candidate trends and returns up to
    MAX_CONCEPTS finished dance-video concepts.
    """

    client = create_gemini_client()

    available_trends = [
        trend
        for trend in trends
        if trend.lower().strip()
        not in history_names_set
    ]

    if not available_trends:
        log("No unused trends available")
        return []

    prompt = f"""
You are the creative director for an automated Indian dance
short-video generation system.

Generate up to {MAX_CONCEPTS} ORIGINAL short-form dance video
concepts from the candidate trends below.

IMPORTANT:
- Return ONLY valid JSON.
- Do not use markdown.
- Do not explain your answer.
- Do not create duplicate concepts.
- Make concepts visually strong for AI video generation.
- Prefer Indian dance, Bollywood, Garba, Kathak, Sangeet,
  wedding and festival-inspired ideas.
- Each concept must be feasible as a short AI-generated video.
- Avoid copyrighted characters and direct imitation of named
  living creators.
- Keep each concept concise.

Candidate trends:
{json.dumps(available_trends, ensure_ascii=False)}

Return exactly this JSON structure:

{{
  "concepts": [
    {{
      "title": "Short memorable title",
      "trend": "Source trend",
      "description": "One sentence describing the video",
      "dance_style": "Dance style",
      "visual_style": "Visual/cinematic style",
      "prompt": "Detailed AI video generation prompt",
      "score": 8
    }}
  ]
}}

The score must be an integer from 1 to 10 representing
viral potential and suitability for a short AI dance video.

Only return concepts with score >= 5.
"""

    log(
        f"Sending ONE Gemini request for "
        f"{len(available_trends)} trends..."
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )

        raw = response.text

        parsed = extract_json(raw)

        if isinstance(parsed, dict):
            concepts = parsed.get(
                "concepts",
                []
            )
        elif isinstance(parsed, list):
            concepts = parsed
        else:
            concepts = []

        if not isinstance(concepts, list):
            concepts = []

        cleaned = []

        for item in concepts:
            if not isinstance(item, dict):
                continue

            title = str(
                item.get("title", "")
            ).strip()

            trend = str(
                item.get("trend", "")
            ).strip()

            description = str(
                item.get("description", "")
            ).strip()

            dance_style = str(
                item.get("dance_style", "")
            ).strip()

            visual_style = str(
                item.get("visual_style", "")
            ).strip()

            prompt_text = str(
                item.get("prompt", "")
            ).strip()

            try:
                score = int(
                    item.get("score", 0)
                )
            except (TypeError, ValueError):
                score = 0

            if not title:
                continue

            if not prompt_text:
                continue

            if score < 5:
                continue

            cleaned.append({
                "title": title,
                "trend": trend,
                "description": description,
                "dance_style": dance_style,
                "visual_style": visual_style,
                "prompt": prompt_text,
                "score": score
            })

        # Highest scoring concepts first.
        cleaned.sort(
            key=lambda x: x.get("score", 0),
            reverse=True
        )

        # Remove duplicate titles.
        unique = []
        seen_titles = set()

        for concept in cleaned:
            key = concept["title"].lower().strip()

            if key in seen_titles:
                continue

            seen_titles.add(key)
            unique.append(concept)

        unique = unique[:MAX_CONCEPTS]

        log(
            f"Gemini returned {len(unique)} usable concepts"
        )

        for concept in unique:
            log(
                f"✅ Concept: "
                f"{concept['title']} "
                f"(score {concept['score']})"
            )

        return unique

    except Exception as e:
        error_text = str(e)

        if (
            "429" in error_text
            or "RESOURCE_EXHAUSTED" in error_text
        ):
            log(
                "Gemini quota exceeded. "
                "No additional Gemini calls will be made."
            )

        else:
            log(
                f"Gemini concept generation failed: {e}"
            )

        return []


# ============================================================
# QUEUE
# ============================================================

def build_jobs(concepts):
    jobs = []

    timestamp = utc_now()

    for index, concept in enumerate(concepts):
        job_id = (
            datetime.now(timezone.utc)
            .strftime("%Y%m%d%H%M%S")
            + f"-{index + 1}"
        )

        job = {
            "id": job_id,
            "title": concept.get("title", ""),
            "trend": concept.get("trend", ""),
            "description": concept.get(
                "description",
                ""
            ),
            "dance_style": concept.get(
                "dance_style",
                ""
            ),
            "visual_style": concept.get(
                "visual_style",
                ""
            ),
            "prompt": concept.get(
                "prompt",
                ""
            ),
            "score": concept.get(
                "score",
                0
            ),
            "status": "queued",
            "created_at": timestamp
        }

        jobs.append(job)

    return jobs


def add_jobs_to_queue(jobs):
    if not jobs:
        log("No jobs to add to queue")
        return False

    current = github_get(
        QUEUE_PATH
    )

    if current is None:
        log(
            f"ERROR: Could not read "
            f"{QUEUE_PATH} from GitHub"
        )
        return False

    queue_data = current["data"]
    sha = current["sha"]

    if not isinstance(queue_data, dict):
        queue_data = {}

    queue_data.setdefault(
        "queue",
        []
    )

    queue_data.setdefault(
        "completed",
        []
    )

    queue_data.setdefault(
        "failed",
        []
    )

    queue_data.setdefault(
        "metadata",
        {}
    )

    if not isinstance(
        queue_data["queue"],
        list
    ):
        queue_data["queue"] = []

    existing_ids = {
        str(item.get("id"))
        for item in queue_data["queue"]
        if isinstance(item, dict)
    }

    added = 0

    for job in jobs:
        if job["id"] in existing_ids:
            continue

        queue_data["queue"].append(job)
        existing_ids.add(job["id"])
        added += 1

    queue_data["metadata"][
        "last_updated"
    ] = utc_now()

    queue_data["metadata"][
        "total_generated"
    ] = (
        len(queue_data.get("completed", []))
        + len(queue_data.get("queue", []))
    )

    success = github_put(
        QUEUE_PATH,
        queue_data,
        sha=sha,
        message=f"Add {added} dance video job(s)"
    )

    if success:
        log(
            f"✅ Added {added} job(s) to GitHub queue"
        )
        return True

    log("ERROR: failed to update queue")
    return False


# ============================================================
# HISTORY UPDATE
# ============================================================

def update_history(concepts):
    if not concepts:
        return True

    current = github_get(
        HISTORY_PATH
    )

    if current is None:
        # File does not exist yet.
        history = []
        sha = None
    else:
        history = current["data"]
        sha = current["sha"]

        if isinstance(history, dict):
            history = history.get(
                "history",
                []
            )

        if not isinstance(history, list):
            history = []

    timestamp = utc_now()

    for concept in concepts:
        history.append({
            "title": concept.get(
                "title",
                ""
            ),
            "trend": concept.get(
                "trend",
                ""
            ),
            "created_at": timestamp
        })

    # Keep history reasonably sized.
    history = history[-500:]

    return github_put(
        HISTORY_PATH,
        history,
        sha=sha,
        message="Update trend history"
    )


# ============================================================
# MAIN
# ============================================================

def run():
    log("Starting trend discovery...")

    # --------------------------------------------------------
    # Validate configuration
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

    # --------------------------------------------------------
    # Load history
    # --------------------------------------------------------

    history = load_history()
    history_names_set = history_names(history)

    log(
        f"Loaded {len(history_names_set)} historical trends"
    )

    # --------------------------------------------------------
    # Discover candidate trends
    # --------------------------------------------------------

    trends = get_candidate_trends()

    log(
        f"Found {len(trends)} raw trends"
    )

    unused = [
        trend
        for trend in trends
        if trend.lower().strip()
        not in history_names_set
    ]

    log(
        f"{len(unused)} unused trends after history filter"
    )

    if not unused:
        log("No new trends available")
        return True

    # --------------------------------------------------------
    # ONE Gemini request
    # --------------------------------------------------------

    concepts = generate_concepts_with_gemini(
        unused,
        history_names_set
    )

    if not concepts:
        log(
            "No concepts generated."
        )
        return False

    # --------------------------------------------------------
    # Build jobs
    # --------------------------------------------------------

    jobs = build_jobs(
        concepts
    )

    log(
        f"Built {len(jobs)} dance video job(s)"
    )

    # --------------------------------------------------------
    # Update queue FIRST
    # --------------------------------------------------------

    queue_success = add_jobs_to_queue(
        jobs
    )

    if not queue_success:
        log(
            "ERROR: queue update failed"
        )
        return False

    # --------------------------------------------------------
    # Update history
    # --------------------------------------------------------

    history_success = update_history(
        concepts
    )

    if not history_success:
        log(
            "WARNING: history update failed"
        )

    # --------------------------------------------------------
    # Done
    # --------------------------------------------------------

    log(
        f"Done. {len(jobs)} new jobs queued"
    )

    return True


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    success = run()

    if not success:
        log("FAILED")
        raise SystemExit(1)

    log("SUCCESS")
