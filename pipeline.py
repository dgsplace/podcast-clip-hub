#!/usr/bin/env python3
"""
Dan's Podcast Hub — Daily Pipeline
Fetches episodes → transcribes → finds clips → uploads to R2 → writes clips.json
"""

import os, json, time, hashlib, logging, subprocess, tempfile, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import feedparser
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError

from config import (
    ASSEMBLYAI_API_KEY, ANTHROPIC_API_KEY,
    R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID, R2_PUBLIC_URL,
    R2_BUCKET_NAME, LOOKBACK_DAYS, MAX_EPISODES_PER_RUN,
    CLIP_BEFORE_SECS, CLIP_AFTER_SECS, SNIPPET_WORDS,
    CLIPS_JSON, SEEN_EPISODES_FILE, PODCASTS, KEYWORD_TOPICS, AI_CLIP_PROMPT,
    OUTPUT_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-5s  %(message)s")
log = logging.getLogger(__name__)

# ── R2 client ────────────────────────────────────────────────────────────────
def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        region_name="auto",
    )

# ── Helpers ──────────────────────────────────────────────────────────────────
def ep_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

def load_seen() -> set:
    if Path(SEEN_EPISODES_FILE).exists():
        return set(json.loads(Path(SEEN_EPISODES_FILE).read_text()))
    return set()

def save_seen(seen: set):
    Path(SEEN_EPISODES_FILE).write_text(json.dumps(sorted(seen)))

def load_clips() -> list:
    p = Path(CLIPS_JSON)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return []
    return []

def save_clips(clips: list):
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    Path(CLIPS_JSON).write_text(json.dumps(clips, indent=2))

def cutoff_dt() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

# ── Fetch podcast artwork from RSS ──────────────────────────────────────────
def fetch_artwork(feed) -> str:
    """Extract artwork URL from parsed feedparser feed."""
    try:
        if hasattr(feed.feed, 'image') and hasattr(feed.feed.image, 'href'):
            return feed.feed.image.href
        if hasattr(feed.feed, 'itunes_image'):
            img = feed.feed.itunes_image
            if isinstance(img, dict):
                return img.get('href', '')
            return str(img)
    except Exception:
        pass
    return ""

# ── RSS fetching ─────────────────────────────────────────────────────────────
def fetch_rss(podcast: dict, seen: set, cutoff: datetime) -> list:
    feed = feedparser.parse(podcast["url"])
    artwork = fetch_artwork(feed)
    episodes = []
    for entry in feed.entries:
        pub = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if pub and pub < cutoff:
            continue
        audio_url = ""
        for link in getattr(entry, "enclosures", []):
            if "audio" in link.get("type", ""):
                audio_url = link.get("href", "")
                break
        if not audio_url:
            continue
        eid = ep_id(audio_url)
        if eid in seen:
            log.info("    Already processed: %s", entry.get("title", ""))
            continue
        episodes.append({
            "id": eid,
            "podcast": podcast["name"],
            "artwork": artwork,
            "title": entry.get("title", ""),
            "audio_url": audio_url,
            "episode_url": entry.get("link", audio_url),
            "published": pub.isoformat() if pub else "",
        })
    return episodes

# ── YouTube fetching ─────────────────────────────────────────────────────────
def fetch_youtube(podcast: dict, seen: set, cutoff: datetime) -> list:
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--dump-json",
             "--playlist-end", "5", podcast["url"]],
            capture_output=True, text=True, timeout=60
        )
        episodes = []
        for line in result.stdout.strip().splitlines():
            try:
                item = json.loads(line)
            except Exception:
                continue
            upload_date = item.get("upload_date", "")
            if upload_date:
                pub = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    continue
            video_url = f"https://www.youtube.com/watch?v={item['id']}"
            eid = ep_id(video_url)
            if eid in seen:
                continue
            episodes.append({
                "id": eid,
                "podcast": podcast["name"],
                "artwork": item.get("thumbnail", ""),
                "title": item.get("title", ""),
                "audio_url": video_url,
                "episode_url": video_url,
                "published": pub.isoformat() if upload_date else "",
            })
        return episodes
    except Exception as exc:
        log.warning("  YouTube fetch failed: %s", exc)
        return []

# ── Audio download ───────────────────────────────────────────────────────────
def download_audio(episode: dict, tmp_dir: str) -> str | None:
    out_path = os.path.join(tmp_dir, f"{episode['id']}.mp3")
    url = episode["audio_url"]
    try:
        if "youtube.com" in url or "youtu.be" in url:
            subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3",
                 "-o", out_path, url],
                check=True, capture_output=True, timeout=300
            )
        else:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
        return out_path
    except Exception as exc:
        log.warning("  Download failed: %s", exc)
        return None

