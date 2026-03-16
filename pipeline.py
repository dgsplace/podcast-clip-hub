"""
Podcast Clip Hub — Pipeline
============================
Fetches new podcast episodes, transcribes them, finds clips using
AssemblyAI + Claude, cuts audio with ffmpeg, uploads to Cloudflare R2,
and writes clips.json for the GitHub Pages frontend.

Run manually:  python pipeline.py
Scheduled:     GitHub Actions daily cron
"""

import os
import re
import json
import time
import uuid
import logging
import hashlib
import tempfile
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import feedparser
import boto3
from botocore.config import Config
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Credentials from environment
# ─────────────────────────────────────────────────────────────────────────────

ASSEMBLYAI_API_KEY  = os.environ["ASSEMBLYAI_API_KEY"]
R2_ACCESS_KEY_ID    = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_ACCOUNT_ID       = os.environ["R2_ACCOUNT_ID"]
R2_PUBLIC_URL       = os.environ["R2_PUBLIC_URL"].rstrip("/")
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]

# R2 client (S3-compatible) — using urllib3 to bypass SSL issues on GitHub Actions
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import os as _os
_os.environ["AWS_CA_BUNDLE"] = ""

r2 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(
        signature_version="s3v4",
        retries={"max_attempts": 3},
    ),
    region_name="auto",
    verify=False,
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 podcast-clip-hub/1.0"})


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def episode_id(podcast_name, episode_title):
    raw = f"{podcast_name}|{episode_title}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def load_seen_episodes():
    path = Path("seen_episodes.json")
    if path.exists():
        return set(json.loads(path.read_text()))
    return set()


def save_seen_episodes(seen):
    Path("seen_episodes.json").write_text(json.dumps(list(seen)))


