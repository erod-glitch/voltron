"""
ts_fallback_map.py
ThoughtSpot query runner with intent-based fallback, token expiry detection,
timeout, and structured error returns.

Canonical unified file — replaces ts_intent_map.py + ts_runner.py split.

Usage:
    from ts_fallback_map import run_with_fallback, warm_account_cache, read_account_cache

    result = run_with_fallback("deal_stage", account_name="Acme Corp")
    if result["status"] == "ok":
        rows = result["data_rows"]
"""

import os
import time
import requests

# ---------------------------------------------------------------------------
# Worksheet IDs
# ---------------------------------------------------------------------------

WORKSHEETS = {
    "revops":    "9cfb299b-7378-48cb-ae20-a0bc5a82b223",
    "campaigns": "b2d24878-0150-4c1e-a74c-bfc46cc4a682",
    "studio":    "dbef58b8-a3ea-485e-8818-c1bfc35bf207",
    "engage":    "6d10b8a9-d4ca-451c-a102-a745329db735",
}

# ---------------------------------------------------------------------------
# Intent map
# Each entry: (primary_worksheet, primary_query, fallback_worksheet, fallback_query)
# Filter syntax: [Field Name] = 'value' — bracket style required
# ---------------------------------------------------------------------------

INTENT_MAP = {
    "account_ownership": (
        "studio",
        "[Account Name] [Account Owner Name] [Account Owner Team] [Account Segment] [Account Status [For Calculation]] [Account Owner Name] = '{owner_name}'",
        "revops",
        "[Account Name] [Account Owner Name] [Account Owner Team] [Account Segment] [Account Status] [Account Owner Name] = '{owner_name}'"
    ),
    "territory_overview": (
        "revops",
        "[Account Name] [Account Segment] [Account TSA ICP Score] [Account TSE ICP Score] [Account Last Activity Date] [Days from Account last touch] [Account Snapshot 6S Reach Score] [Account Kernel Main Vertical] [Account Owner Name] = '{owner_name}'",
        "studio",
        "[Account Name] [Account Segment] [Account Rev Score - TSA] [Account Rev Score - TSE] [Account Clearbit Industry Group] [Account Owner Name] = '{owner_name}'"
    ),
     "6sense_intent": (
        "revops",
        "[Account Name] [Person 6S Intent Score] [Account Owner Name] [Account Owner Name] = '{owner_name}'",
        "revops",
        "[Account Name] [Person 6S Intent Score] [Account Owner Name] = '{owner_name}'"
    ),
    "6sense_account_intent": (
        "revops",
        "[Account Name] [Account 6S Intent Score] [Account 6S Reach Score] [Account Consolidated Intent Level] [Account Owner Name] [Account Owner Name] = '{owner_name}'",
        "revops",
        "[Account Name] [Account 6S Intent Score] [Account 6S Reach Score] [Account Owner Name] = '{owner_name}'"
    ),
    "last_activity": (
        "revops",
        "[Account Name] [Account Last Activity Date] [Days from Account last touch] [Account last touch grouped] [Account Owner Name] [Account Owner Name] = '{owner_name}'",
        "revops",
        "[Account Name] [Account Last Activity Date] [Account Owner Name] [Account Owner Name] = '{owner_name}'"
    ),
    "icp_scores": (
        "revops",
        "[Account Name] [Account TSA ICP Score] [Account TSA ICP Grade] [Account TSE ICP Score] [Account Owner Name] [Account Owner Name] = '{owner_name}'",
        "studio",
        "[Account Name] [Account Rev Score - TSA] [Account Rev Score - TSE] [Account Owner Name] [Account Owner Name] = '{owner_name}'"
    ),
    "account_vertical": (
        "revops",
        "[Account Name] [Account Kernel Main Vertical] [Account Kernel Sub Vertical] [Account Clearbit Industry] [Account Owner Name] [Account Owner Name] = '{owner_name}'",
        "studio",
        "[Account Name] [Account Clearbit Sub-Industry [For Calculation]] [Account Clearbit Industry Group] [Account Owner Name] [Account Owner Name] = '{owner_name}'"
    ),
    "deal_stage": (
        "revops",
        "[Account Name] [Opportunity Name] [Opportunity Stage Maximum Name] [Opportunity Pipeline Qualified Flag] [Opportunity Owner Name] [Opportunity Created Date] [Opportunity Last Activity Date] [Account Name] = '{account_name}'",
        "revops",
        "[Account Name] [Opportunity Name] [Opportunity Stage Maximum Name] [Opportunity Owner Name] [Account Name] = '{account_name}'"
    ),
    "deal_funnel_timing": (
        "revops",
        "[Account Name] [Opportunity Name] [f Opportunity S1 Duration] [f Opportunity S2 Duration] [f Opportunity S3 Duration] [f Opportunity M0 to S7 Duration] [Opportunity Stage Maximum Name] [Account Name] = '{account_name}'",
        "revops",
        "[Account Name] [Opportunity Name] [Opportunity Current Stage Duration] [Opportunity Stage Maximum Name] [Account Name] = '{account_name}'"
    ),
    "meddpicc_flags": (
        "revops",
        "[Account Name] [Opportunity Name] [Opportunity Gong Champion Validated] [Opportunity Gong Economic Buyer Validated] [Opportunity Gong Identify Pain Validated] [Opportunity Gong Metrics Validated] [Opportunity Gong Decision Criteria Validated] [Opportunity Gong Decision Process Validated] [Opportunity Gong Paper Process Validated] [Opportunity Gong Competition Validated] [Opportunity Gong Data Readiness Validated] [Account Name] = '{account_name}'",
        "revops",
        "[Account Name] [Opportunity Name] [Opportunity Gong Champion Validated] [Opportunity Gong Economic Buyer Validated] [Opportunity Gong Metrics Validated] [Account Name] = '{account_name}'"
    ),
    "meddpicc_detail": (
        "revops",
        "[Account Name] [Opportunity Name] [Opportunity Gong Champion] [Opportunity Gong Economic Buyer] [Opportunity Gong Identify Pain] [Opportunity Gong Metrics] [Opportunity Gong Decision Criteria] [Opportunity Gong Decision Process] [Opportunity Gong Paper Process] [Opportunity Gong Competition] [Opportunity Gong Data Readiness] [Account Name] = '{account_name}'",
        "revops",
        "[Account Name] [Opportunity Name] [Opportunity Gong Champion] [Opportunity Gong Economic Buyer] [Opportunity Gong Metrics] [Account Name] = '{account_name}'"
    ),
    "activity_history": (
        "revops",
        "[Account Name] [Activity Type] [Activity Subject] [Activity Time] [Activity Owner Name] [Activity Owner Role] [Activity Direction] [Account Name] = '{account_name}' last 180 days",
        "revops",
        "[Account Name] [Activity Type] [Activity Subject] [Activity Time] [Activity Owner Name] [Account Name] = '{account_name}' last 90 days"
    ),
    "sfdc_stakeholder": (
        "studio",
        "[Account Name] [Account Owner Name] [Account Owner Team] [Opportunity Name] [Opportunity Owner Name] [Opportunity CS Name] [Executive Business Sponsor [For Calculation]] [Opportunity Status] [Account Name] = '{account_name}'",
        "revops",
        "[Account Name] [Account Owner Name] [Opportunity Name] [Opportunity Owner Name] [Opportunity Stage Maximum Name] [Account Name] = '{account_name}'"
    ),
}