# ── AssemblyAI transcription (direct REST API) ───────────────────────────────
def transcribe(audio_path: str) -> dict | None:
    log.info("  Transcribing with AssemblyAI...")
    headers = {"authorization": ASSEMBLYAI_API_KEY}

    # 1. Upload audio
    try:
        with open(audio_path, "rb") as f:
            up = requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers=headers,
                data=f,
                timeout=300,
            )
        up.raise_for_status()
        upload_url = up.json()["upload_url"]
    except Exception as exc:
        log.warning("  Upload failed: %s", exc)
        return None

    # 2. Submit transcription job
    try:
        job = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={**headers, "content-type": "application/json"},
            json={
                "audio_url": upload_url,
                "speaker_labels": True,
                "auto_highlights": True,
            },
            timeout=30,
        )
        if not job.ok:
            log.warning("  Transcription submit failed %s: %s", job.status_code, job.text)
            return None
        transcript_id = job.json()["id"]
    except Exception as exc:
        log.warning("  Transcription submit failed: %s", exc)
        return None

    # 3. Poll for completion
    poll_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    for _ in range(240):
        time.sleep(5)
        try:
            status_resp = requests.get(poll_url, headers=headers, timeout=30)
            status_resp.raise_for_status()
            data = status_resp.json()
        except Exception:
            continue
        if data["status"] == "completed":
            log.info("  Transcription complete (%d chars)", len(data.get("text") or ""))
            return data
        if data["status"] == "error":
            log.warning("  Transcription error: %s", data.get("error"))
            return None
    log.warning("  Transcription timed out")
    return None

# ── Keyword clip finder ──────────────────────────────────────────────────────
def find_keyword_clips(transcript: dict, episode: dict) -> list:
    words = transcript.get("words", [])
    if not words:
        return []

    clips = []
    matched_ranges = []

    for keyword in KEYWORD_TOPICS:
        kw_lower = keyword.lower()
        kw_words = kw_lower.split()
        kw_len = len(kw_words)

        for i, word in enumerate(words):
            if i + kw_len > len(words):
                break
            chunk = " ".join(w.get("text", "").lower().strip(".,!?\"'") for w in words[i:i+kw_len])
            if chunk == kw_lower:
                match_start = words[i].get("start", 0) / 1000.0
                match_end   = words[i + kw_len - 1].get("end", 0) / 1000.0

                # Deduplicate overlapping matches
                overlapping = False
                for rs, re in matched_ranges:
                    if not (match_end < rs or match_start > re):
                        overlapping = True
                        break
                if overlapping:
                    continue
                matched_ranges.append((match_start, match_end))

                clip_start = max(0, match_start - CLIP_BEFORE_SECS)
                clip_end   = match_end + CLIP_AFTER_SECS

                # Build transcript snippet (~SNIPPET_WORDS words around match)
                half = SNIPPET_WORDS // 2
                word_times = [(w.get("start", 0) / 1000.0, w.get("text", "")) for w in words]
                match_word_idx = min(range(len(word_times)), key=lambda x: abs(word_times[x][0] - match_start))
                snip_start = max(0, match_word_idx - half)
                snip_end   = min(len(word_times), match_word_idx + half)
                snippet_words = [w[1] for w in word_times[snip_start:snip_end]]
                # Highlight the keyword in the snippet
                snippet_text = " ".join(snippet_words)
                snippet_text = re.sub(
                    re.escape(keyword),
                    f"<mark>{keyword}</mark>",
                    snippet_text,
                    flags=re.IGNORECASE,
                )

                clips.append({
                    "keyword": keyword,
                    "clip_start": clip_start,
                    "clip_end":   clip_end,
                    "transcript_snippet": snippet_text,
                })

    return clips

# ── Claude AI clip finder ────────────────────────────────────────────────────
def find_ai_clips(transcript: dict, episode: dict) -> list:
    if not ANTHROPIC_API_KEY:
        return []
    text = transcript.get("text", "")
    if not text:
        return []
    # Include timestamps in text sent to Claude
    words = transcript.get("words", [])
    timed_text = ""
    for i, w in enumerate(words):
        if i % 50 == 0:
            ts = int(w.get("start", 0) / 1000)
            timed_text += f"[{ts}s] "
        timed_text += w.get("text", "") + " "

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 2000,
                "messages": [{
                    "role": "user",
                    "content": f"{AI_CLIP_PROMPT}\n\nPodcast: {episode['podcast']}\nEpisode: {episode['title']}\n\nTranscript:\n{timed_text[:15000]}"
                }]
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"]
        # Strip markdown fences if present
        content = re.sub(r"```json|```", "", content).strip()
        ai_clips = json.loads(content)
        return ai_clips if isinstance(ai_clips, list) else []
    except Exception as exc:
        log.warning("  Claude AI clip detection failed: %s", exc)
        return []

# ── Cut audio with ffmpeg ────────────────────────────────────────────────────
def cut_audio(src: str, start: float, end: float, out_path: str) -> bool:
    duration = end - start
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-i", src,
             "-t", str(duration), "-c", "copy", out_path],
            check=True, capture_output=True, timeout=120,
        )
        return True
    except Exception as exc:
        log.warning("  ffmpeg cut failed: %s", exc)
        return False

