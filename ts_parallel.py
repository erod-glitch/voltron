"""
ts_parallel.py
Parallel ThoughtSpot query runner built on threading.Thread.
Uses raw threading — concurrent.futures is blocked in PTC sandbox.

Usage:
    from ts_parallel import run_ts_batch, run_ts_batch_for_accounts,
                           check_token_expiry, check_missing_env, TERRITORY_FILTER_INTENTS

    results = run_ts_batch([
        ("deal_stage",         {"account_name": "Acme Corp"}),
        ("meddpicc_flags",     {"account_name": "Acme Corp"}),
        ("activity_history",   {"account_name": "Acme Corp"}),
    ], max_workers=6, timeout=25)

    if check_token_expiry(results):
        print("⚠️ TOKEN_EXPIRED")

    if check_missing_env(results):
        # THOUGHTSPOT_TOKEN/THOUGHTSPOT_URL not set in this sandbox — agent
        # must fall back to ask_spotter_question() per intent, see
        # ts_fallback_map.normalize_spotter_result()
        print("⚠️ MISSING_ENV — fall back to Spotter")

    if results["deal_stage"]["status"] == "ok":
        rows = results["deal_stage"]["data_rows"]
"""

import threading
import time
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Lazy import guard
# ---------------------------------------------------------------------------

try:
    from ts_fallback_map import run_with_fallback
except ImportError:
    def run_with_fallback(intent: str, timeout: int = 20, **variables) -> dict:
        raise ImportError(
            "ts_fallback_map not loaded. "
            "Cannot run ThoughtSpot queries."
        )


# ---------------------------------------------------------------------------
# Convenience constant
# ---------------------------------------------------------------------------

