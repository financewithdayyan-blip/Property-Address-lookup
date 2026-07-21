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


def _find_legal_desc_match(scored, property_description_input: str, logger):
    """Does exactly one scored candidate's own legal description strongly
    match the input row's property_description? If so, return its
    (MatchResult, PropertyMatch) pair - an independent, often very strong
    signal that should be able to resolve a row even when the NAME score
    alone can't, which matters most exactly when the name score can't be
    trusted: a lead list that concatenates several co-owners' full names
    together with no separator (e.g. "KNAPIK JOHN RICHARD RIZZO MICHAEL
    RIZZO ERIC RIZZO RENEE" for four people) will always score the real
    single-owner match poorly against the whole messy input string,
    however good the search was at finding it - found live, this landed
    well below NO_MATCH territory even though the right record was right
    there in the results. So this checks EVERY scored candidate
    (regardless of its name-score classification), not just "plausible"
    ones. Deliberately conservative - if zero or more than one candidate
    crosses the threshold, this can't disambiguate any further, so the
    caller falls back to ordinary name-score-based classification.
    """
    norm_desc_input = _normalize_description(property_description_input)
    if not norm_desc_input:
        return None

    hits = []
    for result, m in scored:
        norm_desc_found = _normalize_description(m.legal_description)
        if not norm_desc_found:
            continue
        desc_score = fuzz.token_set_ratio(norm_desc_input, norm_desc_found)
        if desc_score >= DESC_MATCH_THRESHOLD:
            hits.append((desc_score, result, m))

    if len(hits) == 1:
        desc_score, result, m = hits[0]
        logger.info(
            "  resolved via legal description cross-check (name_score=%.1f, desc_score=%.1f): "
            "input_desc=%r matched candidate legal=%r",
            result.score, desc_score, property_description_input, m.legal_description,
        )
        return result, m
    if len(hits) > 1:
        logger.info(
            "  legal description cross-check found %d candidates above "
            "threshold (%.0f) - can't disambiguate further",
            len(hits), DESC_MATCH_THRESHOLD,
        )
    return None


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

    # Legal-description cross-check runs BEFORE name-score classification,
    # and covers every candidate regardless of name score - see
    # _find_legal_desc_match()'s docstring for why a hard name-score
    # cutoff shouldn't block this. Only resolves things when there's
    # exactly one strong hit; otherwise falls through unchanged.
    desc_hit = _find_legal_desc_match(scored, property_description_input, logger)
    if desc_hit is not None:
        result, m = desc_hit
        row.match_score = f"{result.score:.1f}"
        row.owner_name_found = m.owner_name_found
        row.property_address = m.property_address
        row.mailing_address = m.mailing_address
        row.parcel_id = m.parcel_id
        row.source_url = m.source_url
        row.status = "FOUND"
        return row

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
    # genuinely ambiguous by name alone, and the legal-description check
    # above already had its shot at resolving this. Leave for manual review.
    row.status = "MULTIPLE MATCHES"
    row.match_score = f"{top_result.score:.1f}"
    row.owner_name_found = top_match.owner_name_found
    row.property_address = top_match.property_address
    row.mailing_address = top_match.mailing_address
    row.parcel_id = top_match.parcel_id
    row.source_url = top_match.source_url
    return row
