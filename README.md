# Podcast Clip Hub

Automated sports media podcast clip finder. Runs daily via GitHub Actions.

## How it works

1. Fetches new episodes from RSS feeds and YouTube channels
2. Transcribes audio with AssemblyAI
3. Finds compelling moments using keyword matching + Claude AI
4. Cuts clips with ffmpeg
5. Uploads to Cloudflare R2
6. Updates `docs/clips.json` which powers the GitHub Pages frontend

## Setup

All credentials are stored as GitHub Secrets:

| Secret | Description |
|--------|-------------|
| `ASSEMBLYAI_API_KEY` | AssemblyAI API key |
| `R2_ACCESS_KEY_ID` | Cloudflare R2 access key |
| `R2_SECRET_ACCESS_KEY` | Cloudflare R2 secret key |
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_PUBLIC_URL` | R2 public bucket URL (pub-xxxx.r2.dev) |
| `ANTHROPIC_API_KEY` | Anthropic API key (for Claude) |

## Adding podcasts

Edit `config.py`:
- Add RSS feeds to `RSS_FEEDS`
- Add YouTube channels to `YOUTUBE_CHANNELS`
- Add keywords to `KEYWORD_TOPICS`

## Manual run

```bash
pip install -r requirements.txt
sudo apt-get install ffmpeg
export ASSEMBLYAI_API_KEY=...
export R2_ACCESS_KEY_ID=...
# (set all secrets as env vars)
python pipeline.py
```

## Frontend

Lives at `docs/index.html` — served via GitHub Pages from the `docs/` folder.
