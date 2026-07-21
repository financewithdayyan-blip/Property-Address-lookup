"""
county_configs.py

COUNTY_CONFIGS is the directory scraper.py and main.py read from. Each key
is "<county>|<2-letter state>" (lowercase, produced by config_key()) mapping
to a dict describing how to search and parse that county's property
appraiser site.

--------------------------------------------------------------------------
CONFIG SCHEMA
--------------------------------------------------------------------------
display_name       str  - human-readable name, used in logs/output.
search_type         str  - one of:
                        "html_get"        simple GET request, name goes in
                                          a query string param.
                        "aspnet_postback" classic ASP.NET WebForms search:
                                          GET the page first to harvest
                                          __VIEWSTATE/__EVENTVALIDATION
                                          hidden fields, then POST the form.
                        "selenium"        page requires JS to render
                                          results (SPA / client-side
                                          rendering) - handled by
                                          selenium_scraper.py instead of
                                          requests+BeautifulSoup.
                        "arcgis_query"    county's GIS parcel data is
                                          exposed as a public ArcGIS
                                          FeatureServer/MapServer layer -
                                          queried directly as JSON (no
                                          HTML/browser involved at all).
                                          See the "arcgis" key below.
                                          Prefer this over html_get/
                                          aspnet_postback/selenium
                                          whenever a county has one -
                                          it's far more reliable than
                                          scraping a web UI. Check for it
                                          by looking for an
                                          "*.arcgis.com" or "/arcgis/rest/
                                          services" / "/gis/rest/services"
                                          URL anywhere on the county GIS
                                          site, then hit
                                          "<.../MapServer/N>?f=json" to see
                                          if it lists owner/address
                                          fields.
arcgis                dict - only for search_type "arcgis_query":
                        query_url: the layer's .../query endpoint.
                        owner_field: field name to filter owner name on
                          (e.g. "OWNER1").
                        out_fields: list of field names to request.
                        owner_name_fields: field(s) to join (with " & ")
                          into owner_name_found (e.g. ["OWNER1","OWNER2"]
                          for co-owners).
                        property_address_compose /
                        mailing_address_compose: dicts with optional
                          "street"/"street2"/"city"/"state"/"zip" keys
                          mapping to field names, combined into one
                          address string.
                        parcel_id_field: field name for the parcel ID.
                        max_results: cap on resultRecordCount per query
                          (default 50).
search_page_url     str  - page to GET first (form page). Same as
                          search_url for html_get.
search_url          str  - for html_get: URL template containing "{name}"
                          (already URL-quoted by the scraper). For
                          aspnet_postback: the POST target (usually same
                          as search_page_url). For selenium: the URL to
                          load in the browser.
method               str  - "GET" or "POST" (html_get is GET, aspnet is
                          POST; selenium ignores this).
name_param           str  - form field name / query param that carries the
                          owner name being searched.
extra_static_params  dict - additional fixed form fields/query params to
                          always send (e.g. a "search type = owner"
                          selector, a submit-button field).
hidden_field_selector str - CSS selector used to harvest ALL hidden
                          <input> fields from the GET'd form page (only
                          used for aspnet_postback). Default:
                          'input[type="hidden"]'.
row_selector         str  - CSS selector (relative to the parsed results
                          page) that matches ONE result row per property.
fields                dict - maps our output field names to extraction
                          rules, each either {"selector": "<css, relative
                          to the row>"} or {"index": <int>} for plain
                          "nth <td> in the row". Recognized keys:
                          "owner_name_found", "property_address",
                          "mailing_address", "parcel_id".
no_results_markers   list[str] - case-insensitive substrings that, if
                          present in the response body, mean "no matches"
                          even if row_selector happens to match nothing
                          (or vice versa - checked either way).
selenium_search_box_selector / selenium_submit_selector /
selenium_wait_selector - only used when search_type == "selenium"; CSS
                          selectors for the name input, the submit
                          control, and an element to wait for after
                          submitting (results container).
verified              bool - True once someone has confirmed this config
                          against the live site with a real search. False
                          means "structurally correct pattern, but exact
                          field names/selectors are unconfirmed" - see
                          verification_note.
verification_note     str  - explains what to check / how to verify.

See README.md -> "Adding a New County" for the step-by-step, non-technical
walkthrough, and `python main.py --inspect-county "County ST"` for a tool
that dumps a search page's form fields to help you fill this in.
--------------------------------------------------------------------------
"""

from __future__ import annotations

# Full state-name -> 2-letter abbreviation, so the input CSV can say either
# "FL" or "Florida" and still match.
US_STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL",
    "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY",
}
_VALID_ABBR = set(US_STATE_ABBR.values())


def normalize_state(state: str) -> str:
    """'Florida' / 'FL' / ' fl ' -> 'FL'. Returns '' if unrecognized."""
    if not state:
        return ""
    s = state.strip().upper()
    if s in _VALID_ABBR:
        return s
    return US_STATE_ABBR.get(s, "")


