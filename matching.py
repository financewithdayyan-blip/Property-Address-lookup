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

import re
from dataclasses import dataclass
from typing import Optional, Protocol

from rapidfuzz import fuzz

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

# When a search comes back MULTIPLE MATCHES and the input row supplied a
# property_description (legal description, e.g. "LOT 5 BLK 2 SUNSET PARK"),
# each candidate's own legal description (when the county exposes one - see
# "legal_description_field" in county_configs.py) is fuzz-compared against
# it as a second, independent signal to break the tie. token_set_ratio
# (rather than token_sort_ratio) because legal descriptions commonly differ
# in length - a user's shorthand is often a subset of the county's full
# platted description - so word-subset overlap matters more than an exact
# whole-string ratio.
DESC_MATCH_THRESHOLD = 80.0

_DESC_PUNCT_RE = re.compile(r"[^A-Z0-9 ]")
_DESC_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_description(raw: str) -> str:
    if not raw:
        return ""
    text = raw.upper().strip()
    text = _DESC_PUNCT_RE.sub(" ", text)
    return _DESC_WHITESPACE_RE.sub(" ", text).strip()


def _legal_desc_confirms(match: PropertyMatch, property_description_input: str) -> bool:
    """Does this one candidate's own legal description strongly match the
    input row's property_description? Used to confirm/upgrade a single
    already-identified best candidate (unlike the MULTIPLE MATCHES
    cross-check below, which picks a winner out of several) - e.g. a
    name that only clears LOW_CONFIDENCE because the county only kept a
    middle initial ("DOOLEY, DAWN P" vs input "Dooley Dawn Patricia")
    still deserves to be FOUND if the legal description independently
    confirms it's the right property.
    """
    norm_input = _normalize_description(property_description_input)
    norm_found = _normalize_description(match.legal_description)
    if not norm_input or not norm_found:
        return False
    return fuzz.token_set_ratio(norm_input, norm_found) >= DESC_MATCH_THRESHOLD


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
    property_description_input: str = "",
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
        status = "FOUND" if top_result.classification == "MATCH" else "LOW CONFIDENCE"
        if status == "LOW CONFIDENCE" and _legal_desc_confirms(top_match, property_description_input):
            logger.info(
                "  upgraded LOW CONFIDENCE to FOUND via legal description cross-check: "
                "input_desc=%r matched candidate legal=%r (name_score=%.1f)",
                property_description_input, top_match.legal_description, top_result.score,
            )
            status = "FOUND"
        row.match_score = f"{top_result.score:.1f}"
        row.owner_name_found = top_match.owner_name_found
        row.property_address = top_match.property_address
        row.mailing_address = top_match.mailing_address
        row.parcel_id = top_match.parcel_id
        row.source_url = top_match.source_url
        row.status = status
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
    # genuinely ambiguous by name alone. If the input row supplied a
    # property_description, use it as an independent tie-breaker: does
    # exactly one candidate's own legal description match it? If so,
    # that's a strong enough signal to resolve this as FOUND rather than
    # leaving it for manual review. Deliberately conservative - if zero or
    # more than one candidate crosses the threshold, this can't
    # disambiguate any further than the name match already did, so it
    # falls through to MULTIPLE MATCHES unchanged.
    norm_desc_input = _normalize_description(property_description_input)
    if norm_desc_input:
        desc_hits = []
        for result, m in scored:
            norm_desc_found = _normalize_description(m.legal_description)
            if not norm_desc_found:
                continue
            desc_score = fuzz.token_set_ratio(norm_desc_input, norm_desc_found)
            if desc_score >= DESC_MATCH_THRESHOLD:
                desc_hits.append((desc_score, result, m))

        if len(desc_hits) == 1:
            desc_score, result, m = desc_hits[0]
            logger.info(
                "  resolved MULTIPLE MATCHES via legal description cross-check: "
                "input_desc=%r matched candidate legal=%r (desc_score=%.1f, name_score=%.1f)",
                property_description_input, m.legal_description, desc_score, result.score,
            )
            row.match_score = f"{result.score:.1f}"
            row.owner_name_found = m.owner_name_found
            row.property_address = m.property_address
            row.mailing_address = m.mailing_address
            row.parcel_id = m.parcel_id
            row.source_url = m.source_url
            row.status = "FOUND"
            return row
        elif len(desc_hits) > 1:
            logger.info(
                "  legal description cross-check found %d candidates above "
                "threshold (%.0f) - still ambiguous, leaving as MULTIPLE MATCHES",
                len(desc_hits), DESC_MATCH_THRESHOLD,
            )

    row.status = "MULTIPLE MATCHES"
    row.match_score = f"{top_result.score:.1f}"
    row.owner_name_found = top_match.owner_name_found
    row.property_address = top_match.property_address
    row.mailing_address = top_match.mailing_address
    row.parcel_id = top_match.parcel_id
    row.source_url = top_match.source_url
    return row
