import os

# ── API Keys (from GitHub Secrets) ──────────────────────────────────────────
ASSEMBLYAI_API_KEY  = os.environ.get("ASSEMBLYAI_API_KEY", "")
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
R2_ACCESS_KEY_ID    = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_ACCOUNT_ID       = os.environ.get("R2_ACCOUNT_ID", "")
R2_PUBLIC_URL       = os.environ.get("R2_PUBLIC_URL", "")
R2_BUCKET_NAME      = "podcast-clips"
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")

# ── Pipeline Settings ────────────────────────────────────────────────────────
LOOKBACK_DAYS       = 14       # days back to fetch episodes
MAX_EPISODES_PER_RUN = 40      # safety cap per pipeline run
CLIP_BEFORE_SECS    = 20       # seconds before keyword match
CLIP_AFTER_SECS     = 30       # seconds after keyword match
SNIPPET_WORDS       = 50       # words around keyword for transcript snippet
OUTPUT_DIR          = "docs"
CLIPS_JSON          = "docs/clips.json"
SEEN_EPISODES_FILE  = "seen_episodes.json"

# ── Podcast Sources ──────────────────────────────────────────────────────────
PODCASTS = [
    {
        "name": "Sports Business Radio",
        "type": "rss",
        "url":  "https://cms.megaphone.fm/channel/sportsbusinessradio",
    },
    {
        "name": "SBJ Morning Buzzcast",
        "type": "rss",
        "url":  "https://feeds.simplecast.com/IDVEdQwe",
    },
    {
        "name": "SBJ Sports Media Podcast",
        "type": "rss",
        "url":  "https://feeds.simplecast.com/M6Ik0Ix0",
    },
    {
        "name": "The Joe Pomp Show",
        "type": "rss",
        "url":  "https://feeds.simplecast.com/joepompshow",
    },
    {
        "name": "Pardon My Take",
        "type": "rss",
        "url":  "https://mcsorleys.barstoolsports.com/feed/pardon-my-take",
    },
    {
        "name": "The Bill Simmons Podcast",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/the-bill-simmons-podcast",
    },
    {
        "name": "The Ryen Russillo Podcast",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/the-ryen-russillo-podcast",
    },
    {
        "name": "The Herd with Colin Cowherd",
        "type": "youtube",
        "url":  "https://www.youtube.com/@ColinCowherd",
    },
    {
        "name": "The Varsity",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/the-varsity",
    },
    {
        "name": "Marchand Sports Media",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/marchand-sports-media",
    },
    {
        "name": "Sporticast",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/sporticast",
    },
    {
        "name": "Sports Media with Richard Deitsch",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/sports-media-deitsch",
    },
    {
        "name": "The Press Box",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/the-press-box",
    },
    {
        "name": "The Town with Matthew Belloni",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/the-town-with-matthew-belloni",
    },
    {
        "name": "Awful Announcing Podcast",
        "type": "rss",
        "url":  "https://feeds.simplecast.com/awfulannouncing",
    },
    {
        "name": "Pablo Torre Finds Out",
        "type": "rss",
        "url":  "https://feeds.megaphone.fm/pablo-torre-finds-out",
    },
    {
        "name": "SI Media with Jimmy Traina",
        "type": "rss",
        "url":  "https://feeds.simplecast.com/si-media-jimmy-traina",
    },
    {
        "name": "Sports Media Watch Podcast",
        "type": "rss",
        "url":  "https://feeds.simplecast.com/sportsmediawatch",
    },
]

# ── Keywords & Names to Clip ─────────────────────────────────────────────────
KEYWORD_TOPICS = [
    # Industry topics
    "media rights deal",
    "brand partnership",
    "content distribution",
    "direct to consumer",
    "digital content strategy",
    "Netflix deal",
    "Amazon deal",
    "private equity",
    "athlete media",
    "distribution deal",
    "TV ratings",
    # Production companies
    "Omaha Productions",
    "Words + Pictures",
    "OBB",
    "ShadowLion",
    "Togethxr",
    "Springhill",
    "Fulwell",
    "SMAC Entertainment",
    "Skydance",
    # People
    "Jessica Berman",
    "Adam Silver",
    "Gary Bettman",
    "Rob Manfred",
    "Noah Garden",
    "Nick Khan",
    "Pat McAfee",
    "Ross Ketover",
    "Keith Cossrow",
    "Lindsay Rovegno",
    "Nick Parsons",
    "Brian Lockhart",
    "Marcia Cooke",
    "Jamie Horowitz",
    "Connor Schell",
    "Libby Geist",
    "Mike Levine",
    "Mark Steinberg",
    "Jon Weinbach",
]

# ── Claude AI Prompt for Smart Clip Detection ────────────────────────────────
AI_CLIP_PROMPT = """You are an editor for a sports media industry newsletter.
Review this podcast transcript and identify the 3-5 most clip-worthy moments
about sports media business, rights deals, streaming, production companies,
athlete-owned media, or industry figures.

For each clip return JSON with:
- start_time: seconds from start
- end_time: seconds from start  
- title: punchy headline (max 12 words)
- summary: 2-sentence context (what was said and why it matters)
- topics: list of 2-4 relevant topic tags
- people: list of names mentioned
- quality: score 1-10

Return only a JSON array. No other text."""