TERRITORY_FILTER_INTENTS = [
    "6sense_account_intent",
    "last_activity",
    "icp_scores",
    "account_vertical",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def _run_single_thread(
    intent:    str,
    variables: dict,
    key:       str,
    timeout:   int,
    results:   dict,
    errors:    dict,
):
    """
    Execute one run_with_fallback() call and write result into shared dict.
    Designed to run inside a threading.Thread.
    """
    print(f"[ts_parallel] {_now_str()} | START | intent={intent!r} key={key!r}")
    try:
        result = run_with_fallback(intent, timeout=timeout, **variables)
        results[key] = result
        print(
            f"[ts_parallel] {_now_str()} | DONE  | "
            f"key={key!r} status={result.get('status', 'unknown')}"
        )
    except Exception as exc:
        err_msg = str(exc)
        errors[key] = err_msg
        results[key] = {
            "status":       "error",
            "message":      err_msg,
            "column_names": [],
            "data_rows":    [],
        }
        print(f"[ts_parallel] {_now_str()} | ERROR | key={key!r} error={err_msg!r}")


# ---------------------------------------------------------------------------
# Token expiry check
# ---------------------------------------------------------------------------

def check_token_expiry(results: dict) -> bool:
    """
    Return True if any result indicates token expiry.

    Use immediately after run_ts_batch() or run_ts_batch_for_accounts():
        if check_token_expiry(results):
            # Surface ⚠️ token expiry — halt TS queries
    """
    return any(
        v.get("status") == "token_expired"
        for k, v in results.items()
        if k != "_token_expired" and isinstance(v, dict)
    )


def check_missing_env(results: dict) -> bool:
    """
    Return True if any result failed because THOUGHTSPOT_TOKEN/THOUGHTSPOT_URL
    aren't set in this sandbox — the signal to fall back to the agent calling
    ask_spotter_question()/get_spotter_query_status() directly, transcribing
    the rows, and passing them through ts_fallback_map.normalize_spotter_result()
    instead of retrying the direct-HTTP path.

    Use immediately after run_ts_batch() or run_ts_batch_for_accounts():
        if check_missing_env(results):
            # Fall back to Spotter per intent — see ts_fallback_map.normalize_spotter_result()
    """
    return any(
        v.get("error_code") == "missing_env"
        for k, v in results.items()
        if k != "_token_expired" and isinstance(v, dict)
    )


# ---------------------------------------------------------------------------
# Primary public helper — multiple intents
# ---------------------------------------------------------------------------

def run_ts_batch(
    intents_and_vars: list,
    max_workers:      int = 6,
    timeout:          int = 25,
) -> dict:
    """
    Fire multiple ThoughtSpot intents concurrently using threading.Thread.

    Parameters
    ----------
    intents_and_vars : list of (intent_str, variables_dict) tuples
    max_workers      : Maximum concurrent threads (default 6)
    timeout          : Per-query timeout in seconds (default 25)

    Returns
    -------
    dict keyed by intent name. Duplicate intents get _2, _3 suffixes.
    Includes "_token_expired": True if any query expired.
    """
    if not intents_and_vars:
        return {}

    # Build de-duplicated keys
    key_counts: dict  = {}
    keyed_tasks: list = []

    for intent, variables in intents_and_vars:
        if intent not in key_counts:
            key_counts[intent] = 1
            key = intent
        else:
            key_counts[intent] += 1
            key = f"{intent}_{key_counts[intent]}"
        keyed_tasks.append((key, intent, variables))

    n           = len(keyed_tasks)
    workers     = min(max_workers, n)
    results     = {}
    errors      = {}
    batch_start = time.monotonic()

    print(
        f"[ts_parallel] {_now_str()} | BATCH | "
        f"{n} quer{'y' if n == 1 else 'ies'} "
        f"max_workers={workers} timeout={timeout}s"
    )

    # Process in batches of max_workers
    for batch_start_idx in range(0, n, workers):
        batch   = keyed_tasks[batch_start_idx:batch_start_idx + workers]
        threads = []

        for key, intent, variables in batch:
            t = threading.Thread(
                target=_run_single_thread,
                args=(intent, variables, key, timeout, results, errors),
                daemon=True,
            )
            threads.append(t)
            t.start()

        # Wait for all threads in this batch with timeout
        deadline = time.monotonic() + timeout + 5
        for t in threads:
            remaining = max(0, deadline - time.monotonic())
            t.join(timeout=remaining)

        # Any thread still alive after join = timed out
        for i, t in enumerate(threads):
            if t.is_alive():
                key = batch[i][0]
                if key not in results:
                    results[key] = {
                        "status":       "error",
                        "message":      f"Thread timed out after {timeout}s",
                        "column_names": [],
                        "data_rows":    [],
                    }
                print(
                    f"[ts_parallel] {_now_str()} | TIMEOUT | "
                    f"key={key!r}"
                )

    # Flag token expiry
    if check_token_expiry(results):
        results["_token_expired"] = True
        expired_keys = [
            k for k, v in results.items()
            if isinstance(v, dict) and v.get("status") == "token_expired"
        ]
        print(
            f"[ts_parallel] {_now_str()} | ⚠️ TOKEN EXPIRED | "
            f"detected in: {expired_keys}"
        )

    elapsed  = time.monotonic() - batch_start
    ok_count = sum(
        1 for k, v in results.items()
        if k != "_token_expired" and isinstance(v, dict)
        and v.get("status") == "ok"
    )
    print(
        f"[ts_parallel] {_now_str()} | FINISH | "
        f"{ok_count}/{n} succeeded elapsed={elapsed:.2f}s"
    )
    return results


# ---------------------------------------------------------------------------
# Secondary public helper — same intent, multiple accounts or owners
# ---------------------------------------------------------------------------

def run_ts_batch_for_accounts(
    intent:       str,
    values:       list,
    variable_key: str = "account_name",
    max_workers:  int = 5,
    timeout:      int = 25,
) -> dict:
    """
    Fire the same intent against multiple values concurrently.

    Parameters
    ----------
    intent       : ThoughtSpot intent name
    values       : List of account names or owner names
    variable_key : "account_name" (default) or "owner_name"
    max_workers  : Maximum concurrent threads (default 5)
    timeout      : Per-query timeout in seconds (default 25)

    Returns
    -------
    dict keyed by value. Includes "_token_expired": True if any expired.
    """
    if not values:
        return {}

    n           = len(values)
    workers     = min(max_workers, n)
    results     = {}
    errors      = {}
    batch_start = time.monotonic()

    print(
        f"[ts_parallel] {_now_str()} | ACCT-BATCH | "
        f"intent={intent!r} {n} value(s) "
        f"variable_key={variable_key!r} "
        f"max_workers={workers} timeout={timeout}s"
    )

    # Process in batches of max_workers
    for batch_start_idx in range(0, n, workers):
        batch   = values[batch_start_idx:batch_start_idx + workers]
        threads = []

        for value in batch:
            t = threading.Thread(
                target=_run_single_thread,
                args=(
                    intent,
                    {variable_key: value},
                    value,
                    timeout,
                    results,
                    errors,
                ),
                daemon=True,
            )
            threads.append((t, value))
            t.start()

        # Wait for batch with timeout
        deadline = time.monotonic() + timeout + 5
        for t, value in threads:
            remaining = max(0, deadline - time.monotonic())
            t.join(timeout=remaining)

        # Check for timed-out threads
        for t, value in threads:
            if t.is_alive():
                if value not in results:
                    results[value] = {
                        "status":       "error",
                        "message":      f"Thread timed out after {timeout}s",
                        "column_names": [],
                        "data_rows":    [],
                    }
                print(
                    f"[ts_parallel] {_now_str()} | TIMEOUT | "
                    f"{variable_key}={value!r}"
                )

    # Flag token expiry
    if check_token_expiry(results):
        results["_token_expired"] = True
        print(
            f"[ts_parallel] {_now_str()} | ⚠️ TOKEN EXPIRED | "
            f"intent={intent!r}"
        )

    elapsed  = time.monotonic() - batch_start
    ok_count = sum(
        1 for k, v in results.items()
        if k != "_token_expired" and isinstance(v, dict)
        and v.get("status") == "ok"
    )
    print(
        f"[ts_parallel] {_now_str()} | FINISH | "
        f"intent={intent!r} {ok_count}/{n} succeeded "
        f"elapsed={elapsed:.2f}s"
    )
    return results


# ---------------------------------------------------------------------------
# Owner extraction helper
# Eliminates pre-flight SFDC lookup just to get the owner name
# ---------------------------------------------------------------------------

def extract_owner_from_ts_results(ts_results: dict) -> str:
    """
    Extract the AE/account owner name from sfdc_stakeholder batch results.
    Used to avoid a separate SFDC lookup just to get the owner name.

    Returns the owner name string, or "" if not found.
    """
    sfdc = ts_results.get("sfdc_stakeholder", {})
    if not sfdc or sfdc.get("status") != "ok":
        return ""
    cols = sfdc.get("column_names", [])
    rows = sfdc.get("data_rows", [])
    if not rows:
        return ""
    rec = dict(zip(cols, rows[0])) if isinstance(rows[0], list) else rows[0]
    return (
        rec.get("Account Owner Name")
        or rec.get("Opportunity Owner Name")
        or rec.get("account_owner_name")
        or ""
    )


# ---------------------------------------------------------------------------
# 6Sense follow-on helper
# Fire after main batch once owner_name is known from sfdc_stakeholder
# ---------------------------------------------------------------------------

def run_6sense_followon(owner_name: str, timeout: int = 15) -> dict:
    """
    Fire the 6Sense follow-on queries after the main batch, once owner_name
    is known from sfdc_stakeholder results.

    Fires TWO independent queries, not one — they hit different underlying
    tables (SFDC_ACCOUNT vs SFDC_PERSON) and must stay separate. Combining
    account-level and person-level 6Sense columns into a single query forces
    ThoughtSpot to join across those tables, and an account with no scored
    individual contact then returns zero rows entirely — silently hiding
    real account-level intent data behind a missing person-level join.

    Returns {"6sense_account_intent": {...}, "6sense_intent": {...}} —
    merge both keys into ts_data (e.g. ts_data.update(...)), don't assign
    the whole return value to a single "6sense_intent" key.
    """
    if not owner_name:
        skipped = {
            "status":       "skipped",
            "data_rows":    [],
            "column_names": [],
            "reason":       "owner_name empty — cannot query 6sense by owner",
        }
        return {"6sense_account_intent": dict(skipped), "6sense_intent": dict(skipped)}

    results = run_ts_batch(
        [
            ("6sense_account_intent", {"owner_name": owner_name}),
            ("6sense_intent",         {"owner_name": owner_name}),
        ],
        max_workers=2,
        timeout=timeout,
    )
    for key in ("6sense_account_intent", "6sense_intent"):
        r = results.get(key, {})
        print(
            f"[ts_parallel] 6sense follow-on '{key}' for '{owner_name}': "
            f"{r.get('status')} — {len(r.get('data_rows', []))} rows"
        )
    return {
        "6sense_account_intent": results.get("6sense_account_intent", {}),
        "6sense_intent":         results.get("6sense_intent", {}),
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== ts_parallel.py self-test (threading mode) ===\n")

    print("✅ Module imported OK")
    assert len(TERRITORY_FILTER_INTENTS) == 4
    print(f"✅ TERRITORY_FILTER_INTENTS: {TERRITORY_FILTER_INTENTS}")

    fake_expired = {
        "deal_stage": {"status": "token_expired", "column_names": [], "data_rows": []},
    }
    assert check_token_expiry(fake_expired) is True
    print("✅ check_token_expiry detects expiry")

    fake_ok = {"deal_stage": {"status": "ok", "column_names": [], "data_rows": []}}
    assert check_token_expiry(fake_ok) is False
    print("✅ check_token_expiry clean on ok")

    result = run_ts_batch([])
    assert result == {}
    print("✅ run_ts_batch handles empty input")

    result = run_ts_batch_for_accounts("deal_stage", [])
    assert result == {}
    print("✅ run_ts_batch_for_accounts handles empty input")

    # Test threading directly
    test_results = {}
    test_errors  = {}

    def _fake_query(intent, variables, key, timeout, results, errors):
        time.sleep(0.1)
        results[key] = {"status": "ok", "column_names": ["test"], "data_rows": [["val"]]}

    threads = []
    for i in range(3):
        t = threading.Thread(
            target=_fake_query,
            args=(f"intent_{i}", {}, f"key_{i}", 5, test_results, test_errors),
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    assert len(test_results) == 3
    assert all(v["status"] == "ok" for v in test_results.values())
    print("✅ threading.Thread parallel execution verified")

    print("\nSelf-test complete.")
