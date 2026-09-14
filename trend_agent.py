"""
Trend Agent
Discovers Hindi trends via Google News RSS, scores them with Gemini,
and generates unique fictional adult dance video concepts.
"""

import base64
import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any

import feedparser
from google import genai
import requests


# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

SETTINGS_PATH = "config/settings.json"
QUEUE_PATH = "data/dance_queue.json"
HISTORY_PATH = "data/trend_history.json"

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=bollywood+dance&hl=hi&gl=IN&ceid=IN:hi",
    "https://news.google.com/rss/search?q=hindi+trending&hl=hi&gl=IN&ceid=IN:hi",
    "https://news.google.com/rss/search?q=india+viral&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=bollywood+song+2024&hl=hi&gl=IN&ceid=IN:hi",
    "https://news.google.com/rss/search?q=hindi+music+trending&hl=hi&gl=IN&ceid=IN:hi",
]

DANCE_STYLES = [
    "Bharatnatyam fusion",
    "Kathak contemporary",
    "Bollywood jazz",
    "Lavani fusion",
    "Garba modern",
    "Bhangra pop",
    "Classical contemporary",
    "Item number style",
    "Folk fusion",
    "Sufi dance",
]

SETTINGS: dict[str, Any] = {}

# Current Gemini model
GEMINI_MODEL = "gemini-3.6-flash"


# ── Gemini ───────────────────────────────────────────────────────────────────

def init_gemini():
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    return genai.Client(api_key=GEMINI_API_KEY)


def gemini_text(client, prompt: str) -> str:
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    text = getattr(response, "text", None)

    if not text:
        raise RuntimeError("Gemini returned an empty response")

    return text.strip()


# ── Local helpers ────────────────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def github_get(path: str) -> dict | None:
    """Read a file from GitHub via API."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    r = requests.get(url, headers=headers, timeout=15)

    if r.status_code == 200:
        content = base64.b64decode(r.json()["content"]).decode()

        return {
            "data": json.loads(content),
            "sha": r.json()["sha"],
        }

    return None


def github_put(
    path: str,
    data: dict,
    sha: str | None = None,
    message: str = "update",
) -> bool:
    """Write a file to GitHub via API with detailed error reporting."""

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    content = base64.b64encode(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ).encode()
    ).decode()

    payload: dict = {
        "message": message,
        "content": content,
    }

    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        if r.status_code in (200, 201):
            print(
                f"[TrendAgent] GitHub update successful: {path}"
            )
            return True

        print(
            f"[TrendAgent] GitHub update FAILED: "
            f"HTTP {r.status_code}"
        )
        print(
            f"[TrendAgent] GitHub response: {r.text}"
        )

        return False

    except Exception as e:
        print(
            f"[TrendAgent] GitHub request error: {e}"
        )
        return False
    path: str,
    data: dict,
    sha: str | None = None,
    message: str = "update",
) -> bool:
    """Write a file to GitHub via API."""

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    content = base64.b64encode(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ).encode()
    ).decode()

    payload: dict = {
        "message": message,
        "content": content,
    }

    if sha:
        payload["sha"] = sha

    r = requests.put(
        url,
        headers=headers,
        json=payload,
        timeout=15,
    )

    return r.status_code in (200, 201)


# ── RSS trend discovery ──────────────────────────────────────────────────────

def fetch_raw_trends() -> list[str]:
    """Pull titles from Google News RSS feeds."""

    trends: list[str] = []

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)

            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()

                if title and len(title) > 5:
                    title = re.sub(
                        r"\s+-\s+\S.*$",
                        "",
                        title,
                    ).strip()

                    trends.append(title)

        except Exception as e:
            print(
                f"[TrendAgent] RSS error for {url}: {e}"
            )

        time.sleep(0.5)

    # Deduplicate
    seen: set[str] = set()
    unique: list[str] = []

    for trend in trends:
        key = trend.lower()

        if key not in seen:
            seen.add(key)
            unique.append(trend)

    return unique


# ── Gemini scoring + concept generation ──────────────────────────────────────

def score_and_filter_trends(
    client,
    trends: list[str],
    used: list[str],
) -> list[dict]:
    """Ask Gemini to score trends for dance-video suitability."""

    trend_list = "\n".join(
        f"- {t}" for t in trends
    )

    used_list = (
        ", ".join(used[-50:])
        if used
        else "none"
    )

    prompt = f"""
You are a Hindi social media video analyst.

Score each trend below from 1-10 for suitability as a
Bollywood/Indian dance short video concept.

Criteria:
- cultural relevance
- visual dance potential
- audience engagement
- uniqueness

Previously used trends:
{used_list}

Trends:
{trend_list}

Return ONLY valid JSON array:

[
  {{
    "trend": "trend text",
    "score": 8,
    "reason": "brief reason"
  }}
]

Only include trends with score >= 5.
Sort by score descending.
"""

    try:
        text = gemini_text(client, prompt)

        match = re.search(
            r"\[.*\]",
            text,
            re.DOTALL,
        )

        if match:
            scored = json.loads(match.group())

            return [
                s
                for s in scored
                if s.get("score", 0) >= 5
            ]

    except Exception as e:
        print(
            f"[TrendAgent] Gemini scoring error: {e}"
        )

    return []


def generate_dance_concept(
    client,
    trend: str,
    used_concepts: list[str],
) -> dict | None:
    """Generate a unique fictional adult dance video concept."""

    style = DANCE_STYLES[
        hash(trend) % len(DANCE_STYLES)
    ]

    used_str = (
        "; ".join(used_concepts[-30:])
        if used_concepts
        else "none"
    )

    prompt = f"""