def load_clips():
    path = Path(config.CLIPS_JSON_PATH)
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_clips(clips):
    path = Path(config.CLIPS_JSON_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sort newest first
    clips.sort(key=lambda c: c.get("published", ""), reverse=True)
    path.write_text(json.dumps(clips, indent=2))
    log.info("Saved %d clips to %s", len(clips), path)


def seconds_to_hms(s):
    s = int(s)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


# ─────────────────────────────────────────────────────────────────────────────
#  Episode fetching
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rss_episodes(source):
    """Return list of recent episodes from an RSS feed."""
    log.info("Fetching RSS: %s", source["name"])
    try:
        feed = feedparser.parse(source["rss"])
    except Exception as exc:
        log.warning("RSS parse failed for %s: %s", source["name"], exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.LOOKBACK_DAYS)
    episodes = []

    for entry in feed.entries:
        # Parse published date
        pub = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if pub and pub < cutoff:
            continue

        # Find audio URL
        audio_url = None
        for enc in getattr(entry, "enclosures", []):
            if "audio" in enc.get("type", ""):
                audio_url = enc.href
                break
        if not audio_url:
            continue

        # Estimate duration
        duration_min = 0
        raw_dur = entry.get("itunes_duration", "")
        if raw_dur:
            parts = str(raw_dur).split(":")
            try:
                if len(parts) == 3:
                    duration_min = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 2:
                    duration_min = int(parts[0])
                else:
                    duration_min = int(raw_dur) // 60
            except Exception:
                pass

        if duration_min > config.MAX_EPISODE_MINUTES:
            log.info("  Skipping long episode (%d min): %s", duration_min, entry.title)
            continue

        episodes.append({
            "podcast":     source["name"],
            "tags":        source.get("tags", []),
            "title":       entry.get("title", "Untitled"),
            "audio_url":   audio_url,
            "published":   pub.isoformat() if pub else datetime.now(timezone.utc).isoformat(),
            "description": entry.get("summary", "")[:500],
            "duration_min": duration_min,
        })

    log.info("  Found %d recent episode(s)", len(episodes))
    return episodes


def fetch_youtube_episodes(source):
    """Use yt-dlp to get recent episodes from a YouTube channel."""
    log.info("Fetching YouTube: %s", source["name"])
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--playlist-end", "5",
                "--print", "%(id)s\t%(title)s\t%(upload_date)s\t%(duration)s",
                source["channel_url"],
            ],
            capture_output=True, text=True, timeout=60
        )
        episodes = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=config.LOOKBACK_DAYS)
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            vid_id, title, upload_date, duration = parts[0], parts[1], parts[2], parts[3]
            try:
                pub = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if pub < cutoff:
                continue
            try:
                duration_min = int(duration) // 60
            except Exception:
                duration_min = 0
            if duration_min > config.MAX_EPISODE_MINUTES:
                log.info("  Skipping long episode (%d min): %s", duration_min, title)
                continue
            episodes.append({
                "podcast":     source["name"],
                "tags":        source.get("tags", []),
                "title":       title,
                "audio_url":   f"https://www.youtube.com/watch?v={vid_id}",
                "published":   pub.isoformat(),
                "description": "",
                "duration_min": duration_min,
                "youtube_id":  vid_id,
            })
        log.info("  Found %d recent episode(s)", len(episodes))
        return episodes
    except Exception as exc:
        log.warning("yt-dlp failed for %s: %s", source["name"], exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  Audio download
# ─────────────────────────────────────────────────────────────────────────────

def download_audio(episode, tmpdir):
    """Download episode audio to a temp file. Returns path or None."""
    out_path = Path(tmpdir) / "episode.mp3"

    if episode.get("youtube_id"):
        # Download from YouTube via yt-dlp
        log.info("  Downloading YouTube audio: %s", episode["title"])
        try:
            subprocess.run(
                [
                    "yt-dlp",
                    "-x", "--audio-format", "mp3",
                    "--audio-quality", "5",
                    "-o", str(out_path).replace(".mp3", ".%(ext)s"),
                    episode["audio_url"],
                ],
                check=True, capture_output=True, timeout=300
            )
            # yt-dlp may output .mp3 directly or rename
            candidates = list(Path(tmpdir).glob("episode.*"))
            if candidates:
                return str(candidates[0])
        except Exception as exc:
            log.warning("  YouTube download failed: %s", exc)
            return None
    else:
        # Direct HTTP download
        log.info("  Downloading audio: %s", episode["audio_url"][:80])
        try:
            resp = SESSION.get(episode["audio_url"], stream=True, timeout=60)
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(65536):
                    f.write(chunk)
            return str(out_path)
        except Exception as exc:
            log.warning("  Download failed: %s", exc)
            return None


# ─────────────────────────────────────────────────────────────────────────────
#  Transcription
# ─────────────────────────────────────────────────────────────────────────────

def transcribe(audio_path):
    """
    Transcribe audio using AssemblyAI REST API directly.
    Returns a simple object with .text and .words attributes.
    """
    log.info("  Transcribing with AssemblyAI...")
    headers = {"authorization": ASSEMBLYAI_API_KEY, "content-type": "application/json"}

    # 1. Upload audio file
    try:
        with open(audio_path, "rb") as f:
            upload_resp = requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers={"authorization": ASSEMBLYAI_API_KEY},
                data=f,
                timeout=120,
            )
        upload_resp.raise_for_status()
        upload_url = upload_resp.json()["upload_url"]
    except Exception as exc:
        log.warning("  Upload failed: %s", exc)
        return None

    # 2. Submit transcription job
    try:
        transcript_resp = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers=headers,
            json={
                "audio_url": upload_url,
                "speech_models": ["universal-2"],
                "speaker_labels": True,
                "auto_highlights": True,
            },
            timeout=30,
        )
        transcript_resp.raise_for_status()
        transcript_id = transcript_resp.json()["id"]
    except Exception as exc:
        log.warning("  Transcription submit failed: %s", exc)
        return None

    # 3. Poll until complete
    polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    max_wait = 600  # 10 minutes
    waited = 0
    while waited < max_wait:
        try:
            poll_resp = requests.get(polling_url, headers=headers, timeout=30)
            poll_resp.raise_for_status()
            data = poll_resp.json()
            status = data.get("status")
            if status == "completed":
                log.info("  Transcription complete (%d chars)", len(data.get("text") or ""))
                # Build a simple result object
                class TranscriptResult:
                    pass
                result = TranscriptResult()
                result.text = data.get("text", "")
                # Build word objects
                class Word:
                    def __init__(self, d):
                        self.text  = d.get("text", "")
                        self.start = d.get("start", 0)
                        self.end   = d.get("end", 0)
                result.words = [Word(w) for w in data.get("words") or []]
                return result
            elif status == "error":
                log.warning("  Transcription error: %s", data.get("error"))
                return None
            else:
                time.sleep(5)
                waited += 5
        except Exception as exc:
            log.warning("  Polling error: %s", exc)
            time.sleep(5)
            waited += 5

    log.warning("  Transcription timed out after %ds", max_wait)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Clip detection
# ─────────────────────────────────────────────────────────────────────────────

