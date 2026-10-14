"""List prices from Anthropic's pricing page, and what a reply cost."""
import json, re, time, urllib.request
from pathlib import Path

from roost.core import config


PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
PRICES = Path.home() / ".cache" / "roost-prices.json"
PRICES_MAX_AGE = 86400  # fetch Anthropic's pricing page at most once a day
PRICES_RETRY = 3600     # and after a failed fetch, try again only after an hour


def model_ids(cell):
    """Model ids for a pricing table's first cell: 'Claude Opus 5 / Claude Opus 4.8' gives
    claude-opus-5 and claude-opus-4-8. Notes in parentheses are dropped."""
    out = []
    for part in cell.split(" / "):
        name = part.split("(")[0].strip()
        if name.startswith("Claude "):
            out.append(re.sub(r"[ .]+", "-", name.lower()))
    return out


def dollars(cell):
    m = re.match(r"\$([\d.,]+) / MTok", cell.strip())
    return float(m.group(1).replace(",", "")) if m else None


def parse_prices(md):
    """The model and fast mode tables from Anthropic's pricing page (Markdown), in dollars per
    million tokens. standard: model -> [[prompt limit or None, [input, 5m write, 1h write, cache
    read, output]], …]; fast: model -> [input, output]. Raises ValueError if there's no model table."""
    std, fast, section = {}, {}, ""
    for line in md.splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip().lower()
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        nums = [dollars(c) for c in cells[1:]]
        if not nums or None in nums:
            continue
        if section == "model pricing" and len(nums) == 5:
            tier = re.search(r"prompts up to ([\d,]+) tokens", cells[0])
            for mid in model_ids(cells[0]):
                std.setdefault(mid, []).append([int(tier.group(1).replace(",", "")) if tier else None, nums])
        elif section == "fast mode pricing" and len(nums) == 2:
            for mid in model_ids(cells[0]):
                fast[mid] = nums
    if not std:
        raise ValueError("the pricing page has no model table roost can read")
    return {"standard": std, "fast": fast}


def prices(now=None):
    """Anthropic's list prices: fetched from its pricing page at most once a day, else the copy
    fetched last. {standard, fast, fetched}, or None when roost has never fetched them. A failed
    fetch is noted in the cache (failed_at), so the next try waits an hour."""
    now = now or time.time()
    try:
        cache = json.loads(PRICES.read_text())
    except (OSError, ValueError):
        cache = {}
    have = cache if cache.get("standard") else None
    if have and now - cache.get("fetched", 0) < PRICES_MAX_AGE or now - cache.get("failed_at", -PRICES_RETRY) < PRICES_RETRY:
        return have
    try:
        req = urllib.request.Request(PRICING_URL, headers={"User-Agent": f"roost/{config.VERSION}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cache = have = dict(parse_prices(r.read().decode("utf-8", "replace")), fetched=now)
    except (OSError, ValueError) as e:
        config.log(f"prices: couldn't fetch {PRICING_URL}: {e}")
        cache = dict(cache, failed_at=now)
    try:
        PRICES.parent.mkdir(parents=True, exist_ok=True)
        PRICES.write_text(json.dumps(cache))
    except OSError as e:
        config.log(f"prices: couldn't write {PRICES}: {e}")
    return have


def reply_cost(model, u, table):
    """One reply's gross cost in dollars at list prices, or None when the table lacks its model."""
    key = re.sub(r"\[.*\]$", "", model or "")
    key = re.sub(r"^(?:[a-z]{2,4}\.)?anthropic\.|-v\d+(?::\d+)?$", "", key)  # a Bedrock id: us.anthropic.claude-…-v1:0
    key = re.sub(r"-\d{8}$", "", key)
    tiers = ((table or {}).get("standard") or {}).get(key)
    if not tiers:
        return None
    made = u.get("cache_creation") or {}
    w5 = made.get("ephemeral_5m_input_tokens", 0) if made else u.get("cache_creation_input_tokens", 0)
    w1 = made.get("ephemeral_1h_input_tokens", 0)
    inp, read, out = u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0)
    prompt = inp + read + w5 + w1
    p = next((n for limit, n in tiers if limit is None or prompt <= limit), tiers[-1][1])
    in_x = out_x = 1
    if u.get("speed") == "fast":  # fast mode's premium; the cache multipliers apply on top of it
        f = (table.get("fast") or {}).get(key)
        if not f:
            return None
        in_x, out_x = f[0] / p[0], f[1] / p[4]
    geo = 1.1 if u.get("inference_geo") == "us" else 1  # US-only inference
    return geo * (in_x * (inp * p[0] + w5 * p[1] + w1 * p[2] + read * p[3]) + out_x * out * p[4]) / 1e6