def normalize_county(county: str) -> str:
    """'Duval County' / 'duval' / ' Duval ' -> 'duval'."""
    if not county:
        return ""
    c = county.strip().lower()
    for suffix in (" county", " parish", " borough"):
        if c.endswith(suffix):
            c = c[: -len(suffix)]
    return c.strip()


def config_key(county: str, state: str) -> str:
    # COUNTY_CONFIGS keys are lowercase (e.g. "duval|fl"); normalize_state()
    # returns an uppercase abbreviation ("FL") for display/comparison
    # purposes elsewhere, so it must be lowercased again here.
    return f"{normalize_county(county)}|{normalize_state(state).lower()}"


COUNTY_CONFIGS = {
    # ----------------------------------------------------------------
    # Duval County, FL - Duval County Property Appraiser (DCPAO)
    #
    # Public search tool confirmed at:
    #   https://paopropertysearch.coj.net/Basic/Search.aspx
    # (URL provided by user and independently confirmed via search of
    # jacksonville.gov's Property Appraiser department page.)
    #
    # This site is a classic ASP.NET WebForms application, so the pattern
    # below (GET the form to harvest __VIEWSTATE/__EVENTVALIDATION, then
    # POST the search) is structurally correct for how these sites work.
    #
    # !! NOT YET FIELD-VERIFIED !!
    # The build environment used to create this config could not reach
    # paopropertysearch.coj.net directly (outbound requests were refused
    # by the site, likely blocking datacenter/bot IP ranges), so the
    # exact form field NAMES below (name_param, extra_static_params) and
    # the results-table CSS selectors (row_selector, fields) are
    # best-guess placeholders, NOT confirmed against live HTML.
    #
    # Before relying on this for real leads, run:
    #     python main.py --inspect-county "Duval FL"
    # from a machine that CAN reach the site. It prints every <input>
    # field name/id on the search form (which tells you the real
    # name_param) and, if you pass --inspect-sample-name, also runs one
    # search and prints the results HTML structure so you can fix
    # row_selector/fields in ~2 minutes. See README "Adding a New
    # County" step 5 for what to look for.
    # ----------------------------------------------------------------
    "duval|fl": {
        "display_name": "Duval County, FL",
        "search_type": "aspnet_postback",
        "search_page_url": "https://paopropertysearch.coj.net/Basic/Search.aspx",
        "search_url": "https://paopropertysearch.coj.net/Basic/Search.aspx",
        "method": "POST",
        # PLACEHOLDER - confirm real field name via --inspect-county.
        "name_param": "ctl00$MainContent$txtOwnerName",
        "extra_static_params": {
            # PLACEHOLDER - confirm real submit-button field name/value.
            "ctl00$MainContent$btnSearch": "Search",
        },
        "hidden_field_selector": 'input[type="hidden"]',
        "row_selector": "table#MainContent_gvResults tr.resultRow",
        "fields": {
            "parcel_id": {"index": 0},
            "owner_name_found": {"index": 1},
            "property_address": {"index": 2},
            "mailing_address": {"index": 3},
        },
        "no_results_markers": [
            "no records found",
            "no results found",
            "0 records",
        ],
        "verified": False,
        "verification_note": (
            "Structurally correct ASP.NET postback pattern; exact field "
            "names/selectors unconfirmed because the build environment "
            "could not reach the live site. Run "
            "`python main.py --inspect-county \"Duval FL\"` to verify/fix "
            "name_param, extra_static_params, row_selector and fields "
            "before production use."
        ),
    },
    # ----------------------------------------------------------------
    # Pinellas County, FL - Pinellas County Property Appraiser (PCPAO)
    #
    # PCPAO's own web UI ("Advanced Search" at pcpao.gov) turned out to be
    # a JS-heavy report-builder that DOWNLOADS a file rather than
    # rendering results inline - not a good scraping target. Instead,
    # this queries Pinellas County's public ArcGIS parcel layer directly
    # as JSON. No authentication required.
    #
    # !! LIVE-VERIFIED !!
    # A real query for surname "SMITH" against this endpoint returned
    # actual owner/address/parcel records (owner names, situs addresses,
    # mailing addresses, and parcel IDs all populated). See
    # verification_note for the one real caveat.
    # ----------------------------------------------------------------
    "pinellas|fl": {
        "display_name": "Pinellas County, FL",
        "search_type": "arcgis_query",
        "arcgis": {
            "query_url": "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1/query",
            "owner_field": "OWNER1",
            "out_fields": [
                "OWNER1", "OWNER2", "SITE_ADDRESS", "SITE_CITY", "SITE_STATE", "SITE_ZIP",
                "OWNADD_1", "OWNADD_2", "OWNCITY", "OWNSTATE", "OWNZIP", "PARCELID", "STRAP",
            ],
            "owner_name_fields": ["OWNER1", "OWNER2"],
            "property_address_compose": {
                "street": "SITE_ADDRESS", "city": "SITE_CITY", "state": "SITE_STATE", "zip": "SITE_ZIP",
            },
            "mailing_address_compose": {
                "street": "OWNADD_1", "street2": "OWNADD_2",
                "city": "OWNCITY", "state": "OWNSTATE", "zip": "OWNZIP",
            },
            "parcel_id_field": "PARCELID",
            "max_results": 50,
        },
        "verified": True,
        "verification_note": (
            "Live-tested successfully. Search strategy uses the LAST WORD "
            "of owner_name_input as a surname anchor (assumes 'FIRST LAST' "
            "input ordering, matching the sample CSV format), then fuzzy-"
            "matches all candidates the county returns against the full "
            "input name. Owner records for trusts/businesses/entities "
            "whose name doesn't end in a surname-like token (e.g. 'ABC "
            "HOLDINGS LLC' anchors on 'LLC', which won't match anything) "
            "will come back NOT FOUND - if your lead list has many "
            "entity-owned properties, consider widening the query (e.g. "
            "anchor on the first word instead, or OR multiple tokens "
            "together) in county_configs.py."
        ),
    },
}

