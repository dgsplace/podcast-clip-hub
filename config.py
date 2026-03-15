"""
Podcast Clip Hub — Configuration
=================================
Add/remove podcasts here. YouTube channels use yt-dlp.
RSS feeds use feedparser + requests.
"""

# ─────────────────────────────────────────────────────────────────────────────
#  Podcast Sources
# ─────────────────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    {
        "name": "Sports Business Radio",
        "rss": "https://cms.megaphone.fm/channel/sportsbusinessradio",
        "tags": ["sports business"],
    },
    {
        "name": "SBJ Morning Buzzcast",
        "rss": "https://feeds.simplecast.com/IDVEdQwe",
        "tags": ["sports business", "media"],
    },
    {
        "name": "SBJ Sports Media Podcast",
        "rss": "https://feeds.simplecast.com/sbj-sports-media",
        "tags": ["sports business", "media"],
    },
    {
        "name": "The Joe Pomp Show",
        "rss": "https://feeds.simplecast.com/joe-pomp-show",
        "tags": ["sports business"],
    },
    {
        "name": "Pardon My Take",
        "rss": "https://mcsorleys.barstoolsports.com/feed/pardon-my-take",
        "tags": ["sports", "comedy"],
    },
    {
        "name": "The Bill Simmons Podcast",
        "rss": "https://feeds.megaphone.fm/the-bill-simmons-podcast",
        "tags": ["sports", "media"],
    },
    {
        "name": "The Ryen Russillo Podcast",
        "rss": "https://feeds.megaphone.fm/the-ryen-russillo-podcast",
        "tags": ["sports"],
    },
    {
        "name": "The Varsity",
        "rss": "https://feeds.megaphone.fm/the-varsity",
        "tags": ["sports business", "media"],
    },
    {
        "name": "Marchand Sports Media",
        "rss": "https://andrewmarchand.substack.com/feed/podcast",
        "tags": ["sports media"],
    },
    {
        "name": "Sporticast",
        "rss": "https://feeds.megaphone.fm/sporticast",
        "tags": ["sports business"],
    },
    {
        "name": "Sports Media with Richard Deitsch",
        "rss": "https://feeds.megaphone.fm/sports-media-deitsch",
        "tags": ["sports media"],
    },
    {
        "name": "The Press Box",
        "rss": "https://feeds.megaphone.fm/the-press-box",
        "tags": ["media"],
    },
    {
        "name": "The Town with Matthew Belloni",
        "rss": "https://feeds.megaphone.fm/the-town-with-matthew-belloni",
        "tags": ["media", "hollywood"],
    },
]

YOUTUBE_CHANNELS = [
    {
        "name": "The Herd with Colin Cowherd",
        "channel_url": "https://www.youtube.com/@TheHerd",
        "tags": ["sports"],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
#  Clip Detection
# ─────────────────────────────────────────────────────────────────────────────

# Keywords that always trigger a clip (case-insensitive)
KEYWORD_TOPICS = [
    "media rights",
    "streaming",
    "ESPN",
    "Netflix",
    "Amazon",
    "Apple",
    "YouTube",
    "NBC Sports",
    "Fox Sports",
    "CBS Sports",
    "TNT",
    "NBA",
    "NFL",
    "MLB",
    "NHL",
    "college football",
    "NIL",
    "private equity",
    "valuation",
    "stadium",
    "broadcast deal",
    "ratings",
    "viewership",
    "rights deal",
    "Peacock",
    "Max",
    "Paramount",
    "Disney",
    "Spotify",
    "podcast",
]

# AI also autonomously finds compelling clips beyond keywords
AI_CLIP_PROMPT = """
You are an expert at identifying compelling moments in sports media podcasts.

Given a transcript segment, identify moments worth clipping (30-120 seconds) that are:
1. A strong opinion or bold take on sports media/business
2. A breaking news reveal or exclusive information
3. A heated debate or disagreement between hosts
4. A surprising statistic or financial figure
5. A prediction about the future of sports media
6. A notable quote from a guest that stands alone

For each clip, return JSON with:
- start_time: seconds from episode start
- end_time: seconds from episode start  
- title: punchy 8-word max title
- summary: 1-2 sentence description
- topics: list of relevant topic tags (from: sports business, media rights, streaming, ESPN, NFL, NBA, MLB, NHL, college sports, ratings, technology, personalities)
- people: list of people mentioned by name
- quality_score: 1-10 (10 = must-listen moment)

Only return clips with quality_score >= 7. Return as JSON array.
"""

# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline Settings
# ─────────────────────────────────────────────────────────────────────────────

# How many days back to look for new episodes
LOOKBACK_DAYS = 5

# Max episode duration to process (minutes) — skip very long episodes to control costs
MAX_EPISODE_MINUTES = 90

# Max clips per episode
MAX_CLIPS_PER_EPISODE = 5

# Min/max clip duration in seconds
MIN_CLIP_SECONDS = 30
MAX_CLIP_SECONDS = 120

# AssemblyAI model
ASSEMBLYAI_SPEECH_MODEL = "best"

# Cloudflare R2
R2_BUCKET_NAME = "podcast-clips"
R2_ENDPOINT = "https://{account_id}.r2.cloudflarestorage.com"

# Output
CLIPS_JSON_PATH = "docs/clips.json"
