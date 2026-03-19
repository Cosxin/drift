#!/usr/bin/env python3
"""
BLIND — Restaurant Selection Script
====================================
Two-phase selection pipeline for the Blind website.

Phase 1 (Geo + Prefilter):
    Pick a random neighborhood, search Google Places, apply hard filters
    (chains, review band, closed), produce ~10 candidates.

Phase 2 (Selection — two modes):
    --mode random    Uniform random pick from candidates (default)
    --mode llm       Send candidates to an LLM to judge which is most likely
                     family-owned, independent, and deserving of a spotlight.
                     The LLM also filters out non-restaurants (adult clubs,
                     bars, etc.) that slipped through the hard filters.

Usage:
    python blind_select.py --city atlanta --api-key GKEY --pick --mode random
    python blind_select.py --city atlanta --api-key GKEY --pick --mode llm --llm-api-key LKEY

Requires:
    - Google Places API (New) key
    - For --mode llm: an Anthropic API key (or DeepSeek, configurable)
"""

import json
import math
import random
import sys
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# ── City Neighborhood Pools ──────────────────────────────────────────────

CITIES = {
    "atlanta": {
        "display_name": "Atlanta, GA",
        "neighborhoods": [
            # Core city
            "Downtown Atlanta, GA",
            "Midtown Atlanta, GA",
            "Buckhead Atlanta, GA",
            "West End Atlanta, GA",
            "East Atlanta Village, GA",
            "Old Fourth Ward Atlanta, GA",
            "Inman Park Atlanta, GA",
            "Little Five Points Atlanta, GA",
            "Kirkwood Atlanta, GA",
            "Reynoldstown Atlanta, GA",
            "Grant Park Atlanta, GA",
            "Cabbagetown Atlanta, GA",
            "Edgewood Atlanta, GA",
            "Vine City Atlanta, GA",
            "Westview Atlanta, GA",
            "Bankhead Atlanta, GA",
            "Collier Hills Atlanta, GA",
            "Piedmont Heights Atlanta, GA",
            "Morningside-Lenox Park Atlanta, GA",
            "Virginia-Highland Atlanta, GA",
            "Poncey-Highland Atlanta, GA",
            "Ansley Park Atlanta, GA",
            "Home Park Atlanta, GA",
            "Bolton Atlanta, GA",
            "Sylvan Hills Atlanta, GA",
            "Capitol View Atlanta, GA",
            "Pittsburgh Atlanta, GA",
            "Mechanicsville Atlanta, GA",
            "Adair Park Atlanta, GA",
            "Castleberry Hill Atlanta, GA",
            # Inner suburbs
            "Decatur, GA",
            "East Point, GA",
            "College Park, GA",
            "Clarkston, GA",
            "Chamblee, GA",
            "Doraville, GA",
            "Tucker, GA",
            "Stone Mountain, GA",
            "Avondale Estates, GA",
            "Scottdale, GA",
            "Pine Lake, GA",
            "Lithonia, GA",
            "Conley, GA",
            # Buford Highway corridor
            "Buford Highway Doraville, GA",
            "Buford Highway Chamblee, GA",
            "Buford Highway Brookhaven, GA",
            # Outer suburbs
            "Marietta, GA",
            "Smyrna, GA",
            "Kennesaw, GA",
            "Roswell, GA",
            "Alpharetta, GA",
            "Duluth, GA",
            "Lawrenceville, GA",
            "Norcross, GA",
            "Lilburn, GA",
            "Snellville, GA",
            "Riverdale, GA",
            "Forest Park, GA",
            "Morrow, GA",
            "Jonesboro, GA",
            "Austell, GA",
            "Mableton, GA",
            "Powder Springs, GA",
            "Acworth, GA",
            "Woodstock, GA",
            "Canton, GA",
        ],
        "min_reviews": 5,
        "max_reviews": 300,
    },
}


# ── Known Chain Restaurants ──────────────────────────────────────────────