# ---------------------------------------------------------------------------
# Internal query engine
# ---------------------------------------------------------------------------
def extract_contacts_from_activities(ts_data: dict) -> list:
    """
    Parse contact names out of Gong/Outreach activity subject lines.
    Returns list of dicts: {name, source}
    Used to cross-reference exec profiles and set in_sfdc=True on name matches.
    """
    import re
    contacts = []
    seen = set()

    act = ts_data.get("sfdc_contacts", ts_data.get("activity_history", {}))
    cols = act.get("column_names", [])
    rows = act.get("data_rows", [])

    for row in rows:
        r = dict(zip(cols, row)) if isinstance(row, list) else row
        subject = r.get("Activity Subject", "")

        # "[Gong Outbound]: Call with {Account} - {Contact Name} - No Answer"
        m = re.search(r'Call with [^-]+ - ([A-Z][a-z]+ [A-Z][a-zA-Z\s]+?) - ', subject)
        if m:
            name = m.group(1).strip()
            if name and name not in seen:
                seen.add(name)
                contacts.append({"name": name, "source": "gong_activity"})
            continue

        # "[Outreach] ... Outbound call to {Full Name}, ..."
        m2 = re.search(r'[Oo]utbound call to ([A-Z][a-z]+ [A-Z][a-zA-Z\s]+?)(?:,|\s*$)', subject)
        if m2:
            name = m2.group(1).strip().split(',')[0].strip()
            if name and name not in seen:
                seen.add(name)
                contacts.append({"name": name, "source": "outreach_activity"})

    return contacts