# ----------------------------------------------------------------------
# TEMPLATE - copy this into COUNTY_CONFIGS above when adding a county.
# Not registered under any key, so it has no effect by itself.
# See README.md "Adding a New County" for the full walkthrough.
# ----------------------------------------------------------------------
TEMPLATE_HTML_GET = {
    "display_name": "Example County, ST",
    "search_type": "html_get",
    "search_page_url": "https://example-county-appraiser.gov/search",
    # {name} is replaced with the URL-encoded owner name at request time.
    "search_url": "https://example-county-appraiser.gov/search?ownerName={name}&type=owner",
    "method": "GET",
    "name_param": None,  # not used for html_get; name is baked into search_url
    "extra_static_params": {},
    "row_selector": "table.results-table tr.result-row",
    "fields": {
        "owner_name_found": {"selector": "td.owner"},
        "property_address": {"selector": "td.site-address"},
        "mailing_address": {"selector": "td.mailing-address"},
        "parcel_id": {"selector": "td.parcel-number"},
    },
    "no_results_markers": ["no results", "0 records found"],
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}

TEMPLATE_ASPNET_POSTBACK = {
    "display_name": "Example County, ST",
    "search_type": "aspnet_postback",
    "search_page_url": "https://example-county-appraiser.gov/Search.aspx",
    "search_url": "https://example-county-appraiser.gov/Search.aspx",
    "method": "POST",
    "name_param": "ctl00$MainContent$txtOwnerName",
    "extra_static_params": {"ctl00$MainContent$btnSearch": "Search"},
    "hidden_field_selector": 'input[type="hidden"]',
    "row_selector": "table#MainContent_gvResults tr.resultRow",
    "fields": {
        "parcel_id": {"index": 0},
        "owner_name_found": {"index": 1},
        "property_address": {"index": 2},
        "mailing_address": {"index": 3},
    },
    "no_results_markers": ["no records found"],
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}

TEMPLATE_ARCGIS_QUERY = {
    "display_name": "Example County, ST",
    "search_type": "arcgis_query",
    "arcgis": {
        # Find this by looking for "*.arcgis.com" or "/arcgis/rest/services"
        # / "/gis/rest/services" anywhere on the county's GIS site, then
        # browsing to a layer and appending "?f=json" to see its fields.
        "query_url": "https://example-county-gis.gov/arcgis/rest/services/Parcels/MapServer/0/query",
        "owner_field": "OWNER1",
        "out_fields": ["OWNER1", "OWNER2", "SITE_ADDRESS", "MAIL_ADDRESS", "PARCEL_ID"],
        "owner_name_fields": ["OWNER1", "OWNER2"],
        "property_address_compose": {"street": "SITE_ADDRESS"},
        "mailing_address_compose": {"street": "MAIL_ADDRESS"},
        "parcel_id_field": "PARCEL_ID",
        "max_results": 50,
    },
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}

TEMPLATE_SELENIUM = {
    "display_name": "Example County, ST",
    "search_type": "selenium",
    "search_url": "https://example-county-appraiser.gov/app/search",
    "selenium_search_box_selector": "input#ownerNameInput",
    "selenium_submit_selector": "button#searchButton",
    "selenium_wait_selector": "div.results-container",
    "row_selector": "div.result-card",
    "fields": {
        "owner_name_found": {"selector": ".owner-name"},
        "property_address": {"selector": ".site-address"},
        "mailing_address": {"selector": ".mailing-address"},
        "parcel_id": {"selector": ".parcel-id"},
    },
    "no_results_markers": ["no results found"],
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}


def get_county_config(county: str, state: str):
    """Look up a county's config, or None if not in the directory."""
    return COUNTY_CONFIGS.get(config_key(county, state))
