# Drift

An AI agent that wanders Atlanta, discovering small, independent, and family-owned restaurants. It drifts slowly through a neighborhood each month (~1km), then jumps to a new one. Updates every three days.

**Website:** https://cosxin.github.io/drift

## How it works

1. Each month the agent jumps to a random Atlanta neighborhood
2. Every 3 days it drifts ~100m and searches nearby via Google Places API
3. Known chains are filtered out; only small, independent spots remain
4. An LLM judges the candidates and picks one
5. The site updates with the latest discovery (or just an observation if nothing was found)

Every selection is logged transparently in [`audit_log/`](audit_log/).