def extract_snippet(words, match_time_ms, context_words=25):
    """
    Extract ~50 words around a keyword match time.
    Returns dict with plain text and the matched keyword char offset.
    """
    # Find word index closest to match time
    best_idx = 0
    best_diff = float("inf")
    for i, w in enumerate(words):
        diff = abs(w.start - match_time_ms)
        if diff < best_diff:
            best_diff = diff
            best_idx = i

    start_idx = max(0, best_idx - context_words)
    end_idx   = min(len(words), best_idx + context_words)
    snippet_words = words[start_idx:end_idx]

    # Build plain text + find highlight offset
    text = " ".join(w.text for w in snippet_words)
    # Highlight offset = char position of the matched word within snippet
    pre_text = " ".join(w.text for w in snippet_words[:best_idx - start_idx])
    highlight_start = len(pre_text) + (1 if pre_text else 0)
    matched_word = words[best_idx].text if best_idx < len(words) else ""

    return {
        "text":            text,
        "highlight_start": highlight_start,
        "highlight_len":   len(matched_word),
    }


def find_keyword_clips(transcript, episode):
    """
    Find transcript segments containing keyword topics.
    Each clip is exactly 20s before + 30s after the keyword match (50s total).
    """
    if not transcript.words:
        return []

    keywords = [k.lower() for k in config.KEYWORD_TOPICS]
    words    = transcript.words
    clips    = []
    found_times = set()

    for word in words:
        word_text_lower = word.text.lower()
        matched_kw = next(
            (kw for kw in config.KEYWORD_TOPICS if kw.lower() in word_text_lower),
            None
        )
        if not matched_kw:
            # Also check multi-word keywords spanning this word
            matched_kw = next(
                (kw for kw in config.KEYWORD_TOPICS
                 if len(kw.split()) > 1 and kw.lower() in
                 " ".join(w.text for w in words[max(0,words.index(word)-3):words.index(word)+3]).lower()),
                None
            )
        if not matched_kw:
            continue

        match_sec = word.start / 1000
        start     = max(0, match_sec - 20)   # 20 seconds before
        end       = match_sec + 30             # 30 seconds after
        end       = min(end, words[-1].end / 1000)

        # Deduplicate — skip if we already have a clip within 25s
        bucket = int(match_sec / 25) * 25
        if bucket in found_times:
            continue
        found_times.add(bucket)

        # ~50-word snippet centred on the match
        snippet = extract_snippet(words, word.start, context_words=25)

        clips.append({
            "start_time":      start,
            "end_time":        end,
            "title":           f"{matched_kw.title()} — {episode['podcast']}",
            "summary":         snippet["text"][:300],
            "transcript_snippet": snippet,
            "matched_keyword": matched_kw,
            "topics":          [matched_kw],
            "people":          [],
            "quality_score":   7,
            "source":          "keyword",
        })

    return clips[:config.MAX_CLIPS_PER_EPISODE]


def find_ai_clips(transcript, episode):
    """Use Claude to find compelling clips in the transcript."""
    log.info("  Running AI clip detection...")
    if not transcript.text:
        return []

    # Split transcript into chunks with timestamps
    words = transcript.words or []
    chunk_size = 3000  # chars per chunk
    text = transcript.text
    chunks = [text[i:i+chunk_size] for i in range(0, min(len(text), 30000), chunk_size)]

    # Build timestamped transcript (sample every 50 words for context)
    ts_lines = []
    for i, word in enumerate(words):
        if i % 50 == 0:
            t = word.start / 1000
            ts_lines.append(f"[{seconds_to_hms(t)}] {word.text}")
    timestamped = "\n".join(ts_lines[:200])

    prompt = f"""Podcast: {episode['podcast']}
Episode: {episode['title']}

Timestamped transcript sample:
{timestamped}

Full transcript (first 6000 chars):
{text[:6000]}

{config.AI_CLIP_PROMPT}

Return ONLY valid JSON array, no other text."""

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
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        clips = json.loads(raw)
        log.info("  AI found %d clip(s)", len(clips))
        return [c for c in clips if c.get("quality_score", 0) >= 7]
    except Exception as exc:
        log.warning("  AI clip detection failed: %s", exc)
        return []


def merge_clips(keyword_clips, ai_clips):
    """Merge and deduplicate clips, preferring AI clips."""
    all_clips = list(ai_clips)
    ai_starts = {int(c["start_time"] / 30) for c in ai_clips}

    for kc in keyword_clips:
        if int(kc["start_time"] / 30) not in ai_starts:
            all_clips.append(kc)

    # Sort by quality score descending, take top N
    all_clips.sort(key=lambda c: c.get("quality_score", 0), reverse=True)
    return all_clips[:config.MAX_CLIPS_PER_EPISODE]


# ─────────────────────────────────────────────────────────────────────────────
#  Audio cutting
# ─────────────────────────────────────────────────────────────────────────────