KNOWN_CHAINS = {
    "mcdonald's", "mcdonalds", "burger king", "wendy's", "wendys",
    "taco bell", "chick-fil-a", "chickfila", "subway", "domino's", "dominos",
    "pizza hut", "papa john's", "papa johns", "little caesars",
    "popeyes", "kfc", "kentucky fried chicken", "arby's", "arbys",
    "sonic drive-in", "sonic", "jack in the box",
    "five guys", "shake shack", "in-n-out", "whataburger",
    "chipotle", "qdoba", "moe's southwest", "moes southwest",
    "panera", "panera bread", "au bon pain",
    "starbucks", "dunkin", "dunkin donuts", "dunkin'",
    "panda express", "pei wei",
    "olive garden", "red lobster", "outback steakhouse", "longhorn steakhouse",
    "applebee's", "applebees", "chili's", "chilis", "tgi friday's", "tgi fridays",
    "ihop", "denny's", "dennys", "waffle house", "cracker barrel",
    "golden corral", "bob evans", "perkins",
    "raising cane's", "raising canes", "zaxby's", "zaxbys",
    "wingstop", "buffalo wild wings",
    "firehouse subs", "jersey mike's", "jersey mikes", "jimmy john's", "jimmy johns",
    "jason's deli", "jasons deli", "mcalister's", "mcalisters",
    "the cheesecake factory", "cheesecake factory",
    "red robin", "ruby tuesday", "texas roadhouse", "logan's roadhouse",
    "crumbl", "insomnia cookies",
    "steak 'n shake", "steak n shake",
    "bojangles", "church's chicken", "churchs chicken",
    "el pollo loco", "del taco", "checkers", "rally's",
    "white castle", "culver's", "culvers",
    "noodles & company", "noodles and company",
    "tropical smoothie", "smoothie king", "jamba juice", "jamba",
    "chuy's", "chuys", "bahama breeze", "bonefish grill",
    "carrabba's", "carrabbas", "capital grille",
    "ruth's chris", "ruths chris", "morton's", "mortons",
    "sweetgreen", "cava", "chipotle mexican grill",
    "cook out", "cookout",
    "marco's pizza", "marcos pizza", "hungry howie's",
    "wawa", "sheetz", "buc-ee's", "bucees",
    "tim hortons", "krispy kreme",
}


def is_chain(name: str) -> bool:
    """Check if a restaurant name matches a known chain."""
    name_lower = name.lower().strip()
    for chain in KNOWN_CHAINS:
        if name_lower == chain:
            return True
        if name_lower.startswith(chain) and len(name_lower) > len(chain):
            next_char = name_lower[len(chain)]
            if not next_char.isalpha():
                return True
    return False


# ── Drift State Management ───────────────────────────────────────────────

DRIFT_STATE_FILE = "data/drift_state.json"

# ~1km total drift per month. With runs every 3 days (~10 runs/month),
# each step is ~100m. In degrees: 100m ≈ 0.0009° lat, ~0.0011° lng at Atlanta's latitude.
DRIFT_STEP_DEG = 0.001  # ~100m per step


def geocode_neighborhood(neighborhood: str, api_key: str) -> tuple[float, float] | None:
    """Geocode a neighborhood name to lat/lng using Google Geocoding API."""
    import requests

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": neighborhood, "key": api_key}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                loc = results[0]["geometry"]["location"]
                return loc["lat"], loc["lng"]
    except Exception as e:
        print(f"  [WARN] Geocoding failed: {e}")
    return None