# ── Upload clip to R2 ────────────────────────────────────────────────────────
def upload_to_r2(local_path: str, key: str) -> str | None:
    try:
        r2 = get_r2_client()
        # Generate presigned PUT URL
        presigned = r2.generate_presigned_url(
            "put_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": key, "ContentType": "audio/mpeg"},
            ExpiresIn=3600,
        )
        with open(local_path, "rb") as f:
            resp = requests.put(
                presigned,
                data=f,
                headers={"Content-Type": "audio/mpeg"},
                verify=False,
                timeout=300,
            )
        resp.raise_for_status()
        return f"{R2_PUBLIC_URL}/{key}"
    except Exception as exc:
        log.warning("  R2 upload failed: %s", exc)
        return None

# ── Process one episode ──────────────────────────────────────────────────────
def process_episode(episode: dict, seen: set) -> list:
    log.info("  Processing: %s — %s", episode["podcast"], episode["title"])
    new_clips = []

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = download_audio(episode, tmp)
        if not audio_path:
            seen.add(episode["id"])
            return []

        transcript = transcribe(audio_path)
        if not transcript:
            seen.add(episode["id"])
            return []

        # Find clips via keywords
        kw_clips = find_keyword_clips(transcript, episode)
        log.info("  Keyword clips found: %d", len(kw_clips))

        # Find clips via Claude AI
        ai_clips = find_ai_clips(transcript, episode)
        log.info("  AI clips found: %d", len(ai_clips))

        # Process keyword clips
        for kc in kw_clips:
            clip_id = f"{episode['id']}_{int(kc['clip_start'])}"
            out_path = os.path.join(tmp, f"{clip_id}.mp3")
            if not cut_audio(audio_path, kc["clip_start"], kc["clip_end"], out_path):
                continue
            r2_key = f"clips/{clip_id}.mp3"
            public_url = upload_to_r2(out_path, r2_key)
            if not public_url:
                continue
            new_clips.append({
                "id": clip_id,
                "podcast": episode["podcast"],
                "artwork": episode.get("artwork", ""),
                "episode": episode["title"],
                "episode_url": episode["episode_url"],
                "published": episode["published"],
                "title": f"{kc['keyword']} — {episode['podcast']}",
                "summary": "",
                "topics": [kc["keyword"]],
                "people": [],
                "transcript_snippet": kc["transcript_snippet"],
                "matched_keyword": kc["keyword"],
                "audio_url": public_url,
                "duration": kc["clip_end"] - kc["clip_start"],
                "clip_type": "keyword",
            })

        # Process AI clips
        for ac in ai_clips:
            start = float(ac.get("start_time", 0))
            end   = float(ac.get("end_time", start + 60))
            clip_id = f"{episode['id']}_ai_{int(start)}"
            out_path = os.path.join(tmp, f"{clip_id}.mp3")
            if not cut_audio(audio_path, start, end, out_path):
                continue
            r2_key = f"clips/{clip_id}.mp3"
            public_url = upload_to_r2(out_path, r2_key)
            if not public_url:
                continue
            new_clips.append({
                "id": clip_id,
                "podcast": episode["podcast"],
                "artwork": episode.get("artwork", ""),
                "episode": episode["title"],
                "episode_url": episode["episode_url"],
                "published": episode["published"],
                "title": ac.get("title", episode["title"]),
                "summary": ac.get("summary", ""),
                "topics": ac.get("topics", []),
                "people": ac.get("people", []),
                "transcript_snippet": "",
                "matched_keyword": "",
                "audio_url": public_url,
                "duration": end - start,
                "quality": ac.get("quality", 5),
                "clip_type": "ai",
            })

    seen.add(episode["id"])
    return new_clips

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=== Dan's Podcast Hub Pipeline — %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    seen   = load_seen()
    clips  = load_clips()
    cutoff = cutoff_dt()
    all_episodes = []

    for podcast in PODCASTS:
        log.info("Fetching %s: %s", podcast["type"].upper(), podcast["name"])
        if podcast["type"] == "rss":
            eps = fetch_rss(podcast, seen, cutoff)
        elif podcast["type"] == "youtube":
            eps = fetch_youtube(podcast, seen, cutoff)
        else:
            eps = []
        log.info("  Found %d new episode(s)", len(eps))
        all_episodes.extend(eps)

    all_episodes = all_episodes[:MAX_EPISODES_PER_RUN]
    log.info("Total episodes to process: %d", len(all_episodes))

    new_clips = []
    for episode in all_episodes:
        ep_clips = process_episode(episode, seen)
        new_clips.extend(ep_clips)
        save_seen(seen)

    if new_clips:
        clips = new_clips + clips
        save_clips(clips)
        log.info("=== Done. %d new clip(s) added. %d total. ===", len(new_clips), len(clips))
    else:
        log.info("=== Done. 0 new clips. ===")

if __name__ == "__main__":
    main()
