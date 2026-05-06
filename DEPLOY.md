# Deployment Guide — Job Intelligence System

This guide covers two paths:

- **Path A — Streamlit Community Cloud** (recommended for sharing with friends/family)
  Free hosting, no server required, shareable URL in minutes.
- **Path B — Run locally** (for your own machine only)

---

## Path A — Streamlit Community Cloud

### What you need
- A GitHub account (free)
- An Anthropic API key (https://console.anthropic.com)
- This codebase

### Step 1 — Push to GitHub

Create a new **private** repository on GitHub (private keeps your code safe).

```bash
cd D:\ai-projects\job_intelligence

git init
git add .
git commit -m "Initial commit — Job Intelligence System"

# Create a repo on github.com first, then:
git remote add origin https://github.com/YOUR_USERNAME/job-intelligence.git
git branch -M main
git push -u origin main
```

> **Important:** Make sure `.env` is in `.gitignore` so your API key is never pushed.
> The `.gitignore` should include: `.env`, `outputs/`, `logs/`, `.cache/`, `saved_inputs/`, `__pycache__/`

### Step 2 — Create a .gitignore

If you don't have one already:

```
.env
outputs/
logs/
.cache/
saved_inputs/
__pycache__/
*.pyc
venv/
node_modules/
.streamlit/secrets.toml
```

### Step 3 — Deploy on Streamlit Community Cloud

1. Go to **https://share.streamlit.io** and sign in with GitHub
2. Click **"New app"**
3. Set:
   - **Repository:** your-username/job-intelligence
   - **Branch:** main
   - **Main file path:** streamlit_app.py
4. Click **"Advanced settings"** before deploying
5. Under **Secrets**, paste:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-YOUR_KEY_HERE"
   TAVILY_API_KEY = "tvly-YOUR_KEY_HERE"
   DAILY_COST_CAP_USD = "5.00"
   ```
6. Click **"Deploy"**

Streamlit Cloud will install all packages from `requirements.txt` and launch the app.
First deploy takes ~3–5 minutes. After that, it stays running.

### Step 4 — Share the URL

Once deployed you'll get a URL like:
```
https://your-username-job-intelligence-streamlit-app-xxxx.streamlit.app
```

Share this URL with friends and family. They open it in a browser — no installation, no setup.

---

## Important limits to communicate to users

| Limit | Detail |
|---|---|
| Concurrent users | ~2–3 simultaneous runs at Anthropic Tier 1. If a run fails with "rate limit", wait 2 minutes and retry. |
| Run time | 5–10 minutes per analysis. The browser tab must stay open. |
| File storage | Outputs (CV, PDF) exist only for the current session on Streamlit Cloud. Download them before closing the tab. |
| Cost | ~$0.30 per run (Sonnet). Set `DAILY_COST_CAP_USD=5.00` in Secrets to cap daily spend at $5. |
| JD similarity | The "Similar JDs" tab requires PyTorch which is too large for Streamlit Cloud. It will show "No similar JDs yet" — that's expected. |

---

## Path B — Run locally

For running on your own Windows machine.

### Prerequisites

- Python 3.10 or 3.11
- pip

### Setup

```bash
cd D:\ai-projects\job_intelligence

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# (Optional) Enable JD similarity search
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install sentence-transformers

# Set up environment
copy .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY and TAVILY_API_KEY
```

### Run the Streamlit app

```bash
venv\Scripts\activate
streamlit run streamlit_app.py
```

Opens at http://localhost:8501

### Run via CLI

```bash
venv\Scripts\activate
python main.py
```

---

## Upgrading Anthropic API Tier

If you get frequent 429 rate limit errors, you've hit the Tier 1 throughput limit.

To upgrade to Tier 2 (~$500 cumulative spend unlocks ~15–20 concurrent users):
- Add credit at https://console.anthropic.com → Billing
- Tier 2 is unlocked automatically once cumulative spend crosses $500

---

## Cost estimates

| Model | Per run | 10 runs/day | 100 runs/day |
|---|---|---|---|
| Haiku | ~$0.05 | ~$0.50 | ~$5 |
| Sonnet (default) | ~$0.30 | ~$3 | ~$30 |
| Opus | ~$1.20 | ~$12 | ~$120 |

Set `DAILY_COST_CAP_USD` in Secrets to hard-cap daily spend. The pipeline will show an error and abort if the cap is reached, rather than silently spending more.

---

## Monitoring usage

The sidebar in the app shows:
- Active runs and elapsed time
- Today's run count and estimated spend
- Average run duration
- Quality distribution (green / amber / red)

This data comes from the system log (`logs/run_log_YYYY-MM-DD.jsonl`). On Streamlit Cloud, logs reset on each restart but persist for the current session.