def load_drift_state() -> dict | None:
    """Load the current drift state from disk."""
    path = Path(DRIFT_STATE_FILE)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_drift_state(state: dict):
    """Save drift state to disk."""
    Path(DRIFT_STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(DRIFT_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_drift_position(neighborhood_pool: list[str], api_key: str) -> tuple[str, float, float]:
    """
    Determine the search position using drift logic:
    - New month → jump to a random neighborhood (big move)
    - Same month → drift ~100m from last position (small move)

    Returns (neighborhood_name, lat, lng).
    """
    now = datetime.now(timezone.utc)
    current_month = now.strftime("%Y-%m")

    state = load_drift_state()

    # Check if we need a new month jump
    if state is None or state.get("month") != current_month:
        # New month: pick a random neighborhood and geocode it
        neighborhood = random.choice(neighborhood_pool)
        print(f"\n  New month ({current_month}) — jumping to: {neighborhood}")
        coords = geocode_neighborhood(neighborhood, api_key)
        if coords:
            lat, lng = coords
        else:
            # Fallback: Atlanta center
            print("  [WARN] Geocoding failed, using Atlanta center.")
            lat, lng = 33.749, -84.388
            neighborhood = "Downtown Atlanta, GA"

        state = {
            "month": current_month,
            "neighborhood": neighborhood,
            "base_lat": lat,
            "base_lng": lng,
            "current_lat": lat,
            "current_lng": lng,
            "step_count": 0,
        }
        save_drift_state(state)
        return neighborhood, lat, lng

    # Same month: drift from current position
    prev_lat = state["current_lat"]
    prev_lng = state["current_lng"]

    # Random direction, fixed step size
    angle = random.uniform(0, 2 * math.pi)
    new_lat = prev_lat + DRIFT_STEP_DEG * math.sin(angle)
    new_lng = prev_lng + DRIFT_STEP_DEG * math.cos(angle)

    state["current_lat"] = new_lat
    state["current_lng"] = new_lng
    state["step_count"] = state.get("step_count", 0) + 1
    save_drift_state(state)

    neighborhood = state["neighborhood"]
    print(f"\n  Drifting within {neighborhood} (step {state['step_count']})")
    print(f"  Position: {new_lat:.4f}, {new_lng:.4f}")
    return neighborhood, new_lat, new_lng


# ── Google Places API (New) ──────────────────────────────────────────────

def search_restaurants_google(neighborhood: str, api_key: str,
                              lat: float | None = None, lng: float | None = None) -> list[dict]:
    """Search Google Places API (New) for restaurants, optionally biased to a location."""
    import requests

    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ",".join([
            "places.id",
            "places.displayName",
            "places.formattedAddress",
            "places.rating",
            "places.userRatingCount",
            "places.businessStatus",
            "places.priceLevel",
            "places.types",
            "places.location",
            "places.primaryType",
        ]),
    }
    body = {
        "textQuery": f"restaurants in {neighborhood}",
        "includedType": "restaurant",
        "languageCode": "en",
    }

    # Use location bias if coordinates are provided (drift mode)
    if lat is not None and lng is not None:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": 1000.0,  # 1km radius
            }
        }

    response = requests.post(url, headers=headers, json=body)

    if response.status_code != 200:
        print(f"  [WARN] Google API HTTP {response.status_code}")
        print(f"  [WARN] Response: {response.text[:300]}")
        return []

    data = response.json()
    results = []
    for p in data.get("places", []):
        loc = p.get("location", {})
        results.append({
            "name": p.get("displayName", {}).get("text", ""),
            "formatted_address": p.get("formattedAddress", ""),
            "rating": p.get("rating", 0),
            "user_ratings_total": p.get("userRatingCount", 0),
            "business_status": p.get("businessStatus", "OPERATIONAL"),
            "place_id": p.get("id", ""),
            "geometry": {"location": {"lat": loc.get("latitude"), "lng": loc.get("longitude")}},
            "price_level": p.get("priceLevel"),
            "types": p.get("types", []),
            "primary_type": p.get("primaryType", ""),
        })

    return results


def get_place_details(place_id: str, api_key: str) -> dict:
    """Fetch detailed info for a specific place."""
    import requests

    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ",".join([
            "displayName",
            "formattedAddress",
            "nationalPhoneNumber",
            "internationalPhoneNumber",
            "websiteUri",
            "googleMapsUri",
            "rating",
            "userRatingCount",
            "priceLevel",
            "types",
            "businessStatus",
            "regularOpeningHours",
            "photos",
            "editorialSummary",
        ]),
    }

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"  [WARN] Details API HTTP {response.status_code}")
        return {}

    data = response.json()
    hours_texts = data.get("regularOpeningHours", {}).get("weekdayDescriptions", [])
    photos = data.get("photos", [])
    photo_names = [p.get("name", "") for p in photos[:5]]

    return {
        "formatted_phone_number": data.get("nationalPhoneNumber") or data.get("internationalPhoneNumber"),
        "website": data.get("websiteUri"),
        "url": data.get("googleMapsUri"),
        "opening_hours": {"weekday_text": hours_texts},
        "editorial_summary": {"overview": data.get("editorialSummary", {}).get("text")},
        "photos": [{"photo_reference": n} for n in photo_names],
    }


