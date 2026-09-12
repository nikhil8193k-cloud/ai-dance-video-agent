# 🎬 AI Dance Video Agent

Fully automated pipeline that discovers Hindi/Bollywood trends, generates
unique fictional dance videos using Wan 2.1 + ComfyUI on Kaggle GPU, converts
them to 9:16 Shorts format, and delivers them to Telegram.

---

## 🗺️ Architecture

```
GitHub Actions (every 2h)
    └─► trend_agent.py       ← Google News RSS → Gemini scoring → dance concepts
            └─► dance_queue.json (GitHub)
                    └─► production_worker.py (Kaggle GPU)
                            ├─► ComfyUI + Wan 2.1  (generate raw video)
                            ├─► FFmpeg             (9:16 conversion)
                            ├─► quality_control.py (QC check)
                            └─► Telegram Bot       (deliver video)
```

---

## 📁 File Structure

```
ai-dance-video-agent/
│
├── .github/
│   └── workflows/
│       └── trend_refresh.yml       # Auto-runs every 2 hours
│
├── config/
│   └── settings.json               # All production settings
│
├── data/
│   ├── dance_queue.json            # Persistent job queue
│   ├── trend_history.json          # Prevents duplicate trends
│   └── worker_state.json           # Kaggle worker heartbeat
│
├── src/
│   ├── trend_agent.py              # RSS → Gemini → concepts → queue
│   ├── orchestrator.py             # Queue health, stuck job recovery
│   ├── worker.py                   # GitHub-side queue claim helpers
│   ├── telegram.py                 # Telegram delivery
│   ├── quality_control.py          # FFprobe-based QC
│   └── video_processor.py          # FFmpeg 9:16 conversion
│
├── requirements.txt
├── README.md
│
# ── Kaggle files (upload manually) ──────────────────────────────
├── wan_api.json                    # ComfyUI Wan2.1 workflow
├── production_worker.py            # Main Kaggle worker script
├── production_state.json           # Session progress (auto-created)
└── agent_control.json              # Pause / emergency stop
```

---

## 🚀 Setup Guide

### 1. GitHub Repository

1. Create a new repo (e.g. `your-username/ai-dance-video-agent`)
2. Upload all files maintaining the directory structure above
3. Add **GitHub Secrets** (Settings → Secrets → Actions):
   - `GEMINI_API_KEY` — from [Google AI Studio](https://aistudio.google.com)
   - `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `TELEGRAM_CHAT_ID` — your chat/channel ID

> `GITHUB_TOKEN` is automatically provided by Actions — no setup needed.

### 2. Kaggle Setup

1. Create a **Kaggle notebook** (GPU T4 x2 recommended)
2. Add **Kaggle Secrets**:
   - `GITHUB_TOKEN` — Personal Access Token with `repo` scope
   - `GITHUB_REPO` — e.g. `your-username/ai-dance-video-agent`
   - `GEMINI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Install ComfyUI and Wan 2.1 models (see below)
4. Upload `wan_api.json` and `production_worker.py` to `/kaggle/working/`

### 3. ComfyUI + Wan 2.1 Models

Add this cell to your Kaggle notebook to install ComfyUI:

```bash
# Install ComfyUI
!git clone https://github.com/comfyanonymous/ComfyUI /kaggle/working/ComfyUI
!pip install -r /kaggle/working/ComfyUI/requirements.txt -q

# Install ComfyUI-VideoHelperSuite (for VHS_VideoCombine node)
!git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite \
    /kaggle/working/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite
!pip install -r /kaggle/working/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt -q

# Download Wan 2.1 1.3B model
!mkdir -p /kaggle/working/ComfyUI/models/diffusion_models
!wget -q -O /kaggle/working/ComfyUI/models/diffusion_models/wan2.1_t2v_1.3B_bf16.safetensors \
    "https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B/resolve/main/wan2.1_t2v_1.3B_bf16.safetensors"

# Download text encoder
!mkdir -p /kaggle/working/ComfyUI/models/text_encoders
!wget -q -O /kaggle/working/ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors \
    "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"

# Download VAE
!mkdir -p /kaggle/working/ComfyUI/models/vae
!wget -q -O /kaggle/working/ComfyUI/models/vae/wan_2.1_vae.safetensors \
    "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors"
```

Start ComfyUI in the background:

```bash
import subprocess, time
proc = subprocess.Popen(
    ["python", "/kaggle/working/ComfyUI/main.py", "--listen", "0.0.0.0", "--port", "8188"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(15)
print("ComfyUI started")
```

Then run the worker:

```python
exec(open("/kaggle/working/production_worker.py").read())
run()
```

---

## ⚙️ Configuration

Edit `config/settings.json` to tune the system:

| Key | Default | Description |
|-----|---------|-------------|
| `production.target_videos` | `10` | How many videos to generate per run |
| `production.retry_limit` | `3` | Max retries per failed job |
| `production.stuck_job_timeout_minutes` | `30` | When to reclaim stuck jobs |
| `trends.refresh_interval_hours` | `2` | How often GitHub Actions runs |
| `trends.max_trends_per_run` | `20` | Max trends to process per refresh |
| `video.width` / `video.height` | `576` / `1024` | Output resolution (9:16) |

---

## 🎛️ Controls

### Pause the worker
Edit `agent_control.json` on Kaggle:
```json
{ "paused": true, "pause_reason": "Manual pause" }
```

### Emergency stop
```json
{ "emergency_stop": true }
```

### Manually trigger trend refresh
Go to GitHub → Actions → **Trend Refresh** → **Run workflow**

---

## 📊 Monitoring

- **GitHub** → `data/dance_queue.json` — live queue status
- **GitHub** → `data/worker_state.json` — worker heartbeat
- **Telegram** — receives videos + status messages
- **Kaggle** → `production_state.json` — session progress

---

## 🔐 Secrets Reference

| Secret | Where | Description |
|--------|-------|-------------|
| `GEMINI_API_KEY` | GitHub + Kaggle | Google AI Studio key |
| `TELEGRAM_BOT_TOKEN` | GitHub + Kaggle | BotFather token |
| `TELEGRAM_CHAT_ID` | GitHub + Kaggle | Your Telegram chat ID |
| `GITHUB_TOKEN` | Kaggle only | Personal Access Token (repo scope) |
| `GITHUB_REPO` | Kaggle only | e.g. `username/ai-dance-video-agent` |

---

## 🛠️ Troubleshooting

**ComfyUI connection refused**
→ Wait 15–20s after starting, then retry. Check GPU is allocated.

**No trends discovered**
→ Check `GEMINI_API_KEY` is valid. Run `trend_agent.py` manually.

**Videos not reaching Telegram**
→ Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Send `/start` to your bot first.

**Jobs stuck in "processing"**
→ Orchestrator auto-recovers after 30 minutes. Or run `orchestrator.py` manually.

**QC failing**
→ Check FFmpeg is installed (`ffmpeg -version`). Increase `MIN_DURATION_SECONDS` if needed.

---

## 📄 License

MIT — free to use, modify, and distribute.
