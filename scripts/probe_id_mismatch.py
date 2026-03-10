"""
probe_id_mismatch.py — Diagnostic script to surface the PMXT market_id format
vs Gamma token ID format discrepancy that causes 0.0 prices → 1% floor bug.

Run this BEFORE attempting any fix to confirm the root cause.

Usage:
    python probe_id_mismatch.py

    # Or target a specific date:
    python probe_id_mismatch.py --date 2026-02-25

    # Or point at a specific PMXT parquet file directly:
    python probe_id_mismatch.py --url https://r2.pmxt.dev/polymarket_orderbook_2026-02-25T12.parquet

What this script does:
    1. Fetches a PMXT hourly Parquet file and prints a sample of raw market_id values.
    2. Fetches Gamma weather markets for the same date and prints their token IDs.
    3. Runs _normalize_id() on both sides and checks for overlap.
    4. Reports exactly how many IDs matched vs. missed, and shows side-by-side
       examples of the raw format on each side so you can see the mismatch at a glance.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Make the script runnable from the repo root without installing the package.
# Adjust this if your package layout differs.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

import httpx
import polars as pl


# ---------------------------------------------------------------------------
# Minimal inline copies of helpers so this script is self-contained
# (no weatheredge imports required just to run the probe).
# ---------------------------------------------------------------------------

PMXT_BASE = "https://r2.pmxt.dev"
GAMMA_API = "https://gamma-api.polymarket.com"


def _download_parquet(url: str, timeout: int = 120) -> pl.DataFrame:
    print(f"  [http] GET {url}")
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return pl.read_parquet(io.BytesIO(resp.content))


def _normalize_id(val: str | int) -> str:
    """Exact copy of pmxt_fetcher._normalize_id for comparison."""
    if not val:
        return ""
    try:
        if isinstance(val, str):
            val = val.strip()
            ival = int(val, 16) if val.lower().startswith("0x") else int(val)
        else:
            ival = int(val)
        return f"0x{ival:064x}".lower()
    except (ValueError, TypeError):
        s = str(val).lower()
        return s if s.startswith("0x") else "0x" + s


def fetch_pmxt_sample(target_date: date, hour: int = 12) -> pl.DataFrame:
    """Download one PMXT hourly file and return raw DataFrame."""
    url = f"{PMXT_BASE}/polymarket_orderbook_{target_date.isoformat()}T{hour:02d}.parquet"
    try:
        return _download_parquet(url)
    except Exception as e:
        print(f"  [warn] Hour {hour:02d} failed: {e}")
        return pl.DataFrame()


def fetch_gamma_markets(target_date: date) -> list[dict]:
    """
    Fetch Gamma markets that resolve on target_date.
    Returns raw market dicts with clobTokenIds.
    """
    date_str = target_date.isoformat()
    url = (
        f"{GAMMA_API}/markets"
        f"?end_date_min={date_str}T00:00:00Z"
        f"&end_date_max={date_str}T23:59:59Z"
        f"&limit=100"
    )
    print(f"  [http] GET {url}")
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        # Gamma returns list or dict with "markets" key depending on endpoint version
        if isinstance(data, list):
            return data
        return data.get("markets", data.get("data", []))
    except Exception as e:
        print(f"  [warn] Gamma fetch failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

SEP = "-" * 72


def _truncate(s: str, n: int = 30) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _fmt_id_table(label: str, ids: list[str], normed: list[str]) -> None:
    print(f"\n  {'RAW ' + label:<42}  NORMALIZED")
    print(f"  {'-'*42}  {'-'*42}")
    for raw, norm in zip(ids[:8], normed[:8]):
        print(f"  {raw:<42}  {norm}")
    if len(ids) > 8:
        print(f"  ... ({len(ids) - 8} more)")


# ---------------------------------------------------------------------------
# Main probe logic
# ---------------------------------------------------------------------------

def probe(target_date: date, pmxt_url: str | None = None) -> None:
    print(f"\n{SEP}")
    print(f"  PMXT ↔ Gamma ID Format Probe  |  date={target_date}")
    print(SEP)

    # ------------------------------------------------------------------
    # Step 1: Download PMXT and sample raw market_id values
    # ------------------------------------------------------------------
    print("\n[1/4] Fetching PMXT orderbook snapshot …")

    if pmxt_url:
        try:
            df = _download_parquet(pmxt_url)
        except Exception as e:
            print(f"  ERROR: {e}")
            return
    else:
        # Try hour 12, fall back to 11 and 13
        df = pl.DataFrame()
        for h in [12, 11, 13, 10, 14]:
            df = fetch_pmxt_sample(target_date, h)
            if not df.is_empty():
                print(f"  Using hour {h:02d} file ({len(df):,} rows)")
                break

    if df.is_empty():
        print("  ERROR: Could not fetch any PMXT data for this date. Try --url with a known good URL.")
        return

    print(f"\n  PMXT schema ({len(df.columns)} columns, {len(df):,} rows):")
    for col, dtype in zip(df.columns, df.dtypes):
        print(f"    {col:<35} {dtype}")

    # Deduplicated raw market_ids from PMXT
    pmxt_raw_ids: list[str] = (
        df.select("market_id")
        .unique()
        .get_column("market_id")
        .cast(pl.Utf8)
        .to_list()
    )
    pmxt_normed = [_normalize_id(i) for i in pmxt_raw_ids]

    print(f"\n  {len(pmxt_raw_ids)} unique market_ids in PMXT file")
    _fmt_id_table("PMXT market_id", pmxt_raw_ids, pmxt_normed)

    # ------------------------------------------------------------------
    # Step 2: Check what update_types exist
    # ------------------------------------------------------------------
    print("\n[2/4] PMXT update_type distribution …")
    if "update_type" in df.columns:
        counts = (
            df.group_by("update_type")
            .agg(pl.len().alias("count"))
            .sort("count", descending=True)
        )
        for row in counts.iter_rows(named=True):
            print(f"    {row['update_type']:<30} {row['count']:>8,} rows")
    else:
        print("  WARNING: 'update_type' column not found — schema may differ from expected")

    # ------------------------------------------------------------------
    # Step 3: Fetch Gamma markets for the same date
    # ------------------------------------------------------------------
    print("\n[3/4] Fetching Gamma markets …")
    gamma_markets = fetch_gamma_markets(target_date)
    print(f"  Gamma returned {len(gamma_markets)} markets")

    if not gamma_markets:
        print("  Skipping ID comparison (no Gamma data)")
        return

    # Collect all token IDs Gamma provides
    gamma_token_ids: list[str] = []
    gamma_condition_ids: list[str] = []

    for m in gamma_markets:
        # clobTokenIds is a JSON-encoded list e.g. '["123456", "654321"]'
        raw_tokens = m.get("clobTokenIds") or m.get("clob_token_ids") or "[]"
        if isinstance(raw_tokens, str):
            try:
                tokens = json.loads(raw_tokens)
            except json.JSONDecodeError:
                tokens = [raw_tokens]
        else:
            tokens = raw_tokens or []

        for t in tokens:
            if t:
                gamma_token_ids.append(str(t))

        cid = m.get("conditionId") or m.get("condition_id") or ""
        if cid:
            gamma_condition_ids.append(str(cid))

    gamma_token_normed = [_normalize_id(t) for t in gamma_token_ids]
    gamma_cid_normed = [_normalize_id(c) for c in gamma_condition_ids]

    print(f"\n  Gamma token IDs   : {len(gamma_token_ids)} total")
    _fmt_id_table("Gamma clobTokenId", gamma_token_ids, gamma_token_normed)

    print(f"\n  Gamma conditionIds: {len(gamma_condition_ids)} total")
    _fmt_id_table("Gamma conditionId", gamma_condition_ids, gamma_cid_normed)

    # ------------------------------------------------------------------
    # Step 4: Intersection analysis
    # ------------------------------------------------------------------
    print(f"\n{SEP}")
    print("[4/4] Intersection analysis …")
    print(SEP)

    pmxt_normed_set = set(pmxt_normed)
    gamma_token_set = set(gamma_token_normed)
    gamma_cid_set = set(gamma_cid_normed)

    token_overlap = pmxt_normed_set & gamma_token_set
    cid_overlap = pmxt_normed_set & gamma_cid_set

    print(f"\n  PMXT normalized IDs          : {len(pmxt_normed_set):>6}")
    print(f"  Gamma token IDs (normalized) : {len(gamma_token_set):>6}")
    print(f"  Gamma conditionIds (normed)  : {len(gamma_cid_set):>6}")
    print()
    print(f"  ✓  Token ID overlap          : {len(token_overlap):>6}  matches")
    print(f"  ✓  ConditionId overlap        : {len(cid_overlap):>6}  matches")

    missed_tokens = gamma_token_set - pmxt_normed_set
    missed_cids = gamma_cid_set - pmxt_normed_set

    print()
    print(f"  ✗  Token IDs with NO match   : {len(missed_tokens):>6}  (these become 0.0 price → 1% floor)")
    print(f"  ✗  ConditionIds with NO match : {len(missed_cids):>6}")

    if token_overlap:
        print("\n  Sample MATCHED token IDs (normalized):")
        for nid in sorted(token_overlap)[:5]:
            # Find original forms
            gamma_orig = next((g for g, n in zip(gamma_token_ids, gamma_token_normed) if n == nid), "?")
            pmxt_orig = next((p for p, n in zip(pmxt_raw_ids, pmxt_normed) if n == nid), "?")
            print(f"    Gamma raw : {gamma_orig}")
            print(f"    PMXT  raw : {pmxt_orig}")
            print(f"    Normed    : {nid}")
            print()

    if missed_tokens:
        print("  Sample MISSED token IDs (normalized — no PMXT counterpart):")
        for nid in sorted(missed_tokens)[:5]:
            gamma_orig = next((g for g, n in zip(gamma_token_ids, gamma_token_normed) if n == nid), "?")
            print(f"    Gamma raw : {gamma_orig}")
            print(f"    Normed    : {nid}")
        print()

    # ------------------------------------------------------------------
    # Diagnosis summary
    # ------------------------------------------------------------------
    print(SEP)
    print("DIAGNOSIS")
    print(SEP)

    if len(token_overlap) == 0 and len(cid_overlap) == 0:
        print("""
  ❌ CONFIRMED ROOT CAUSE: Zero ID overlap between PMXT and Gamma.
     _normalize_id() is NOT producing matching keys.

  Likely causes:
    A) PMXT stores token IDs in a different numeric representation
       (e.g. decimal strings, or shorter/longer hex padding).
    B) PMXT uses a completely different ID scheme (e.g. internal
       market hash unrelated to Polymarket conditionId/tokenId).

  Next step:
    Look at the raw PMXT market_id values printed above in [1/4].
    Compare them to the raw Gamma token IDs in [3/4].
    If PMXT IDs are shorter hex strings, adjust _normalize_id() to
    truncate (or not pad) to match the actual PMXT length.
""")
    elif len(token_overlap) < len(gamma_token_set) * 0.5:
        print(f"""
  ⚠️  PARTIAL MATCH: Only {len(token_overlap)}/{len(gamma_token_set)} token IDs matched.
     Some enrichment will work but many markets will still fall back to
     Gamma post-resolution prices → 1% floor.

  Likely cause: Mixed ID formats in Gamma (some decimal, some hex).
  Check the 'MISSED' sample above to see which format fails.
""")
    else:
        print(f"""
  ✅ Good overlap: {len(token_overlap)}/{len(gamma_token_set)} token IDs matched.
     The ID normalization appears correct. The 1% issue is likely caused
     by a different problem — check the timestamp filtering logic in
     fetch_orderbook_at_timestamp(), or whether book_snapshot rows exist
     for the target hour at all (see update_type counts above).
""")

    print(SEP)
    print("Done.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Probe PMXT vs Gamma ID format mismatch")
    parser.add_argument(
        "--date",
        default="2026-02-25",
        help="Target date (YYYY-MM-DD). Default: 2026-02-25",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Override PMXT parquet URL (skips date-based URL construction)",
    )
    args = parser.parse_args()

    target = date.fromisoformat(args.date)
    probe(target, pmxt_url=args.url)