def _ts_query(query_string: str, worksheet_key: str, timeout: int = 20) -> dict:
    token    = os.environ.get("THOUGHTSPOT_TOKEN", "")
    base_url = os.environ.get("THOUGHTSPOT_URL", "").rstrip("/")

    if not token or not base_url:
        return {
            "_error":   "missing_env",
            "_message": "THOUGHTSPOT_TOKEN or THOUGHTSPOT_URL not set.",
        }

    worksheet_id = WORKSHEETS.get(worksheet_key)
    if not worksheet_id:
        return {
            "_error":   "bad_worksheet",
            "_message": f"Unknown worksheet key: {worksheet_key}",
        }

    url     = f"{base_url}/api/rest/2.0/searchdata"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    payload = {
        "query_string":             query_string,
        "logical_table_identifier": worksheet_id,
        "data_format":              "COMPACT",
        "record_offset":            0,
        "record_size":              500,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        return {"_error": "timeout", "_message": f"Query timed out after {timeout}s."}
    except Exception as exc:
        return {"_error": "request_exception", "_message": str(exc)}

    if resp.status_code == 401:
        return {"_error": "token_expired", "_message": "ThoughtSpot token expired (401)."}
    if resp.status_code == 403:
        return {"_error": "token_expired", "_message": "ThoughtSpot token forbidden (403)."}
    if resp.status_code != 200:
        return {"_error": "http_error", "_message": f"HTTP {resp.status_code}: {resp.text[:300]}"}

    try:
        data = resp.json()
    except Exception:
        return {"_error": "parse_error", "_message": "Could not parse JSON response."}

    return data


def _parse_response(raw: dict) -> tuple:
    """
    Parse a ThoughtSpot API response into (col_names, rows).
    Handles both top-level and contents-wrapped response shapes.
    Returns (None, error_message) on failure.
    """
    try:
        if "contents" in raw:
            payload = raw["contents"][0] if raw["contents"] else {}
        else:
            payload = raw

        col_names = (
            payload.get("column_names")
            or [c["column_name"] for c in payload.get("columns", [])]
        )
        rows = (
            payload.get("data_rows")
            or payload.get("data", {}).get("data_rows", [])
        )

        if not col_names:
            return None, "Response contained no column metadata."

        return col_names, rows

    except Exception as exc:
        return None, f"Could not parse response structure: {exc}"


# ---------------------------------------------------------------------------
# Attempt helper — always returns a consistent dict
# ---------------------------------------------------------------------------

def _attempt(
    worksheet_key:  str,
    query_template: str,
    variables:      dict,
    used_fallback:  bool,
    timeout:        int,
) -> dict:
    """
    Execute one query attempt. Always returns a dict with keys:
        status        : "ok" | "empty" | "token_expired" | "error"
        column_names  : list
        data_rows     : list
        query_used    : str
        used_fallback : bool
        message       : str
        error_code    : "" | "missing_env" | "bad_worksheet" | "timeout" |
                        "request_exception" | "http_error" | "parse_error"
    """
    base = {
        "status":        "error",
        "column_names":  [],
        "data_rows":     [],
        "query_used":    "",
        "used_fallback": used_fallback,
        "message":       "",
        "error_code":    "",
    }

    try:
        query = query_template.format(**variables)
    except KeyError as exc:
        base["message"] = f"Missing variable for query template: {exc}"
        return base

    base["query_used"] = query
    raw = _ts_query(query, worksheet_key, timeout=timeout)

    if raw.get("_error") == "token_expired":
        base["status"]  = "token_expired"
        base["message"] = raw.get("_message", "Token expired.")
        return base

    if "_error" in raw:
        base["message"]    = raw.get("_message", "Unknown error.")
        base["error_code"] = raw.get("_error", "")
        return base

    col_names, rows_or_err = _parse_response(raw)
    if col_names is None:
        base["message"] = rows_or_err
        return base

    base["column_names"] = col_names

    if not rows_or_err:
        base["status"]  = "empty"
        base["message"] = "Query returned 0 rows."
        return base

    if len(rows_or_err) >= 1000:
        base["_truncation_warning"] = (
            "Result set hit 1000-row limit — data may be truncated. "
            "Consider filtering by segment or region."
        )

    base["status"]    = "ok"
    base["data_rows"] = rows_or_err
    base["message"]   = f"{len(rows_or_err)} rows returned."
    return base


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def run_with_fallback(intent: str, timeout: int = 20, **variables) -> dict:
    """
    Execute a ThoughtSpot query by intent name with automatic fallback.

    Parameters
    ----------
    intent      : Key from INTENT_MAP (e.g. "deal_stage", "sfdc_stakeholder")
    timeout     : Per-query timeout in seconds (default 20)
    **variables : Template variables (e.g. account_name="Acme", owner_name="Jane")

    Returns
    -------
    dict with keys:
        status        : "ok" | "empty" | "token_expired" | "error"
        column_names  : list[str]
        data_rows     : list
        used_fallback : bool
        message       : str
        intent        : str
        query_used    : str
    """
    base = {
        "status":        "error",
        "column_names":  [],
        "data_rows":     [],
        "used_fallback": False,
        "message":       "",
        "intent":        intent,
        "query_used":    "",
        "error_code":    "",
    }

    if intent not in INTENT_MAP:
        base["message"] = (
            f"Unknown intent: {intent!r}. "
            f"Valid intents: {list(INTENT_MAP.keys())}"
        )
        return base

    primary_ws, primary_tpl, fallback_ws, fallback_tpl = INTENT_MAP[intent]

    # --- Primary attempt ---
    result = _attempt(primary_ws, primary_tpl, variables, False, timeout)

    if result["status"] == "token_expired":
        base.update(result)
        base["intent"] = intent
        return base

    if result["status"] == "ok":
        base.update(result)
        base["intent"] = intent
        return base

    primary_msg = result["message"]

    # --- Fallback attempt ---
    result2 = _attempt(fallback_ws, fallback_tpl, variables, True, timeout)

    if result2["status"] == "token_expired":
        base.update(result2)
        base["intent"] = intent
        return base

    if result2["status"] == "ok":
        result2["message"] = (
            f"{result2['message']} (fallback). Primary: {primary_msg}"
        )
        base.update(result2)
        base["intent"] = intent
        return base

    # --- Both empty or error ---
    if result["status"] == "empty" or result2["status"] == "empty":
        col_source = result if result["status"] == "empty" else result2
        base.update({
            "status":        "empty",
            "column_names":  col_source["column_names"],
            "data_rows":     [],
            "used_fallback": True,
            "query_used":    result2["query_used"],
            "message":       "Both primary and fallback returned 0 rows.",
        })
        base["intent"] = intent
        return base

    base["message"]    = f"Primary: {primary_msg} | Fallback: {result2['message']}"
    base["error_code"] = result.get("error_code") or result2.get("error_code") or ""
    base["intent"]     = intent
    return base


# ---------------------------------------------------------------------------
# Spotter fallback normalizer
#
# The Python sandbox has no THOUGHTSPOT_TOKEN/THOUGHTSPOT_URL — run_with_fallback()
# will return error_code="missing_env" every time in that environment. Only the
# agent itself can call ask_spotter_question()/get_spotter_query_status() (they
# are agent-layer tool calls, not importable from sandbox Python). When that
# happens, the agent calls Spotter directly, reads its answer, transcribes the
# rows into a plain list of dicts, and hands that list here — this function's
# only job is reshaping already-extracted records into the same envelope
# run_with_fallback() returns, so every downstream consumer (ts_parallel.py,
# pg_report_builder.py) treats Spotter-sourced and direct-HTTP-sourced data
# identically.
# ---------------------------------------------------------------------------

def normalize_spotter_result(intent: str, records: list) -> dict:
    """
    Reshape agent-transcribed Spotter rows into the run_with_fallback() envelope.

    Parameters
    ----------
    intent  : Key from INTENT_MAP (e.g. "6sense_account_intent") — used only
              for the returned "intent" field and unknown-intent validation.
    records : List of dicts, one per row, already extracted by the agent from
              Spotter's answer (column name -> value). A single dict is also
              accepted and wrapped into a one-row list.

    Returns
    -------
    dict with the same keys as run_with_fallback(): status, column_names,
    data_rows, used_fallback, message, intent, query_used, error_code, source.
    """
    base = {
        "status":        "error",
        "column_names":  [],
        "data_rows":     [],
        "used_fallback": False,
        "message":       "",
        "intent":        intent,
        "query_used":    "",
        "error_code":    "",
        "source":        "spotter",
    }

    if intent not in INTENT_MAP:
        base["message"] = (
            f"Unknown intent: {intent!r}. "
            f"Valid intents: {list(INTENT_MAP.keys())}"
        )
        return base

    if not records:
        base["status"]  = "empty"
        base["message"] = "Spotter returned 0 rows."
        return base

    if isinstance(records, dict):
        records = [records]

    base["status"]       = "ok"
    base["column_names"] = list(records[0].keys())
    base["data_rows"]    = records
    base["message"]      = f"{len(records)} rows returned via Spotter."
    return base


# ---------------------------------------------------------------------------
# Account pre-load cache — in-memory only
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 1800  # 30 minutes
_ACCOUNT_CACHE: dict = {}  # in-memory cache — lives for session duration


def warm_account_cache(owner_name: str) -> dict:
    """
    Fire the account_ownership query and store the result in memory.

    Call this AFTER posting "✅ Ready!" — never block session startup on it.

    NOTE: Cache is held in memory only — does not persist across sessions.
    """
    if not owner_name or not owner_name.strip():
        return {"status": "skipped", "message": "No owner_name provided."}

    owner_name = owner_name.strip()

    cached = _ACCOUNT_CACHE.get(owner_name)
    if cached:
        age = time.time() - cached.get("_cached_at", 0)
        if age < _CACHE_TTL_SECONDS:
            return cached

    result = run_with_fallback("account_ownership", owner_name=owner_name)

    if result.get("status") == "token_expired":
        return result

    result["_cached_at"]  = time.time()
    result["_owner_name"] = owner_name
    _ACCOUNT_CACHE[owner_name] = result
    return result


def read_account_cache(owner_name: str):
    """
    Return cached account_ownership result if < 30 minutes old.
    Returns None if missing or expired.

    Use in Territory Flow Step 1 before calling run_with_fallback():
        cached = read_account_cache(owner_name)
        if cached and cached.get("status") == "ok":
            account_data = cached
        else:
            account_data = run_with_fallback("account_ownership", owner_name=owner_name)
    """
    if not owner_name or not owner_name.strip():
        return None
    cached = _ACCOUNT_CACHE.get(owner_name.strip())
    if not cached:
        return None
    age = time.time() - cached.get("_cached_at", 0)
    if age < _CACHE_TTL_SECONDS:
        return cached
    return None
