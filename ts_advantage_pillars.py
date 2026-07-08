"""
ts_advantage_pillars.py

Static reference content for the ThoughtSpot Advantage report section.

Source: "The ThoughtSpot Advantage" messaging deck (GTM Buddy document_id
6a4d2ce2c5e91016c4af8850, RevDrop July Deck). This content is static
company messaging, not account-specific data -- it only changes when
ThoughtSpot updates its positioning. There is no live extraction step
for it; update this file by hand when the deck changes.

Usage:
    from ts_advantage_pillars import PILLARS, get_pillar

    for pillar in PILLARS:
        pillar["name"], pillar["surface_line"], pillar["points"], pillar["say_it_like_this"]
"""

PILLARS = [
    {
        "name": "Trusted Answers, Every Time",
        "surface_line": "Data integrity — the answer is right at the source",
        "points": [
            {"label": "Deterministic, not probabilistic", "detail": "Backed by our patented tokens for accuracy"},
            {"label": "Verifiable, auditable, transparent", "detail": "Powered by governed semantics"},
            {"label": "Analytical depth & expressibility", "detail": "Complex questions, answered"},
        ],
        "say_it_like_this": (
            "Most AI tools guess. ThoughtSpot doesn't. Every answer traces back to a "
            "governed data source — so when a customer asks how you got that number, "
            "you can show them exactly."
        ),
    },
    {
        "name": "AI and BI, Better Together",
        "surface_line": "Surface consistency — the same answer, look, and feel across every output",
        "points": [
            {"label": "Dashboards, AI, & search", "detail": "one governed answer."},
            {"label": "Self-service that delivers", "detail": "insights users act on themselves."},
            {"label": "Embed in your own apps", "detail": "same platform, internal and external."},
        ],
        "say_it_like_this": (
            "Your team shouldn't get one answer in a dashboard and a different one "
            "from AI. ThoughtSpot is one platform — so every number, everywhere, says "
            "the same thing."
        ),
    },
    {
        "name": "Production Ready, Enterprise Grade",
        "surface_line": "Security, complexity, and cost control a DIY build can't match",
        "points": [
            {"label": "Controlled AI costs", "detail": "We manage the token risk, not you."},
            {"label": "Open by design", "detail": "No vendor lock-in."},
            {"label": "Let us innovate, build and run", "detail": "So you never have to."},
        ],
        "say_it_like_this": (
            "Building your own BI + AI stack sounds great until you're managing token "
            "costs, security reviews, and three vendors. ThoughtSpot is already built, "
            "already governed, already running — you just turn it on."
        ),
    },
]


def _normalize(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch.isspace()).split()


def get_pillar(name: str) -> dict:
    """
    Fuzzy-match a pillar by name (case-insensitive, punctuation-tolerant).
    Returns None if nothing reasonable matches -- callers should treat
    that as a bug in the caller's name, not a data gap, since PILLARS
    is a fixed, exhaustive list of exactly three entries.
    """
    if not name:
        return None
    name_words = _normalize(name)
    for p in PILLARS:
        if _normalize(p["name"]) == name_words:
            return p
    name_set = set(name_words)
    for p in PILLARS:
        pillar_words = set(_normalize(p["name"]))
        if name_set and (name_set <= pillar_words or pillar_words <= name_set):
            return p
    return None


if __name__ == "__main__":
    assert len(PILLARS) == 3
    for p in PILLARS:
        assert len(p["points"]) == 3
        assert p["say_it_like_this"]
    assert get_pillar("Trusted Answers, Every Time") is PILLARS[0]
    assert get_pillar("trusted answers every time") is PILLARS[0]
    assert get_pillar("nonexistent pillar") is None
    print("✅ ts_advantage_pillars.py self-test passed")