Create a unique Bollywood/Indian dance video concept
for this trend:

"{trend}"

Dance style:
{style}

Previously used concepts:
{used_str}

Requirements:
- Fictional characters only
- Visually rich, dynamic dance scene
- 9:16 vertical video format
- Suitable for YouTube Shorts / Instagram Reels

Return ONLY valid JSON:

{{
  "concept_title": "short catchy title",
  "dancer_description": "detailed appearance description of fictional dancer",
  "setting": "detailed location/background description",
  "dance_style": "{style}",
  "mood": "energetic/playful/etc",
  "comfyui_prompt": "detailed text-to-video prompt for Wan2.1 model, 80-120 words, cinematic",
  "negative_prompt": "blurry, low quality, static, ugly, deformed",
  "trend_reference": "{trend}"
}}
"""

    try:
        text = gemini_text(client, prompt)

        match = re.search(
            r"\{.*\}",
            text,
            re.DOTALL,
        )

        if match:
            concept = json.loads(match.group())

            concept["concept_id"] = str(
                uuid.uuid4()
            )[:8]

            concept["generated_at"] = (
                datetime.utcnow().isoformat()
            )

            return concept

    except Exception as e:
        print(
            f"[TrendAgent] Gemini concept error "
            f"for '{trend}': {e}"
        )

    return None


# ── Queue management ─────────────────────────────────────────────────────────

def add_jobs_to_queue(
    new_jobs: list[dict],
) -> None:
    """Add new jobs to the GitHub-hosted queue."""

    result = github_get(QUEUE_PATH)

    if result:
        queue_data = result["data"]
        sha = result["sha"]

    else:
        queue_data = {
            "queue": [],
            "completed": [],
            "failed": [],
            "metadata": {
                "created_at": datetime.utcnow().isoformat(),
                "last_updated": "",
                "total_generated": 0,
                "total_failed": 0,
            },
        }

        sha = None

    existing_ids = {
        j["job_id"]
        for j in queue_data.get("queue", [])
    }

    added = 0

    for job in new_jobs:
        if job["job_id"] not in existing_ids:
            queue_data["queue"].append(job)
            added += 1

    queue_data["metadata"]["last_updated"] = (
        datetime.utcnow().isoformat()
    )

    success = github_put(
        QUEUE_PATH,
        queue_data,
        sha,
        f"Add {added} new dance jobs",
    )

    if success:
        print(
            f"[TrendAgent] Added {added} jobs to queue"
        )
    else:
        print(
            "[TrendAgent] ERROR: failed to update queue"
        )


def build_job(concept: dict) -> dict:
    now = datetime.utcnow().isoformat()

    return {
        "job_id": str(uuid.uuid4()),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "retries": 0,
        "concept": concept,
        "output_path": None,
        "error": None,
    }


# ── Main entrypoint ──────────────────────────────────────────────────────────

def run():
    global SETTINGS

    SETTINGS = load_settings()

    max_trends = SETTINGS.get(
        "trends",
        {},
    ).get(
        "max_trends_per_run",
        20,
    )

    target_videos = SETTINGS.get(
        "production",
        {},
    ).get(
        "target_videos",
        10,
    )

    print("[TrendAgent] Starting trend discovery...")

    client = init_gemini()

    # Load history
    hist_result = github_get(HISTORY_PATH)

    if hist_result:
        history = hist_result["data"]
        hist_sha = hist_result["sha"]

    else:
        history = {
            "used_trends": [],
            "used_concepts": [],
            "last_refresh": "",
            "refresh_count": 0,
        }

        hist_sha = None

    # Fetch raw trends
    raw_trends = fetch_raw_trends()

    print(
        f"[TrendAgent] Found {len(raw_trends)} raw trends"
    )

    # Filter already-used
    unused = [
        t
        for t in raw_trends
        if t not in history.get(
            "used_trends",
            [],
        )
    ]

    print(
        f"[TrendAgent] {len(unused)} unused trends "
        "after history filter"
    )

    # Score with Gemini
    scored = score_and_filter_trends(
        client,
        unused[:max_trends],
        history.get(
            "used_trends",
            [],
        ),
    )

    print(
        f"[TrendAgent] {len(scored)} trends scored ≥5"
    )

    # Generate concepts
    jobs: list[dict] = []

    used_concepts = history.get(
        "used_concepts",
        [],
    )

    for item in scored[:target_videos]:

        trend = item["trend"]

        concept = generate_dance_concept(
            client,
            trend,
            used_concepts,
        )

        if concept:
            job = build_job(concept)

            jobs.append(job)

            used_concepts.append(
                concept.get(
                    "concept_title",
                    trend,
                )
            )

            history["used_trends"].append(
                trend
            )

            print(
                f"[TrendAgent] ✅ Concept: "
                f"{concept.get('concept_title', trend)}"
            )

        time.sleep(1)

    # Update history
    history["last_refresh"] = (
        datetime.utcnow().isoformat()
    )

    history["refresh_count"] = (
        history.get(
            "refresh_count",
            0,
        ) + 1
    )

    history["used_concepts"] = used_concepts

    history["used_trends"] = (
        history["used_trends"][-200:]
    )

    history["used_concepts"] = (
        history["used_concepts"][-200:]
    )

    github_put(
        HISTORY_PATH,
        history,
        hist_sha,
        "Update trend history",
    )

    # Push jobs to queue
    if jobs:
        add_jobs_to_queue(jobs)

        print(
            f"[TrendAgent] Done. "
            f"{len(jobs)} new jobs queued."
        )

    else:
        print(
            "[TrendAgent] No new jobs generated."
        )


if __name__ == "__main__":
    run()
