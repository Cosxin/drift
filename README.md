# Drift

An AI agent that wanders Atlanta each day, discovering small, independent, and family-owned restaurants. The site shows only today's find — no archive, no history. Tomorrow the page is completely different.

**Website:** https://cosxin.github.io/drift

## How it works

1. A random Atlanta neighborhood is selected daily
2. Google Places API surfaces nearby restaurants
3. Known chains are filtered out; only small, independent spots remain
4. An LLM judges the candidates and picks one
5. The site updates with today's discovery (or just an observation if nothing was found)

Every selection is logged transparently in [`audit_log/`](audit_log/).
