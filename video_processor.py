"""
video_processor.py
Converts generated videos to 9:16 vertical Shorts format using FFmpeg.
"""

import os
import subprocess
from pathlib import Path


TARGET_WIDTH = 576
TARGET_HEIGHT = 1024
TARGET_FPS = 30
OUTPUT_DIR = os.environ.get("SHORTS_OUTPUT_DIR", "/kaggle/working/shorts_output")


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Return (width, height) of a video using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            w, h = result.stdout.strip().split(",")
            return int(w), int(h)
    except Exception:
        pass
    return 0, 0


def convert_to_vertical(
    input_path: str,
    output_path: str | None = None,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
) -> str | None:
    """
    Convert any video to 9:16 vertical format.
    Pads/crops if needed. Returns output path or None on failure.
    """
    input_path = str(input_path)
    if not Path(input_path).exists():
        print(f"[VideoProcessor] ❌ Input not found: {input_path}")
        return None

    if output_path is None:
        ensure_dir(OUTPUT_DIR)
        stem = Path(input_path).stem
        output_path = str(Path(OUTPUT_DIR) / f"{stem}_9x16.mp4")

    # FFmpeg filter: scale to fill 9:16, then crop/pad to exact dimensions
    # Strategy: scale to cover, then crop centre
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-r", str(TARGET_FPS),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    print(f"[VideoProcessor] Converting {Path(input_path).name} → 9:16...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and Path(output_path).exists():
            size_mb = Path(output_path).stat().st_size / (1024 * 1024)
            print(f"[VideoProcessor] ✅ Done: {Path(output_path).name} ({size_mb:.1f}MB)")
            return output_path
        else:
            print(f"[VideoProcessor] ❌ FFmpeg failed:\n{result.stderr[-500:]}")
            return None
    except subprocess.TimeoutExpired:
        print("[VideoProcessor] ❌ FFmpeg timeout after 5 minutes")
        return None
    except FileNotFoundError:
        print("[VideoProcessor] ❌ FFmpeg not found. Install with: apt-get install ffmpeg")
        return None


def add_watermark(input_path: str, watermark_text: str = "AI Dance") -> str | None:
    """Add a subtle text watermark to the video."""
    output_path = input_path.replace(".mp4", "_wm.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", (
            f"drawtext=text='{watermark_text}':fontcolor=white@0.3:"
            f"fontsize=24:x=10:y=H-th-10"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            return output_path
    except Exception as e:
        print(f"[VideoProcessor] Watermark error: {e}")
    return None


def process_video(raw_path: str, job_id: str) -> str | None:
    """
    Full processing pipeline:
    1. Convert to 9:16
    2. Quality-ready output
    Returns final output path or None.
    """
    output_path = str(Path(OUTPUT_DIR) / f"{job_id}_final.mp4")
    return convert_to_vertical(raw_path, output_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = convert_to_vertical(sys.argv[1])
        print("Output:", result)
    else:
        print("Usage: python video_processor.py <input_video>")
