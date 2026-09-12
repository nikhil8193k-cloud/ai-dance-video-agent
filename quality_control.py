"""
quality_control.py
Validates generated videos: duration, size, resolution, aspect ratio.
"""

import json
import os
import subprocess
from pathlib import Path


MIN_DURATION_SECONDS = 3
MIN_FILE_SIZE_KB = 500
EXPECTED_ASPECT_RATIO = 9 / 16  # width/height for vertical


def get_video_info(video_path: str) -> dict | None:
    """Use ffprobe to extract video metadata."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[QC] ffprobe error: {e}")
    return None


def check_video(video_path: str) -> tuple[bool, list[str]]:
    """
    Run quality checks on a video file.
    Returns (passed: bool, issues: list[str])
    """
    path = Path(video_path)
    issues: list[str] = []

    # File existence
    if not path.exists():
        return False, ["File does not exist"]

    # File size
    size_kb = path.stat().st_size / 1024
    if size_kb < MIN_FILE_SIZE_KB:
        issues.append(f"File too small: {size_kb:.1f}KB < {MIN_FILE_SIZE_KB}KB minimum")

    # ffprobe metadata
    info = get_video_info(video_path)
    if not info:
        issues.append("Could not read video metadata (ffprobe failed)")
        return False, issues

    # Find video stream
    video_stream = None
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if not video_stream:
        issues.append("No video stream found")
        return False, issues

    # Duration
    duration = float(info.get("format", {}).get("duration", 0))
    if duration < MIN_DURATION_SECONDS:
        issues.append(f"Too short: {duration:.1f}s < {MIN_DURATION_SECONDS}s minimum")

    # Resolution
    width = video_stream.get("width", 0)
    height = video_stream.get("height", 0)

    if width == 0 or height == 0:
        issues.append("Could not determine resolution")
    else:
        # Check aspect ratio (allow ±10% tolerance)
        actual_ratio = width / height
        if abs(actual_ratio - EXPECTED_ASPECT_RATIO) > 0.1:
            issues.append(
                f"Wrong aspect ratio: {width}x{height} ({actual_ratio:.3f}) "
                f"expected ~{EXPECTED_ASPECT_RATIO:.3f} (9:16)"
            )

        # Check minimum resolution
        if height < 720:
            issues.append(f"Resolution too low: {width}x{height} (minimum 720p height)")

    passed = len(issues) == 0
    return passed, issues


def run_qc(video_path: str) -> dict:
    """Full QC report for a video."""
    passed, issues = check_video(video_path)
    info = get_video_info(video_path) or {}
    video_stream = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}
    )

    report = {
        "path": video_path,
        "passed": passed,
        "issues": issues,
        "duration_seconds": float(info.get("format", {}).get("duration", 0)),
        "file_size_kb": Path(video_path).stat().st_size / 1024 if Path(video_path).exists() else 0,
        "width": video_stream.get("width", 0),
        "height": video_stream.get("height", 0),
        "codec": video_stream.get("codec_name", "unknown"),
        "fps": eval(video_stream.get("r_frame_rate", "0/1")),
    }

    if passed:
        print(f"[QC] ✅ PASSED: {Path(video_path).name}")
    else:
        print(f"[QC] ❌ FAILED: {Path(video_path).name}")
        for issue in issues:
            print(f"[QC]    • {issue}")

    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        report = run_qc(sys.argv[1])
        print(json.dumps(report, indent=2))
    else:
        print("Usage: python quality_control.py <video_path>")
