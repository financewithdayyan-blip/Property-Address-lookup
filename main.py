"""
main.py - Property Address Lookup Automation entry point.

Reads a CSV of pre-foreclosure leads (owner_name, county, state), looks up
each owner on the matching county property appraiser site, fuzzy-matches
the scraped owner name against the input name, and writes a clean,
skip-tracing-ready CSV - one row at a time, so a mid-run crash never loses
completed work.

Usage:
    python main.py --input leads.csv --output results.csv
    python main.py --input leads.csv --output results.csv --county "Duval FL" --delay 3
    python main.py --input leads.csv --output results.csv --resume
    python main.py --inspect-county "Duval FL"

See README.md for full docs and "Adding a New County" instructions.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import traceback
from typing import Dict, Optional, Set, Tuple

import pandas as pd

from county_configs import get_county_config, normalize_county, normalize_state
from logger import setup_logger
from matching import OUTPUT_FIELDS, OutputRow, classify_and_build_row
import scraper
from scraper import RateLimiter, ScraperError, make_delay_bounds, new_session

MULTI_MATCH_SUFFIX = "_multiple_matches.csv"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Property Address Lookup Automation")
    p.add_argument("--input", help="Input CSV with owner_name, county, state columns")
    p.add_argument("--output", help="Output CSV path")
    p.add_argument(
        "--county",
        default=None,
        help='Only process rows matching this county, e.g. "Duval FL". '
        "Omit to process every county present in the input file.",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Center of the randomized per-request delay in seconds (default 3; "
        "actual delay is randomized within roughly +/-33%% of this).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip input rows that already appear in the output CSV (for resuming a crashed run).",
    )
    p.add_argument(
        "--inspect-county",
        default=None,
        metavar='"County ST"',
        help="Diagnostic mode: GET the county's search page and print its form "
        "fields (no CSV processing). Use this to verify/fix an unverified "
        "county config.",
    )
    p.add_argument(
        "--inspect-sample-name",
        default=None,
        help="Used with --inspect-county: also run one live search for this "
        "name and print how many rows the current row_selector/fields "
        "config extracted, to help debug parsing.",
    )
    p.add_argument("--verbose", action="store_true", help="Verbose (DEBUG) console logging.")
    return p.parse_args(argv)


def parse_county_filter(value: str) -> Tuple[str, str]:
    """'Duval FL' -> ('duval', 'FL'). State is assumed to be the last token."""
    parts = value.strip().rsplit(" ", 1)
    if len(parts) != 2:
        raise ValueError(
            f'--county value {value!r} must be "<County> <ST>", e.g. "Duval FL"'
        )
    county, state = parts
    return normalize_county(county), normalize_state(state) or state.strip().upper()


def load_input_rows(input_path: str) -> pd.DataFrame:
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    required = {"owner_name", "county", "state"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Input CSV is missing required column(s): {sorted(missing)}. "
            f"Found columns: {list(df.columns)}"
        )
    for col in ("owner_name", "county", "state"):
        df[col] = df[col].astype(str).str.strip()
    return df


def load_resume_keys(output_path: str) -> Set[Tuple[str, str, str]]:
    keys: Set[Tuple[str, str, str]] = set()
    if not os.path.exists(output_path):
        return keys
    with open(output_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add(
                (
                    (row.get("owner_name_input") or "").strip(),
                    (row.get("county") or "").strip(),
                    (row.get("state") or "").strip(),
                )
            )
    return keys


def run_inspect_county(name: str, sample_name: Optional[str], verbose: bool) -> int:
    county, state = parse_county_filter(name)
    config = get_county_config(county, state)
    if config is None:
        print(f'No county config found for "{name}". Add one in county_configs.py first.')
        return 1

    print(f"=== Inspecting {config.get('display_name', name)} ===")
    print(f"search_type: {config.get('search_type')}")
    print(f"verified: {config.get('verified')}")
    if not config.get("verified", True):
        print(f"verification_note: {config.get('verification_note')}")
    print()

    if config.get("search_type") == "arcgis_query":
        arc = config["arcgis"]
        print(f"ArcGIS query_url: {arc['query_url']}")
        print(f"owner_field: {arc['owner_field']}")
        print(f"out_fields: {arc['out_fields']}")
    else:
        print(scraper.inspect_search_page(config))

    if sample_name:
        print()
        print(f"=== Running a live search for {sample_name!r} with the CURRENT config ===")
        session = new_session()
        rate_limiter = RateLimiter(*make_delay_bounds(3.0))
        try:
            matches = scraper.search(config, sample_name, session, rate_limiter)
        except Exception as exc:  # noqa: BLE001 - diagnostic mode, show everything
            print(f"Search failed: {exc}")
            traceback.print_exc()
            return 1
        print(f"Parsed {len(matches)} result row(s) using current row_selector/fields.")
        for m in matches[:10]:
            print(f"  - {m}")
        if not matches:
            print(
                "0 rows parsed. If you know the search actually returned results, "
                "row_selector or fields in county_configs.py are likely wrong - "
                "view the page source of a manual search in your browser and "
                "compare against the config."
            )
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.inspect_county:
        return run_inspect_county(args.inspect_county, args.inspect_sample_name, args.verbose)

    if not args.input or not args.output:
        print("--input and --output are required (unless using --inspect-county).")
        return 2

    logger = setup_logger(args.output, verbose=args.verbose)
    logger.info("Starting run: input=%s output=%s county=%s delay=%s resume=%s",
                args.input, args.output, args.county, args.delay, args.resume)

    try:
        df = load_input_rows(args.input)
    except Exception as exc:
        logger.error("Failed to read input CSV: %s", exc)
        return 2

    county_filter = None
    if args.county:
        try:
            county_filter = parse_county_filter(args.county)
        except ValueError as exc:
            logger.error(str(exc))
            return 2

    resume_keys = load_resume_keys(args.output) if args.resume else set()
    if args.resume:
        logger.info("Resume mode: %d already-processed row(s) will be skipped", len(resume_keys))

    file_exists = os.path.exists(args.output)
    write_header = not (args.resume and file_exists)
    out_mode = "a" if (args.resume and file_exists) else "w"

    multi_match_path = _multi_match_path(args.output)
    multi_exists = os.path.exists(multi_match_path)
    multi_mode = "a" if (args.resume and multi_exists) else "w"
    multi_write_header = not (args.resume and multi_exists)

    delay_bounds = make_delay_bounds(args.delay)
    sessions: Dict[str, requests_session_bundle] = {}

    processed = 0
    skipped_filtered = 0
    skipped_resumed = 0
    status_counts: Dict[str, int] = {}

    with open(args.output, out_mode, newline="", encoding="utf-8") as out_f, \
         open(multi_match_path, multi_mode, newline="", encoding="utf-8") as multi_f:

        writer = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS)
        if write_header:
            writer.writeheader()
            out_f.flush()

        multi_writer = csv.DictWriter(
            multi_f,
            fieldnames=[
                "owner_name_input", "county", "state", "owner_name_found",
                "property_address", "mailing_address", "parcel_id",
                "match_score", "source_url",
            ],
        )
        if multi_write_header:
            multi_writer.writeheader()
            multi_f.flush()

        for _, csv_row in df.iterrows():
            owner_name_input = csv_row["owner_name"]
            county = csv_row["county"]
            state = csv_row["state"]

            if county_filter is not None:
                row_key = (normalize_county(county), normalize_state(state) or state.strip().upper())
                if row_key != county_filter:
                    skipped_filtered += 1
                    continue

            resume_key = (owner_name_input, county, state)
            if args.resume and resume_key in resume_keys:
                skipped_resumed += 1
                continue

            config = get_county_config(county, state)
            if config is None:
                logger.error(
                    'No county config for "%s, %s" (owner=%r) - add one in '
                    "county_configs.py. Marking ERROR.",
                    county, state, owner_name_input,
                )
                row = OutputRow(owner_name_input, county, state, status="ERROR")
                writer.writerow(row.as_dict())
                out_f.flush()
                status_counts["ERROR"] = status_counts.get("ERROR", 0) + 1
                processed += 1
                continue

            bundle = sessions.get(config["display_name"])
            if bundle is None:
                bundle = requests_session_bundle(new_session(), RateLimiter(*delay_bounds))
                sessions[config["display_name"]] = bundle

            logger.info('Searching %s for owner=%r (county=%s, state=%s)',
                        config["display_name"], owner_name_input, county, state)
            try:
                matches = scraper.search(config, owner_name_input, bundle.session, bundle.rate_limiter)
            except ScraperError as exc:
                logger.error("Scrape error for %r in %s: %s", owner_name_input, config["display_name"], exc)
                row = OutputRow(owner_name_input, county, state, status="ERROR")
                writer.writerow(row.as_dict())
                out_f.flush()
                status_counts["ERROR"] = status_counts.get("ERROR", 0) + 1
                processed += 1
                continue
            except Exception as exc:  # noqa: BLE001 - never let one bad row kill the run
                logger.error(
                    "Unexpected error for %r in %s: %s\n%s",
                    owner_name_input, config["display_name"], exc, traceback.format_exc(),
                )
                row = OutputRow(owner_name_input, county, state, status="ERROR")
                writer.writerow(row.as_dict())
                out_f.flush()
                status_counts["ERROR"] = status_counts.get("ERROR", 0) + 1
                processed += 1
                continue

            row = classify_and_build_row(
                owner_name_input, county, state, matches, logger, multi_writer
            )
            writer.writerow(row.as_dict())
            out_f.flush()
            multi_f.flush()
            status_counts[row.status] = status_counts.get(row.status, 0) + 1
            processed += 1

    logger.info(
        "Run complete. processed=%d skipped_filtered=%d skipped_resumed=%d statuses=%s",
        processed, skipped_filtered, skipped_resumed, status_counts,
    )
    logger.info("Output: %s", args.output)
    if any(status_counts.get(s, 0) for s in ("MULTIPLE MATCHES",)):
        logger.info("Full multiple-match candidate list: %s", multi_match_path)

    print(f"Done. Processed {processed} row(s). Statuses: {status_counts}")
    print(f"Output CSV: {args.output}")
    print(f"Log: {os.path.join(os.path.dirname(os.path.abspath(args.output)) or '.', 'run.log')}")
    return 0


def _multi_match_path(output_path: str) -> str:
    base, ext = os.path.splitext(output_path)
    return f"{base}{MULTI_MATCH_SUFFIX}"


class requests_session_bundle:  # noqa: N801 - simple internal pairing, not worth a whole module
    __slots__ = ("session", "rate_limiter")

    def __init__(self, session, rate_limiter):
        self.session = session
        self.rate_limiter = rate_limiter


if __name__ == "__main__":
    sys.exit(main())
