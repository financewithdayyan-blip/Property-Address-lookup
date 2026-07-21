"""
matching.py

Shared classification logic: turns a list of scraped PropertyMatch
candidates plus the input owner name into a single OutputRow decision
(FOUND / LOW CONFIDENCE / MULTIPLE MATCHES / NOT FOUND).

Used by both main.py (the CLI, writing to CSV) and worker.py (the web
backend, writing to Postgres) - kept here so the two entry points can never
drift into classifying the same data differently. Neither entry point's
I/O leaks into this module: classify_and_build_row() takes a duck-typed
`multi_match_writer` with just a `.writerow(dict)` method, so a
csv.DictWriter and a Postgres-backed adapter both work unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import name_matcher
from scraper import PropertyMatch

OUTPUT_FIELDS = [
    "owner_name_input",
    "owner_name_found",
    "property_address",
    "mailing_address",
    "parcel_id",
    "county",
    "state",
    "status",
    "match_score",
    "source_url",
]

MAX_MATCHES_TO_LOG = 25

# A raw search can return many candidates that merely share a surname token
# (e.g. every "SMITH, X" for a search anchored on "SMITH") without being
# real matches - token_sort_ratio gives those a real but moderate score
# just from the shared token. So "is this ambiguous" is decided by the gap
# between the best and second-best score, not by counting how many clear
# LOW_CONFIDENCE_THRESHOLD. A clear winner (>=MATCH_THRESHOLD, and far
# enough ahead of the runner-up) is auto-FOUND; anything closer is left as
# MULTIPLE MATCHES for a human to review.
CLEAR_WINNER_MARGIN = 15.0


@dataclass
class OutputRow:
    owner_name_input: str
    county: str
    state: str
    owner_name_found: str = ""
    property_address: str = ""
    mailing_address: str = ""
    parcel_id: str = ""
    status: str = ""
    match_score: str = ""
    source_url: str = ""

    def as_dict(self) -> dict:
        return {
            "owner_name_input": self.owner_name_input,
            "owner_name_found": self.owner_name_found,
            "property_address": self.property_address,
            "mailing_address": self.mailing_address,
            "parcel_id": self.parcel_id,
            "county": self.county,
            "state": self.state,
            "status": self.status,
            "match_score": self.match_score,
            "source_url": self.source_url,
        }


class MultiMatchWriter(Protocol):
    def writerow(self, row: dict) -> object: ...


def classify_and_build_row(
    owner_name_input: str,
    county: str,
    state: str,
    matches: list[PropertyMatch],
    logger,
    multi_match_writer: Optional[MultiMatchWriter],
) -> OutputRow:
    row = OutputRow(owner_name_input=owner_name_input, county=county, state=state)

    if len(matches) == 0:
        row.status = "NOT FOUND"
        return row

    scored = [
        (name_matcher.match_names(owner_name_input, m.owner_name_found), m)
        for m in matches
    ]

    if len(matches) > 1:
        # Log/record every raw candidate (up to the cap) for audit
        # regardless of how the status below ends up being decided - a
        # search can legitimately return many raw rows (e.g. a broad
        # surname-substring query) even when only one is a real match.
        for result, m in scored[:MAX_MATCHES_TO_LOG]:
            logger.info(
                "  candidate: found_name=%r address=%r parcel=%r score=%.1f",
                m.owner_name_found, m.property_address, m.parcel_id, result.score,
            )
            if multi_match_writer is not None:
                multi_match_writer.writerow(
                    {
                        "owner_name_input": owner_name_input,
                        "county": county,
                        "state": state,
                        "owner_name_found": m.owner_name_found,
                        "property_address": m.property_address,
                        "mailing_address": m.mailing_address,
                        "parcel_id": m.parcel_id,
                        "match_score": f"{result.score:.1f}",
                        "source_url": m.source_url,
                    }
                )
        if len(matches) > MAX_MATCHES_TO_LOG:
            logger.info(
                "  ...%d additional candidates not logged (capped at %d)",
                len(matches) - MAX_MATCHES_TO_LOG, MAX_MATCHES_TO_LOG,
            )

    # Status is decided by the gap between the best and second-best score,
    # not by how many raw rows came back or how many cross a fixed bar -
    # a broad surname-substring search can return dozens of "SMITH, X"
    # candidates that each score >=60 against an "X SMITH" input purely
    # from sharing the surname token, without being real matches. A clear
    # winner (nothing else close behind it) is auto-resolved; a genuine
    # toss-up is left for a human to review.
    ranked = sorted(scored, key=lambda t: t[0].score, reverse=True)
    top_result, top_match = ranked[0]
    second_score = ranked[1][0].score if len(ranked) > 1 else 0.0

    if top_result.classification != "NO_MATCH" and (top_result.score - second_score) >= CLEAR_WINNER_MARGIN:
        row.match_score = f"{top_result.score:.1f}"
        row.owner_name_found = top_match.owner_name_found
        row.property_address = top_match.property_address
        row.mailing_address = top_match.mailing_address
        row.parcel_id = top_match.parcel_id
        row.source_url = top_match.source_url
        row.status = "FOUND" if top_result.classification == "MATCH" else "LOW CONFIDENCE"
        return row

    if top_result.classification == "NO_MATCH":
        # Nothing plausible at all, however many raw rows came back - don't
        # surface a stranger's address into a skip-tracing/CRM pipeline.
        row.status = "NOT FOUND"
        if len(matches) == 1:
            logger.info(
                "  single result rejected on name mismatch: input=%r found=%r score=%.1f",
                owner_name_input, matches[0].owner_name_found, top_result.score,
            )
        return row

    # Best candidate is plausible but not clearly ahead of the runner-up -
    # genuinely ambiguous.
    row.status = "MULTIPLE MATCHES"
    row.match_score = f"{top_result.score:.1f}"
    row.owner_name_found = top_match.owner_name_found
    row.property_address = top_match.property_address
    row.mailing_address = top_match.mailing_address
    row.parcel_id = top_match.parcel_id
    row.source_url = top_match.source_url
    return row
