## BLIND — Website Build Prompt for Claude Code

### Concept

Blind is an AI agent that lives on a map. Every day it moves to a new spot in the city — a random walk through neighborhoods. Most days, nothing happens. It's just passing through. Some days it discovers a small, independent, family-owned restaurant and spotlights it.

The website shows ONE page: where Blind is right now, and what it sees today. No archive on the site. No history page. No "past picks." Tomorrow the page is completely different. If you missed today, you missed it. The ephemerality is the point.

Audit logs of every day (discoveries and non-discoveries) are saved to files and committed to a public GitHub repo for transparency — but they are NOT displayed on the website itself.

### What's already built

**Selection script (`blind_select.py`)** is complete. It:
- Picks a random neighborhood from 66 Atlanta neighborhoods
- Searches Google Places API (New) for restaurants
- Hard-prefilters: 124 known chains removed, 5–300 review band, closed/low-rated removed
- Samples to ~10 candidates
- Two modes: `--mode random` (uniform random) or `--mode llm` (default, sends to Gemini 2.5 Flash free tier to judge family-owned likelihood and filter non-restaurants)
- Outputs `current.json` with full selection record

The script needs a new mode added: a "walk only" mode for days with no discovery, which outputs a `current.json` with just a position, neighborhood, and observation text but no `selected` restaurant.

### The `current.json` schema

**Discovery day** (agent found a restaurant):
```json
{
  "timestamp": "2026-03-19T14:30:00+00:00",
  "city": "Atlanta, GA",
  "neighborhood_searched": "Clarkston, GA",
  "day_number": 5,
  "observation": "The agent's first-person observation about the neighborhood...",
  "selected": {
    "name": "Kathmandu Kitchen",
    "address": "3871 Church St, Clarkston, GA 30021",
    "rating": 4.4,
    "review_count": 89,
    "lat": 33.8095,
    "lng": -84.2399,
    "phone": "(404) 555-0142",
    "website": "https://example.com",
    "google_maps_url": "https://maps.google.com/?cid=...",
    "hours": ["Monday: 11:00 AM – 9:30 PM", ...],
    "editorial_summary": "Family-run Nepalese restaurant..."
  },
  "llm_judgment": {
    "reasoning": "Why the LLM picked this one..."
  },
  "selection_id": "a1b2c3d4",
  "selection_mode": "llm"
}
```

**Nothing day** (agent is just walking):
```json
{
  "timestamp": "2026-03-20T14:30:00+00:00",
  "city": "Atlanta, GA",
  "neighborhood_searched": "Scottdale, GA",
  "day_number": 6,
  "walk_position": { "lat": 33.7972, "lng": -84.2635 },
  "observation": "Strip malls and auto repair shops. A church converted from a gas station. The sidewalk ends abruptly and becomes red clay.",
  "selected": null
}
```

### What I need you to build

A single-page static website. One HTML file. Loads `data/current.json` and renders the current state.

#### Layout (top to bottom):

1. **Header** — "Blind" logo (left), date (right). Minimal.

2. **Content area** — The main body:
   - Neighborhood + city label (small, monospace, uppercase)
   - The agent's observation text (serif, larger, the main readable content)
   - If discovery: a discovery card with restaurant name, address, phone, website, hours, rating, LLM reasoning
   - If nothing: just the observation, maybe "Nothing today."

3. **Footer** —
   - Left: "An AI agent wandering Atlanta. No one paid for this. Tomorrow this page will be different."
   - Center/bottom: **Coordinates display — large, typographic**: format like `33.8095°N  84.2399°W` — this is the agent's position, prominent, like a location stamp
   - Right: link to GitHub source/audit repo

#### CRITICAL design decisions:

- **NO embedded Google Map.** No iframes, no map tiles, no Google Maps embed. The coordinates at the bottom ARE the map. The numbers are the location. Let the visitor look them up if they want. This keeps the site pure, fast, zero external dependencies for rendering.

- **NO archive/history on the website.** Audit logs are saved as files and pushed to GitHub. The website only shows today.

- **NO photos on the site.** Keep it text-only. If someone wants to see the restaurant, they follow the Google Maps link.

- **The coordinate display should be beautiful.** Think of it like a GPS readout or a nautical chart stamp. Monospace, but large and deliberate. Something like:

  ```
  33.8095°N  84.2399°W
  ```

  Or vertically stacked. The coordinates are the visual anchor of the bottom of the page. They ground the ephemeral text in a real physical place.

#### Design direction:
- **Dark, minimal, typographic.** The site is mostly text on a dark background.
- Color palette: near-black background, warm off-white text, one muted gold/amber accent color for coordinates and small labels.
- Fonts: a quality serif for the observation text (the agent's "voice"), monospace for metadata (coordinates, date, neighborhood label, hours).
- Subtle film grain overlay.
- Staggered fade-in animations on page load.
- Mobile-responsive. Should feel like reading on a phone at night.
- **No JavaScript frameworks.** Vanilla JS to fetch and render `current.json`, that's it.
- **No cookies, no analytics, no tracking.** Zero external requests except the JSON fetch and font loading.
- Works in a degraded state with JS disabled (show a noscript message).

#### File structure:
```
/var/www/blind/
├── index.html
├── data/
│   └── current.json
└── audit/          ← NOT served on the website, just in the git repo
    └── *.json
```

Caddy config is just:
```
blind.example.com {
    root * /var/www/blind
    file_server
}
```

#### What I'll handle separately:
- Domain + Caddy setup
- Daily cron to run the selection script and overwrite `data/current.json`
- Google Places / Gemini API keys
- GitHub repo setup
- Contacting restaurants

### Tone of the agent's "voice"

The observation text should read like field notes from something that is paying close attention but doesn't quite understand human life. Not robotic, not overly poetic. Dry, specific, occasionally funny. Short sentences. Notices physical details — signage, materials, light, sound. Doesn't editorialize.

Examples:
- "A parking lot with more potholes than parking spots. Three restaurants share one dumpster. The Vietnamese place has been open since 7 AM."
- "Residential blocks. Quiet. A dog tied to a mailbox, watching me with professional skepticism."
- "The strip mall looks abandoned but the taco truck in the parking lot has a line."

I'll generate these observations using the LLM during the daily cron run. You just need to render whatever string is in the `observation` field.

Build the site.