def cut_clip(audio_path, start, end, out_path):
    """Cut a clip from audio using ffmpeg."""
    duration = end - start
    duration = max(config.MIN_CLIP_SECONDS, min(duration, config.MAX_CLIP_SECONDS))
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", audio_path,
                "-t", str(duration),
                "-acodec", "libmp3lame",
                "-ab", "128k",
                "-ar", "44100",
                out_path,
            ],
            check=True, capture_output=True, timeout=120
        )
        return True
    except Exception as exc:
        log.warning("  ffmpeg failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  R2 upload
# ─────────────────────────────────────────────────────────────────────────────

def upload_to_r2(local_path, key):
    """Upload a file to Cloudflare R2 using pre-signed URL via boto3, then PUT via requests."""
    log.info("  Uploading to R2: %s", key)
    try:
        # Generate a pre-signed PUT URL
        presigned_url = r2.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": config.R2_BUCKET_NAME,
                "Key": key,
                "ContentType": "audio/mpeg",
            },
            ExpiresIn=300,
        )
        # Upload using requests with SSL verification disabled
        with open(local_path, "rb") as f:
            resp = requests.put(
                presigned_url,
                data=f,
                headers={"Content-Type": "audio/mpeg"},
                verify=False,
                timeout=120,
            )
        resp.raise_for_status()
        return f"{R2_PUBLIC_URL}/{key}"
    except Exception as exc:
        log.warning("  R2 upload failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Process one episode
# ─────────────────────────────────────────────────────────────────────────────

def process_episode(episode, seen):
    """Full pipeline for one episode. Returns list of clip records."""
    ep_id = episode_id(episode["podcast"], episode["title"])
    if ep_id in seen:
        log.info("  Already processed: %s", episode["title"])
        return []

    log.info("Processing: [%s] %s", episode["podcast"], episode["title"])

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Download
        audio_path = download_audio(episode, tmpdir)
        if not audio_path:
            seen.add(ep_id)
            return []

        # 2. Transcribe
        transcript = transcribe(audio_path)
        if not transcript:
            seen.add(ep_id)
            return []

        # 3. Find clips
        keyword_clips = find_keyword_clips(transcript, episode)
        ai_clips      = find_ai_clips(transcript, episode)
        clips         = merge_clips(keyword_clips, ai_clips)
        log.info("  %d clip(s) to cut", len(clips))

        # 4. Cut + upload each clip
        records = []
        for i, clip in enumerate(clips):
            clip_filename = f"{ep_id}_{i}.mp3"
            clip_path     = str(Path(tmpdir) / clip_filename)

            if not cut_clip(audio_path, clip["start_time"], clip["end_time"], clip_path):
                continue

            r2_key   = f"clips/{datetime.now().strftime('%Y/%m')}/{clip_filename}"
            clip_url = upload_to_r2(clip_path, r2_key)
            if not clip_url:
                continue

            # Collect people mentioned in transcript around the clip
            all_people = list(set(clip.get("people", [])))

            clip_id = f"{ep_id}_{i}"
            records.append({
                "id":                 clip_id,
                "podcast":            episode["podcast"],
                "episode":            episode["title"],
                "episode_url":        episode.get("audio_url", ""),
                "published":          episode["published"],
                "title":              clip["title"],
                "summary":            clip["summary"],
                "transcript_snippet": clip.get("transcript_snippet", {}),
                "matched_keyword":    clip.get("matched_keyword", ""),
                "topics":             clip.get("topics", []),
                "people":             all_people,
                "audio_url":          clip_url,
                "start_time":         clip["start_time"],
                "duration":           clip["end_time"] - clip["start_time"],
                "quality":            clip.get("quality_score", 7),
                "ratings":            [],
                "avg_rating":         0,
                "created_at":  datetime.now(timezone.utc).isoformat(),
            })
            log.info("  Clip saved: %s", clip["title"])

    seen.add(ep_id)
    return records


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Podcast Clip Hub Pipeline — %s ===",
             datetime.now().strftime("%Y-%m-%d %H:%M"))

    seen  = load_seen_episodes()
    clips = load_clips()

    # Collect all episodes to process
    episodes = []
    for source in config.RSS_FEEDS:
        episodes.extend(fetch_rss_episodes(source))
    for source in config.YOUTUBE_CHANNELS:
        episodes.extend(fetch_youtube_episodes(source))

    log.info("Total episodes to consider: %d", len(episodes))

    new_clip_count = 0
    for episode in episodes:
        new_clips = process_episode(episode, seen)
        clips.extend(new_clips)
        new_clip_count += len(new_clips)
        # Save after each episode in case of failure
        save_clips(clips)
        save_seen_episodes(seen)

    log.info("=== Done. %d new clip(s) added. %d total. ===",
             new_clip_count, len(clips))


if __name__ == "__main__":
    main()