# ══════════════════════════════════════════════════════════════════════════
# PHASE 1: Hard Prefilter (chains, review band, closed — NO venue type filter)
# ══════════════════════════════════════════════════════════════════════════

def prefilter_candidates(places: list[dict], min_reviews: int, max_reviews: int,
                         target_count: int = 10) -> list[dict]:
    """
    Apply hard filters only: chains, review band, closed, very low rating.
    Does NOT filter on venue type — that's the LLM's job in mode=llm.
    Returns up to target_count candidates.
    """
    candidates = []

    for place in places:
        name = place.get("name", "")
        review_count = place.get("user_ratings_total", 0)
        status = place.get("business_status", "OPERATIONAL")
        rating = place.get("rating", 0)

        if is_chain(name):
            continue
        if status != "OPERATIONAL":
            continue
        if review_count < min_reviews or review_count > max_reviews:
            continue
        if rating < 3.5 and review_count > 20:
            continue

        candidates.append({
            "name": name,
            "address": place.get("formatted_address", ""),
            "rating": rating,
            "review_count": review_count,
            "place_id": place.get("place_id", ""),
            "lat": place.get("geometry", {}).get("location", {}).get("lat"),
            "lng": place.get("geometry", {}).get("location", {}).get("lng"),
            "price_level": place.get("price_level"),
            "types": place.get("types", []),
            "primary_type": place.get("primary_type", ""),
        })

    # If more than target, randomly sample down
    if len(candidates) > target_count:
        candidates = random.sample(candidates, target_count)

    return candidates


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2A: Random Selection
# ══════════════════════════════════════════════════════════════════════════

def select_random(candidates: list[dict]) -> dict:
    """Uniform random pick from candidates."""
    return random.choice(candidates)


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2B: LLM Selection
# ══════════════════════════════════════════════════════════════════════════

LLM_SYSTEM_PROMPT = """\
You are the selection judge for BLIND, a non-commercial website that spotlights \
one small, independent, family-owned restaurant every two weeks to help them \
get discovered.

You will receive a list of candidate restaurants (name, address, rating, \
review count, Google Places types). Your job:

1. EXCLUDE any candidate that is NOT a restaurant people go to for a meal. \
   Exclude: nightclubs, adult entertainment venues, strip clubs, bars whose \
   primary purpose is drinking not eating, hookah lounges, smoke shops, \
   convenience stores, gas stations, hotels, or any other non-restaurant venue. \
   Also exclude any chain restaurant that may have slipped through the prefilter.

2. From the remaining candidates, pick ONE that best fits this profile:
   - Most likely to be family-owned or owner-operated (not a franchise, \
     not a corporate concept)
   - Small and independent — the kind of place with a personal story
   - Serves food that reflects the owner's culture or community
   - Would genuinely benefit from free exposure
   - A place you'd tell a friend about

3. Respond with ONLY a JSON object, no markdown, no explanation:
{
  "selected_index": <0-based index of your pick from the input list>,
  "selected_name": "<name>",
  "excluded_indices": [<indices of excluded non-restaurants>],
  "excluded_reasons": {"<index>": "<brief reason>"},
  "reasoning": "<1-2 sentences on why you chose this one>"
}
"""

LLM_PROVIDERS = {
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "model": "gemini-2.5-flash",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-20250514",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
    },
}


def select_llm(candidates: list[dict], llm_api_key: str,
               provider: str = "anthropic") -> tuple[dict, dict]:
    """
    Send candidates to an LLM for judgment.
    Returns (selected_candidate, llm_response_data).
    Falls back to random on any error.
    """
    import requests

    candidate_lines = []
    for i, c in enumerate(candidates):
        line = (
            f"[{i}] {c['name']}\n"
            f"    Address: {c['address']}\n"
            f"    Rating: {c['rating']}★ ({c['review_count']} reviews)\n"
            f"    Types: {', '.join(c['types'])}\n"
            f"    Primary type: {c.get('primary_type', 'N/A')}"
        )
        candidate_lines.append(line)

    user_message = "Here are the candidates. Pick one.\n\n" + "\n\n".join(candidate_lines)

    config = LLM_PROVIDERS.get(provider)
    if not config:
        print(f"  [ERROR] Unknown provider: {provider}. Available: {', '.join(LLM_PROVIDERS.keys())}")
        return random.choice(candidates), {"error": "unknown_provider", "fallback": "random"}

    print(f"\n  Sending {len(candidates)} candidates to {provider} ({config['model']})...")

    try:
        if provider == "gemini":
            model = config["model"]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={llm_api_key}"
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": LLM_SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": user_message}]}],
                    "generationConfig": {
                        "maxOutputTokens": 1024,
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=60,
            )
        elif provider == "anthropic":
            response = requests.post(
                config["url"],
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": llm_api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": config["model"],
                    "max_tokens": 1024,
                    "system": LLM_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_message}],
                },
                timeout=30,
            )
        elif provider == "deepseek":
            response = requests.post(
                config["url"],
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {llm_api_key}",
                },
                json={
                    "model": config["model"],
                    "messages": [
                        {"role": "system", "content": LLM_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": 1024,
                },
                timeout=30,
            )

        if response.status_code != 200:
            print(f"  [WARN] LLM API HTTP {response.status_code}: {response.text[:300]}")
            print(f"  Falling back to random.")
            return random.choice(candidates), {"error": f"http_{response.status_code}", "fallback": "random"}

        data = response.json()

        # Extract text
        if provider == "gemini":
            candidates_resp = data.get("candidates", [{}])
            text = candidates_resp[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        elif provider == "anthropic":
            text = data.get("content", [{}])[0].get("text", "")
        elif provider == "deepseek":
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        print(f"\n  LLM raw response:\n  {text}\n")

        # Parse JSON — be aggressive about finding it in the response
        clean = text.strip()

        # Strip markdown fences (```json ... ``` or ``` ... ```)
        if "```" in clean:
            # Find content between first ``` and last ```
            parts = clean.split("```")
            # parts[0] = before first fence, parts[1] = inside fence, parts[2] = after
            if len(parts) >= 3:
                clean = parts[1]
                # Remove optional language tag (e.g. "json\n")
                if clean.startswith("json"):
                    clean = clean[4:]
                clean = clean.strip()

        # Try direct parse first
        llm_result = None
        try:
            llm_result = json.loads(clean)
        except json.JSONDecodeError:
            pass

        # If that failed, try to find a JSON object in the text with regex
        if llm_result is None:
            import re
            json_match = re.search(r'\{[^{}]*"selected_index"\s*:\s*\d+[^{}]*\}', text, re.DOTALL)
            if json_match:
                try:
                    llm_result = json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

        # If still failed, try to find just the selected_index
        if llm_result is None:
            import re
            idx_match = re.search(r'"selected_index"\s*:\s*(\d+)', text)
            name_match = re.search(r'"selected_name"\s*:\s*"([^"]*)"', text)
            reason_match = re.search(r'"reasoning"\s*:\s*"([^"]*)"', text)
            if idx_match:
                llm_result = {
                    "selected_index": int(idx_match.group(1)),
                    "selected_name": name_match.group(1) if name_match else "N/A",
                    "reasoning": reason_match.group(1) if reason_match else "N/A",
                    "parse_note": "extracted via regex fallback",
                }

        if llm_result is None:
            print(f"  [WARN] Could not extract JSON from LLM response.")
            print(f"  Falling back to random.")
            return random.choice(candidates), {"error": "json_extract_failed", "fallback": "random", "raw_text": text}

        selected_index = llm_result.get("selected_index", 0)

        if 0 <= selected_index < len(candidates):
            pick = candidates[selected_index]
            print(f"  ✦ LLM selected [{selected_index}]: {pick['name']}")
            print(f"  Reasoning: {llm_result.get('reasoning', 'N/A')}")

            if llm_result.get("excluded_indices"):
                print(f"  Excluded {len(llm_result['excluded_indices'])} non-restaurant(s):")
                for idx in llm_result["excluded_indices"]:
                    reason = llm_result.get("excluded_reasons", {}).get(str(idx), "N/A")
                    name = candidates[idx]["name"] if 0 <= idx < len(candidates) else "?"
                    print(f"    [{idx}] {name} — {reason}")

            return pick, llm_result
        else:
            print(f"  [WARN] Invalid index {selected_index}, falling back to random.")
            return random.choice(candidates), {"error": "invalid_index", "fallback": "random", "raw": llm_result}

    except requests.exceptions.Timeout:
        print(f"  [WARN] LLM timed out. Falling back to random.")
        return random.choice(candidates), {"error": "timeout", "fallback": "random"}
    except Exception as e:
        print(f"  [WARN] LLM error: {e}. Falling back to random.")
        return random.choice(candidates), {"error": str(e), "fallback": "random"}


# ══════════════════════════════════════════════════════════════════════════
# Main Pipeline
# ══════════════════════════════════════════════════════════════════════════

def run_selection(city_key: str, api_key: str, mode: str = "random",
                  llm_api_key: str = "", llm_provider: str = "anthropic",
                  max_retries: int = 5) -> dict | None:
    """Full pipeline: drift → geo → prefilter → select."""
    city = CITIES.get(city_key)
    if not city:
        print(f"Unknown city: {city_key}")
        return None

    neighborhoods = city["neighborhoods"]
    min_rev = city["min_reviews"]
    max_rev = city["max_reviews"]

    # Get drift position (handles monthly jumps and within-month drift)
    drift_nbr, drift_lat, drift_lng = get_drift_position(neighborhoods, api_key)

    for attempt in range(max_retries):
        neighborhood = drift_nbr
        print(f"\n[Attempt {attempt + 1}] Searching near: {neighborhood} ({drift_lat:.4f}, {drift_lng:.4f})")

        # Phase 1
        raw_results = search_restaurants_google(neighborhood, api_key, drift_lat, drift_lng)
        print(f"  Found {len(raw_results)} raw results from Google")

        candidates = prefilter_candidates(raw_results, min_rev, max_rev, target_count=10)
        print(f"  {len(candidates)} candidates after prefilter")

        if not candidates:
            print(f"  No candidates, retrying with wider search...")
            # Widen: shift position slightly and retry
            drift_lat += random.uniform(-0.005, 0.005)
            drift_lng += random.uniform(-0.005, 0.005)
            continue

        print(f"\n  Phase 2 candidates:")
        for i, c in enumerate(candidates):
            print(f"    [{i}] {c['name']} — {c['rating']}★ ({c['review_count']} reviews)"
                  f" — {c.get('primary_type', 'N/A')} — {c['address']}")

        # Phase 2
        llm_result = None
        if mode == "llm":
            if not llm_api_key:
                print(f"\n  [ERROR] --mode llm requires --llm-api-key")
                sys.exit(1)
            pick, llm_result = select_llm(candidates, llm_api_key, llm_provider)
        else:
            pick = select_random(candidates)
            print(f"\n  ✦ RANDOM SELECTED: {pick['name']}")

        # Build audit record
        record = {
            "selection_id": hashlib.sha256(
                f"{datetime.now(timezone.utc).isoformat()}{pick['place_id']}".encode()
            ).hexdigest()[:16],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "city": city["display_name"],
            "neighborhood_searched": neighborhood,
            "search_params": {
                "min_reviews": min_rev,
                "max_reviews": max_rev,
                "min_rating": 3.5,
            },
            "selection_mode": mode,
            "raw_result_count": len(raw_results),
            "candidate_count": len(candidates),
            "all_candidates": [
                {
                    "name": c["name"],
                    "address": c["address"],
                    "review_count": c["review_count"],
                    "rating": c["rating"],
                    "types": c["types"],
                    "primary_type": c.get("primary_type", ""),
                }
                for c in candidates
            ],
            "selected": pick,
            "attempt_number": attempt + 1,
        }

        if llm_result:
            record["llm_judgment"] = llm_result
            record["llm_provider"] = llm_provider

        return record

    print(f"\nFailed after {max_retries} attempts.")
    return None


# ── Audit Log & Enrichment ───────────────────────────────────────────────

def save_audit_log(record: dict, log_dir: str = "audit_log"):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{record['timestamp'][:10]}_{record['selection_id']}.json"
    filepath = Path(log_dir) / filename
    with open(filepath, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    print(f"\nAudit log saved: {filepath}")
    return filepath


def enrich_selection(record: dict, api_key: str) -> dict:
    place_id = record["selected"]["place_id"]
    if not place_id:
        return record

    print(f"\nFetching details for: {record['selected']['name']}")
    details = get_place_details(place_id, api_key)

    record["selected"]["phone"] = details.get("formatted_phone_number")
    record["selected"]["website"] = details.get("website")
    record["selected"]["google_maps_url"] = details.get("url")
    record["selected"]["hours"] = details.get("opening_hours", {}).get("weekday_text", [])
    record["selected"]["editorial_summary"] = details.get("editorial_summary", {}).get("overview")

    photos = details.get("photos", [])
    record["selected"]["photo_refs"] = [p.get("photo_reference") for p in photos[:5]]

    return record


def download_place_photo(record: dict, api_key: str, output_dir: str = "data") -> str | None:
    """Download a business photo from Places API. Falls back to Street View."""
    import requests

    sel = record.get("selected", {})
    photo_refs = sel.get("photo_refs", [])

    # Try Places API photo first
    for ref in photo_refs:
        if not ref:
            continue
        url = f"https://places.googleapis.com/v1/{ref}/media"
        params = {"maxHeightPx": 800, "maxWidthPx": 1200, "key": api_key}
        print(f"\nDownloading Places photo: {ref[:60]}...")
        try:
            response = requests.get(url, params=params, timeout=30)
            content_type = response.headers.get("Content-Type", "")
            if response.status_code == 200 and "image" in content_type:
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                filepath = Path(output_dir) / "photo.jpg"
                with open(filepath, "wb") as f:
                    f.write(response.content)
                print(f"  Places photo saved: {filepath}")
                return str(filepath)
            else:
                print(f"  [WARN] Places photo HTTP {response.status_code}, trying next...")
        except Exception as e:
            print(f"  [WARN] Places photo failed: {e}, trying next...")

    # Fall back to Street View
    lat, lng = sel.get("lat"), sel.get("lng")
    if lat is None or lng is None:
        print("  [WARN] No coordinates and no photos, skipping image download.")
        return None

    url = "https://maps.googleapis.com/maps/api/streetview"
    params = {
        "size": "800x600",
        "location": f"{lat},{lng}",
        "heading": "210",
        "pitch": "5",
        "fov": "90",
        "key": api_key,
    }

    print(f"\nFalling back to Street View for {lat},{lng}...")
    try:
        response = requests.get(url, params=params, timeout=30)
        content_type = response.headers.get("Content-Type", "")
        if response.status_code == 200 and "image" in content_type:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            filepath = Path(output_dir) / "photo.jpg"
            with open(filepath, "wb") as f:
                f.write(response.content)
            print(f"  Street View saved: {filepath}")
            return str(filepath)
        else:
            print(f"  [WARN] Street View API HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"  [WARN] Street View download failed: {e}")
        return None


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="BLIND — Restaurant Selection")
    parser.add_argument("--city", default="atlanta")
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_PLACES_API_KEY", ""))
    parser.add_argument("--mode", choices=["random", "llm"], default="random")
    parser.add_argument("--llm-api-key", default=os.environ.get("LLM_API_KEY", ""))
    parser.add_argument("--llm-provider", choices=["gemini", "anthropic", "deepseek"], default="gemini")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pick", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--log-dir", default="audit_log")

    args = parser.parse_args()

    print("=" * 60)
    print("BLIND — Restaurant Selection Script")
    print("=" * 60)

    if args.seed is not None:
        random.seed(args.seed)
        print(f"Random seed: {args.seed}")

    city = CITIES.get(args.city)
    if not city:
        print(f"Unknown city: {args.city}. Available: {', '.join(CITIES.keys())}")
        sys.exit(1)

    print(f"City: {city['display_name']}")
    print(f"Neighborhoods: {len(city['neighborhoods'])}")
    print(f"Review band: {city['min_reviews']}–{city['max_reviews']}")
    print(f"Chain filter: {len(KNOWN_CHAINS)} known chains")
    print(f"Selection mode: {args.mode}")

    if args.dry_run:
        print(f"\n[DRY RUN] Pipeline:")
        print(f"  1. Pick random neighborhood from {len(city['neighborhoods'])} options")
        print(f"  2. Google Places (New) text search for restaurants")
        print(f"  3. Hard prefilter: chains, closed, {city['min_reviews']}–{city['max_reviews']} reviews")
        print(f"  4. Sample down to ~10 candidates")
        if args.mode == "random":
            print(f"  5. Uniform random pick")
        else:
            print(f"  5. Send to {args.llm_provider} LLM — filters non-restaurants,")
            print(f"     selects best family-owned candidate with reasoning")
        print(f"  6. Enrich with place details (phone, website, hours, photos)")
        print(f"  7. Save audit log")
        return

    if not args.api_key:
        print("\n[ERROR] No Google API key. Use --api-key or set GOOGLE_PLACES_API_KEY.")
        sys.exit(1)

    if args.pick:
        record = run_selection(
            city_key=args.city,
            api_key=args.api_key,
            mode=args.mode,
            llm_api_key=args.llm_api_key,
            llm_provider=args.llm_provider,
        )
        if record:
            record = enrich_selection(record, args.api_key)

            sel = record["selected"]
            print(f"\n{'=' * 60}")
            print(f"SELECTION SUMMARY")
            print(f"{'=' * 60}")
            print(f"Name:       {sel['name']}")
            print(f"Address:    {sel['address']}")
            print(f"Rating:     {sel['rating']}★ ({sel['review_count']} reviews)")
            print(f"Types:      {', '.join(sel.get('types', []))}")
            print(f"Primary:    {sel.get('primary_type', 'N/A')}")
            print(f"Phone:      {sel.get('phone', 'N/A')}")
            print(f"Website:    {sel.get('website', 'N/A')}")
            print(f"Maps:       {sel.get('google_maps_url', 'N/A')}")
            if sel.get('editorial_summary'):
                print(f"Summary:    {sel['editorial_summary']}")
            if sel.get('hours'):
                print(f"Hours:")
                for h in sel['hours']:
                    print(f"            {h}")

            print(f"\nMode:         {record['selection_mode']}")
            print(f"Neighborhood: {record['neighborhood_searched']}")
            print(f"Candidates:   {record['candidate_count']}")
            print(f"Selection ID: {record['selection_id']}")

            if record.get("llm_judgment"):
                j = record["llm_judgment"]
                print(f"\nLLM reasoning: {j.get('reasoning', 'N/A')}")
                if j.get("excluded_indices"):
                    print(f"LLM excluded:  {len(j['excluded_indices'])} candidate(s)")

            download_place_photo(record, args.api_key)

            save_audit_log(record, args.log_dir)

            current_path = Path("data") / "current.json"
            with open(current_path, "w") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            print(f"Current pick: {current_path}")
        else:
            print("\nSelection failed.")
            sys.exit(1)
    else:
        print("\nUse --pick to run, or --dry-run to preview.")


if __name__ == "__main__":
    main()