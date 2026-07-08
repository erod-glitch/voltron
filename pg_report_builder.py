"""
pg_report_builder.py — v5.8
----------------------------------------------------------------
v5.8 changes:
- Single phase delivery — no Phase 1/Phase 2 split
- Exec profiles always render regardless of SFDC presence
  (flagged by source: SFDC confirmed / Gong / LinkedIn-researched / not found)
- Deal narrative always renders — threshold lowered to 1 Gong field
- One-pager text extraction hardened — no more dict repr in output
- One-pager case study links fixed — tries url / source_url / link
- Verification logs failures in report header, does not crash session
- Removed next_file() versioning — uses slug-based unique names
- _render_claim_annotations() handles string annotations
- normalize_raw() absorbs all known schema drift
- BUILDER_SCHEMA contract constant
"""

import datetime
import html as _html

from value_drivers import get_drivers, get_money_signals

NAVY       = "#1D2D50"
BLUE       = "#2E5CE5"
LIGHT_BLUE = "#EBF2FF"
WHITE      = "#FFFFFF"

LEG_COLORS = {"DATA": "#2E5CE5", "BUSINESS": "#D97706", "IT": "#16A34A", "ANALYST": "#7C3AED"}
LEG_ICONS  = {"DATA": "🗄️", "BUSINESS": "💼", "IT": "💻", "ANALYST": "📊"}

LEG_RULES = [
    ("DATA", ["cdo", "chief data", "chief analytics", "vp data", "vp of data", "director of data", "head of data", "data platform", "data engineering", "data governance", "data architecture", "data products", "data science", "analytics engineering", "data strategy"]),
    ("ANALYST", ["bi ", "business intelligence", "analytics", "reporting", "insights", "dashboard", "visualization", "data analyst", "business analyst", "analytics engineer", "center of excellence", "coe"]),
    ("IT", ["cio", "cto", "chief information", "chief technology", "vp technology", "vp of technology", "vp engineering", "director of technology", "director of it", "infrastructure", "platform", "architecture", "security", "cloud", "devops", "systems", "software engineering", "it "]),
    ("BUSINESS", ["ceo", "coo", "cfo", "cmo", "president", " gm ", "general manager", "svp", "evp", "managing director", "head of ", "operations", "finance", "marketing", "sales", "revenue", "strategy", "product"]),
]

BUILDER_SCHEMA = {
    "web_research": {
        "pain_points":             [{"text": "", "source": "", "url": ""}],
        "thoughtspot_fit_signals": [{"signal": "", "signal_tier": "HIGH|MEDIUM|LOW", "source": ""}],
        "recent_news":             [{"headline": "", "date": "", "url": ""}],
        "strategic_priorities":    [{"text": "", "source": ""}],
        "competitor_tools_in_use": [{"tool": "", "source": "", "url": ""}],
        "tech_stack":              [{"tool": "", "source": "", "url": ""}],
    },
    "tsumble": {
        "role_highlights": [{"title": "", "department": "", "location": "",
                              "date_posted": "", "url": "",
                              "thoughtspot_signal": "HIGH|MEDIUM|LOW|NONE"}],
        "hiring_trends":   [{"trend": "", "source": ""}],
    },
    "competitor_intel": {
        "tools_confirmed": [{"tool": "", "displacement_angle": "",
                              "thoughtspot_fit": "", "thoughtspot_angle": "",
                              "source": "", "url": ""}],
        "tools_suspected": [{"tool": "", "confidence": "low|medium|high",
                              "source": "", "url": ""}],
        "displacement_summary": "",
    },
    "case_studies": {
        "recommended_case_studies": [{"company": "", "url": "",
                                       "why_chosen": "", "key_metric": "",
                                       "pain_match": "",
                                       "tool_displacement_match": ""}],
    },
    "exec_profiles": {
        "executives": [{"name": "", "title": "", "linkedin_url": "",
                         "source_type": "sfdc|gong|linkedin|web",
                         "in_sfdc": True,
                         "bio_summary": {"text": "", "source": "", "url": ""},
                         "public_quotes":   [{"quote": "", "source": "", "url": ""}],
                         "recent_activity": [{"text": "", "source": "", "url": ""}],
                         "talking_points":  [{"point": "", "source": "", "url": ""}]}],
    },
    "sales_calls": {
        "signals":              [{"sentiment": "POSITIVE|NEGATIVE|COLD",
                                   "contact_name": "", "contact_email": "",
                                   "brief_summary": "", "next_steps": "",
                                   "recommended_action": ""}],
        "total_rows":           0,
        "meaningful_count":     0,
        "voicemail_count":      0,
        "no_content_count":     0,
        "consolidated_next_steps": [{"priority": "HIGH|MED|LOW|DO NOT CONTACT",
                                      "contact": "", "action": "", "owner": ""}],
    },
}

def _e(v) -> str:
    if v is None: return ""
    return _html.escape(str(v))

def _text(v, fallback: str = "") -> str:
    if v is None: return fallback
    if isinstance(v, str): return v or fallback
    if isinstance(v, dict):
        for key in ("text", "quote", "trend", "signal", "headline", "title", "point", "summary", "value"):
            if v.get(key) and isinstance(v[key], str): return v[key]
        return fallback
    if isinstance(v, list):
        parts = [_text(item) for item in v[:5] if _text(item)]
        return "; ".join(parts) or fallback
    return str(v) or fallback

def _src_badge(v) -> str:
    if not isinstance(v, dict): return ""
    src      = v.get("source") or v.get("source_url") or v.get("url") or ""
    src_type = v.get("source_type", "")
    if not src: return " <span style='color:#94A3B8;font-size:10px;'>⚠️ no source</span>"
    if src.startswith("http"):
        label = _e(src_type or "source")
        return f" <a href='{_e(src)}' target='_blank' style='color:#94A3B8;font-size:10px;text-decoration:none;'>↗ {label}</a>"
    return f" <span style='color:#94A3B8;font-size:10px;'>· {_e(src)}</span>"

def _render_item(v) -> str:
    if isinstance(v, dict): return _e(_text(v)) + _src_badge(v)
    return _e(str(v)) if v else ""

def _classify_leg(title: str) -> str:
    if not title: return "UNKNOWN"
    t = title.lower()
    for leg, keywords in LEG_RULES:
        if any(kw in t for kw in keywords): return leg
    return "UNKNOWN"

def _score_to_grade(score) -> str:
    try:
        n = float(score)
        # 6Sense Total Person Intent Score range: 0–500+
        # ICP score range: 0–125
        # Disambiguate by magnitude: > 125 = 6Sense intent sum
        if n > 125:
            # 6Sense intent score
            if n >= 200: return "A+"
            if n >= 100: return "A"
            if n >= 50:  return "B"
            return "C"
        else:
            # ICP score (TSA / TSE)
            if n >= 90: return "A+"
            if n >= 75: return "A"
            if n >= 60: return "B"
            return "C"
    except (TypeError, ValueError):
        return str(score) if score else "N/A"

def _decode_ts_date(val, fmt="%b %Y") -> str:
    from datetime import datetime
    if isinstance(val, dict):
        inner = val.get("v", val)
        if isinstance(inner, dict) and "s" in inner:
            try: return datetime.utcfromtimestamp(inner["s"]).strftime(fmt)
            except Exception: return str(inner)
    if val is None or val == "": return ""
    return str(val)

def _classify_deal(days_cold, activity_rows, cols) -> str:
    if days_cold is None: return "unknown"
    try: dc = int(days_cold)
    except (TypeError, ValueError): return "unknown"
    role_idx = cols.index("Activity Owner Role") if "Activity Owner Role" in cols else -1
    if role_idx >= 0 and activity_rows:
        roles = set()
        for r in activity_rows:
            if isinstance(r, list) and len(r) > role_idx: roles.add(str(r[role_idx]))
        ae_roles = {r for r in roles if r and any(k in r for k in ("AE", "Account Executive", " SE ", "Solutions Engineer", "Sales Engineer"))}
        if not ae_roles and roles - {"", "None", "nan"}: return "sdr_only"
    if dc > 365: return "ghost"
    if dc > 180: return "cold"
    if dc > 60:  return "stalled"
    return "live"

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*, *::before, *::after {{ box-sizing: border-box; }}
body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #f0f4ff; color: #0f172a; font-size: 14px; line-height: 1.6; }}
.page {{ max-width: 960px; margin: 32px auto; background: white; border-radius: 20px; box-shadow: 0 8px 48px rgba(29,45,80,0.12); overflow: hidden; }}
.hero {{ background: linear-gradient(135deg, #060d1f 0%, {NAVY} 45%, #2347c8 100%); padding: 52px 60px 44px; color: white; position: relative; overflow: hidden; }}
.hero::before {{ content: ''; position: absolute; top: -80px; right: -80px; width: 360px; height: 360px; background: radial-gradient(circle, rgba(46,92,229,0.25) 0%, transparent 70%); pointer-events: none; }}
.hero-eyebrow {{ font-size: 10px; font-weight: 700; letter-spacing: 2.5px; text-transform: uppercase; color: {LIGHT_BLUE}; margin-bottom: 14px; position: relative; z-index: 1; }}
.hero h1 {{ font-size: 40px; font-weight: 800; margin: 0 0 10px; line-height: 1.1; letter-spacing: -0.5px; position: relative; z-index: 1; }}
.hero-sub {{ font-size: 15px; opacity: 0.65; margin: 0 0 32px; position: relative; z-index: 1; }}
.hero-meta {{ display: flex; gap: 40px; position: relative; z-index: 1; padding-top: 24px; border-top: 1px solid rgba(255,255,255,0.12); flex-wrap: wrap; }}
.hero-meta-item {{ display: flex; flex-direction: column; gap: 3px; }}
.hero-meta-label {{ font-size: 9px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; opacity: 0.5; }}
.hero-meta-value {{ font-size: 13px; font-weight: 600; }}
.data-gaps {{ background: #FFF7ED; border-left: 4px solid #F97316; border-radius: 8px; padding: 12px 16px; margin: 16px 60px; font-size: 12px; color: #9a3412; }}
.data-gaps-label {{ font-weight: 700; margin-bottom: 4px; }}
.tab-nav {{ display: flex; gap: 6px; flex-wrap: wrap; padding: 20px 60px 0; background: {NAVY}; position: sticky; top: 0; z-index: 100; }}
.tab-btn {{ padding: 10px 18px; border-radius: 8px 8px 0 0; border: none; background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.65); cursor: pointer; font-size: 13px; font-weight: 500; font-family: inherit; transition: all 0.15s; }}
.tab-btn:hover {{ background: rgba(255,255,255,0.18); color: white; }}
.tab-btn.active {{ background: white; color: {NAVY}; font-weight: 700; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.content {{ padding: 52px 60px; }}
.section-header {{ display: flex; align-items: center; gap: 14px; margin: 44px 0 20px; padding-bottom: 14px; border-bottom: 2px solid {LIGHT_BLUE}; }}
.section-header:first-child {{ margin-top: 0; }}
.section-icon {{ width: 40px; height: 40px; background: linear-gradient(135deg, {BLUE}, #5b7fff); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; box-shadow: 0 2px 8px rgba(46,92,229,0.3); }}
.section-title {{ font-size: 20px; font-weight: 700; color: {NAVY}; margin: 0; letter-spacing: -0.3px; }}
.card {{ background: #f8faff; border: 1px solid #e2e8f8; border-radius: 12px; padding: 20px 24px; margin-bottom: 14px; }}
.card-highlight {{ background: linear-gradient(135deg, {LIGHT_BLUE}, #f0f7ff); border: 1px solid #c7d7f8; border-left: 4px solid {BLUE}; border-radius: 12px; padding: 20px 24px; margin-bottom: 14px; }}
.talking-point {{ background: linear-gradient(135deg, {NAVY} 0%, #1e3a8a 100%); color: white; border-radius: 16px; padding: 32px 36px; margin: 28px 0; position: relative; overflow: hidden; box-shadow: 0 4px 24px rgba(29,45,80,0.25); }}
.talking-point::before {{ content: '💡'; position: absolute; top: -16px; right: 24px; font-size: 100px; opacity: 0.06; pointer-events: none; }}
.talking-point-label {{ font-size: 9px; font-weight: 700; letter-spacing: 2.5px; text-transform: uppercase; color: {LIGHT_BLUE}; margin-bottom: 14px; }}
.talking-point p {{ font-size: 15px; line-height: 1.75; margin: 0; position: relative; z-index: 1; opacity: 0.95; }}
.why-box {{ background: #F0F7FF; border: 1px solid #C7D7F8; border-left: 4px solid {BLUE}; border-radius: 12px; padding: 20px 24px; margin: 16px 0; }}
.why-box-label {{ font-size: 10px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: {BLUE}; margin-bottom: 8px; }}
.why-box p {{ font-size: 14px; line-height: 1.7; margin: 0; color: #1e3a5f; }}
.stool-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 20px 0; }}
.stool-leg {{ border-radius: 14px; padding: 20px; border: 1.5px solid #e2e8f8; background: white; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }}
.stool-leg.empty {{ border: 2px dashed #fca5a5; background: #fff5f5; }}
.stool-leg.researched {{ border: 1.5px solid #FED7AA; background: #FFFBEB; }}
.stool-leg-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #f1f5f9; }}
.stool-leg-icon {{ width: 34px; height: 34px; border-radius: 9px; display: flex; align-items: center; justify-content: center; font-size: 17px; }}
.stool-leg-name {{ font-size: 11px; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase; }}
.stool-person {{ padding: 8px 0; border-bottom: 1px solid #f8faff; }}
.stool-person:last-child {{ border-bottom: none; padding-bottom: 0; }}
.stool-person-name {{ font-weight: 600; color: {NAVY}; font-size: 13px; }}
.stool-person-title {{ color: #64748b; font-size: 11px; margin-top: 2px; }}
.stool-person-source {{ color: #D97706; font-size: 10px; margin-top: 2px; font-style: italic; }}
.badge {{ display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 20px; margin-left: 6px; }}
.badge-champion {{ background: #fef9c3; color: #854d0e; }}
.badge-eb {{ background: #dcfce7; color: #166534; }}
.badge-researched {{ background: #FEF3C7; color: #92400E; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }}
thead tr {{ background: {NAVY}; color: white; }}
th {{ padding: 12px 16px; text-align: left; font-weight: 600; font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase; }}
td {{ padding: 11px 16px; border-bottom: 1px solid #f1f5f9; color: #334155; }}
tr:last-child td {{ border-bottom: none; }}
tr:nth-child(even) td {{ background: #f8faff; }}
.signal-card {{ border: 1px solid #e2e8f8; border-left: 4px solid {BLUE}; border-radius: 10px; padding: 18px 22px; margin-bottom: 12px; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
.signal-card-title {{ font-weight: 700; color: {NAVY}; font-size: 14px; margin-bottom: 8px; }}
.signal-card-meta {{ font-size: 12px; color: #64748b; margin-bottom: 8px; line-height: 1.5; }}
.money-row {{ display: flex; gap: 8px; font-size: 12px; margin: 5px 0; color: #334155; line-height: 1.5; }}
.opp-card {{ background: linear-gradient(135deg, #f0fdf4, #ecfdf5); border: 1px solid #86efac; border-radius: 10px; padding: 14px 18px; margin-bottom: 10px; font-size: 13px; }}
.exec-card {{ background: white; border: 1px solid #e2e8f8; border-radius: 12px; padding: 20px 24px; margin-bottom: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }}
.deal-narrative {{ background: #F8FAFF; border: 1px solid #E2E8F8; border-radius: 12px; padding: 18px 22px; margin-top: 16px; }}
.deal-narrative-label {{ font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }}
.pill {{ display: inline-block; padding: 3px 12px; border-radius: 20px; font-size: 11px; font-weight: 600; }}
.pill-blue {{ background: {LIGHT_BLUE}; color: {BLUE}; }}
.pill-green {{ background: #dcfce7; color: #166634; }}
.pill-orange {{ background: #ffedd5; color: #9a3412; }}
.pill-red {{ background: #fee2e2; color: #991b1b; }}
.flag {{ color: #dc2626; font-size: 12px; font-weight: 600; }}
@media print {{
    body {{ background: white; font-size: 11px; }}
    .page {{ max-width: 100%; margin: 0; border-radius: 0; box-shadow: none; }}
    .hero {{ padding: 36px 44px 30px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .content {{ padding: 36px 44px; }}
    .tab-nav {{ display: none !important; }}
    .tab-content {{ display: block !important; }}
    .section-header {{ break-after: avoid; }}
    .card, .card-highlight, .signal-card, .stool-grid, .talking-point, .opp-card, .exec-card, .why-box, .deal-narrative {{ break-inside: avoid; }}
    table {{ break-inside: avoid; }}
    .talking-point, .section-icon {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .page-break {{ page-break-before: always; }}
    .data-gaps {{ display: block !important; }}
}}
</style>
"""

_JS = """
<script>
function showTab(slug, tabId) {
    document.querySelectorAll('.tab-content-' + slug).forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn-' + slug).forEach(el => el.classList.remove('active'));
    document.getElementById(slug + '_' + tabId).classList.add('active');
    document.querySelector('.tab-btn-' + slug + '[data-tab="' + tabId + '"]').classList.add('active');
}
</script>
"""

def _section_header(icon: str, title: str) -> str:
    return f"<div class='section-header'><div class='section-icon'>{icon}</div><h2 class='section-title'>{_e(title)}</h2></div>"

def _card(content: str, highlight: bool = False) -> str:
    cls = "card-highlight" if highlight else "card"
    return f"<div class='{cls}'>{content}</div>"

def _signal_card(title: str, meta: str = "", bullets: list = None) -> str:
    out = f"<div class='signal-card'><div class='signal-card-title'>{title}</div>"
    if meta: out += f"<div class='signal-card-meta'>{meta}</div>"
    if bullets:
        for b in bullets: out += f"<div class='money-row'><span>{b}</span></div>"
    out += "</div>"
    return out

def _company_overview_section(raw: dict) -> str:
    wr   = raw.get("web_research", {})
    desc = wr.get("description", {})
    news = wr.get("recent_news", [])
    prio = wr.get("strategic_priorities", [])
    out  = _section_header("🏢", "Company Overview")
    desc_text = _text(desc)
    if desc_text:
        out += _card(f"<p style='margin:0;font-size:14px;line-height:1.7;'>{_e(desc_text)}{_src_badge(desc)}</p>")
    if prio:
        out += "<h3 style='font-size:14px;font-weight:700;color:#1D2D50;margin:20px 0 10px;'>Strategic Priorities</h3><ul style='margin:0;padding-left:20px;'>"
        for p in prio[:5]: out += f"<li style='margin-bottom:6px;'>{_render_item(p)}</li>"
        out += "</ul>"
    if news:
        out += "<h3 style='font-size:14px;font-weight:700;color:#1D2D50;margin:20px 0 10px;'>Recent News</h3>"
        for n in news[:4]:
            if isinstance(n, dict):
                headline = _e(n.get("headline", "") or n.get("title", ""))
                url      = n.get("url", "") or n.get("source_url", "")
                date     = _e(n.get("date", ""))
                link     = f"<a href='{_e(url)}' target='_blank' style='color:{BLUE};font-weight:600;'>{headline}</a>" if url else f"<b>{headline}</b>"
                out += f"<div class='card' style='padding:12px 16px;margin-bottom:8px;'>{link}"
                if date: out += f" <span style='color:#94A3B8;font-size:11px;'>· {date}</span>"
                out += "</div>"
            else:
                out += f"<div class='card' style='padding:12px 16px;margin-bottom:8px;'>{_e(str(n))}</div>"
    return out or _section_header("🏢", "Company Overview") + "<p style='color:#94A3B8;'>No company data available.</p>"

def _four_leg_stool_section(ts_data: dict, account_name: str, raw: dict = None) -> str:
    """
    v5.8: Exec profiles always populate legs regardless of SFDC presence.
    Source priority: SFDC confirmed → Gong → LinkedIn/web researched (flagged)
    Empty leg only if truly no executive found anywhere.
    """
    raw = raw or {}
    stakeholders = []

    # Priority 1: SFDC Executive Business Sponsor
    sfdc      = ts_data.get("sfdc_stakeholder", {})
    sfdc_rows = sfdc.get("data_rows", [])
    sfdc_cols = sfdc.get("column_names", [])
    exec_sponsor = ""
    if sfdc_rows and sfdc_cols:
        rec = dict(zip(sfdc_cols, sfdc_rows[0])) if isinstance(sfdc_rows[0], list) else sfdc_rows[0]
        exec_sponsor = rec.get("Executive Business Sponsor [For Calculation]", "") or ""
        if exec_sponsor and exec_sponsor.strip():
            stakeholders.append({
                "name": exec_sponsor.strip(),
                "title": "Executive Business Sponsor",
                "is_champion": False, "is_eb": False,
                "source": "sfdc", "in_sfdc": True,
            })

    # Priority 2: Gong Champion / EB from MEDDPICC
    medd      = ts_data.get("meddpicc_detail", {})
    medd_rows = medd.get("data_rows", [])
    medd_cols = medd.get("column_names", [])
    champion_name = eb_name = ""
    if medd_rows and medd_cols:
        rec = dict(zip(medd_cols, medd_rows[0])) if isinstance(medd_rows[0], list) else medd_rows[0]
        champion_name = rec.get("Opportunity Gong Champion", "") or ""
        eb_name       = rec.get("Opportunity Gong Economic Buyer", "") or ""
    if champion_name and champion_name.strip():
        existing = {s["name"].lower() for s in stakeholders}
        if champion_name.lower() not in existing:
            stakeholders.append({"name": champion_name.strip(), "title": "", "is_champion": True, "is_eb": False, "source": "gong", "in_sfdc": False})
    if eb_name and eb_name.strip():
        existing = {s["name"].lower() for s in stakeholders}
        if eb_name.lower() not in existing:
            stakeholders.append({"name": eb_name.strip(), "title": "", "is_champion": False, "is_eb": True, "source": "gong", "in_sfdc": False})

    # Priority 3: Exec profiles (LinkedIn/web research)
    _ep = raw.get("exec_profiles", {})
    execs = _ep.get("executives", _ep.get("profiles", []))
    execs = [e for e in execs if isinstance(e, dict) and e.get("name") and "Incumbent TBD" not in e.get("name", "")]
    for exec_data in execs:
        name  = exec_data.get("name", "").strip()
        title = exec_data.get("title", "")
        if not name: continue
        existing = {s["name"].lower() for s in stakeholders}
        if name.lower() not in existing:
            in_sfdc = exec_data.get("in_sfdc", False)
            source  = exec_data.get("source_type", "linkedin" if exec_data.get("linkedin_url") else "web")
            stakeholders.append({
                "name": name, "title": title or "",
                "is_champion": False, "is_eb": False,
                "source": source, "in_sfdc": in_sfdc,
            })

    # Apply champion/EB flags
    for s in stakeholders:
        if champion_name and champion_name.lower() in s["name"].lower(): s["is_champion"] = True
        if eb_name and eb_name.lower() in s["name"].lower(): s["is_eb"] = True

    legs: dict = {"DATA": [], "BUSINESS": [], "IT": [], "ANALYST": [], "UNKNOWN": []}
    for s in stakeholders: legs[_classify_leg(s["title"])].append(s)

    out = "<div class='stool-grid'>"
    for row_pair in [("DATA", "BUSINESS"), ("IT", "ANALYST")]:
        for leg_key in row_pair:
            color    = LEG_COLORS[leg_key]
            icon     = LEG_ICONS[leg_key]
            contacts = legs[leg_key]
            is_empty = len(contacts) == 0
            has_researched = any(not c.get("in_sfdc") and c.get("source") in ("linkedin", "web", "gong") for c in contacts)
            leg_class = "stool-leg empty" if is_empty else ("stool-leg researched" if has_researched else "stool-leg")
            out += f"<div class='{leg_class}'>"
            out += f"<div class='stool-leg-header'><div class='stool-leg-icon' style='background:{color}20;'>{icon}</div><span class='stool-leg-name' style='color:{color};'>{_e(leg_key)}</span></div>"
            if is_empty:
                out += f"<p class='flag' style='margin:0;font-size:11px;'>⚠️ No contact found — add to target list</p>"
                out += f"<p style='font-size:10px;color:#94A3B8;margin:4px 0 0;'>Research {_e(leg_key)} contacts at {_e(account_name)}</p>"
            else:
                for contact in contacts:
                    out += "<div class='stool-person'>"
                    out += f"<div class='stool-person-name'>{_e(contact['name'])}"
                    if contact["is_champion"]: out += " <span class='badge badge-champion'>🏆 Champion</span>"
                    if contact["is_eb"]:       out += " <span class='badge badge-eb'>💰 EB</span>"
                    if not contact.get("in_sfdc") and contact.get("source") in ("linkedin", "web"):
                        out += " <span class='badge badge-researched' title='Not found in recent SFDC activity data. A Salesforce record may still exist — verify before outreach.'>🔍 Not in activity</span>"
                    out += "</div>"
                    if contact["title"]: out += f"<div class='stool-person-title'>{_e(contact['title'])}</div>"
                    if not contact.get("in_sfdc"):
                        src_label = "Gong" if contact.get("source") == "gong" else "LinkedIn/Web — not in recent SFDC activity"
                        out += f"<div class='stool-person-source'>Source: {src_label}</div>"
                    out += "</div>"
            out += "</div>"
    out += "</div>"

    if legs["UNKNOWN"]:
        out += "<p style='font-size:11px;color:#94A3B8;margin-top:8px;'>⚠️ Unclassified (title not matched): " + ", ".join(_e(s["name"]) for s in legs["UNKNOWN"]) + "</p>"
    return out

def _stakeholder_map_section(ts_data: dict) -> str:
    sfdc      = ts_data.get("sfdc_stakeholder", {})
    rows      = sfdc.get("data_rows", [])
    cols      = sfdc.get("column_names", [])
    medd      = ts_data.get("meddpicc_detail", {})
    medd_rows = medd.get("data_rows", [])
    medd_cols = medd.get("column_names", [])
    flags     = ts_data.get("meddpicc_flags", {})
    flag_rows = flags.get("data_rows", [])
    flag_cols = flags.get("column_names", [])
    opp_owner = cs_name = exec_sponsor = ""
    if rows and cols:
        rec = dict(zip(cols, rows[0])) if isinstance(rows[0], list) else rows[0]
        opp_owner    = rec.get("Opportunity Owner Name", "")
        cs_name      = rec.get("Opportunity CS Name", "")
        exec_sponsor = rec.get("Executive Business Sponsor [For Calculation]", "")
    champion_name = eb_name = ""
    champion_valid = eb_valid = False
    if medd_rows and medd_cols:
        rec = dict(zip(medd_cols, medd_rows[0])) if isinstance(medd_rows[0], list) else medd_rows[0]
        champion_name = rec.get("Opportunity Gong Champion", "")
        eb_name       = rec.get("Opportunity Gong Economic Buyer", "")
    if flag_rows and flag_cols:
        rec = dict(zip(flag_cols, flag_rows[0])) if isinstance(flag_rows[0], list) else flag_rows[0]
        champion_valid = bool(rec.get("Opportunity Gong Champion Validated"))
        eb_valid       = bool(rec.get("Opportunity Gong Economic Buyer Validated"))

    def _fmt(val, label):
        if val and val.strip(): return _e(val)
        return f"<span class='flag'>⚠️ {_e(label)}</span>"

    if champion_valid and not champion_name:
        champ_display = "<span class='flag'>⚠️ Validated in Gong but name not captured</span>"
    elif champion_name:
        champ_display = _e(champion_name) + ("" if champion_valid else " <span class='flag'>(unconfirmed)</span>")
    else:
        champ_display = "<span class='flag'>⚠️ Not identified — prioritize discovery</span>"
    if eb_valid and not eb_name:
        eb_display = "<span class='flag'>⚠️ Validated in Gong but name not captured</span>"
    elif eb_name:
        eb_display = _e(eb_name) + ("" if eb_valid else " <span class='flag'>(unconfirmed)</span>")
    else:
        eb_display = "<span class='flag'>⚠️ Not identified — prioritize discovery</span>"
    crossref = ""
    if champion_name and exec_sponsor and champion_name.lower() != exec_sponsor.lower():
        crossref = (f"<div style='background:#fff7ed;border:1px solid #fed7aa;border-left:4px solid #f97316;border-radius:8px;padding:10px 14px;margin-top:12px;font-size:12px;color:#9a3412;'>"
                    f"⚠️ Gong Champion ({_e(champion_name)}) differs from SFDC Exec Sponsor ({_e(exec_sponsor)}) — verify.</div>")
    out  = "<table><thead><tr><th>Role</th><th>Name</th></tr></thead><tbody>"
    out += f"<tr><td>🏆 Champion</td><td>{champ_display}</td></tr>"
    out += f"<tr><td>💰 Economic Buyer</td><td>{eb_display}</td></tr>"
    out += f"<tr><td>Executive Sponsor</td><td>{_fmt(exec_sponsor, 'Not in SFDC')}</td></tr>"
    out += f"<tr><td>Opportunity Owner</td><td>{_fmt(opp_owner, 'Not found')}</td></tr>"
    out += f"<tr><td>CS Name</td><td>{_fmt(cs_name, 'Not assigned')}</td></tr>"
    out += "</tbody></table>" + crossref
    return out

def _render_claim_annotations(annotations: list) -> str:
    if not annotations: return ""
    normalized = []
    for ann in annotations:
        if isinstance(ann, dict):
            normalized.append(ann)
        elif isinstance(ann, str) and ann.strip():
            parts = ann.split(" — sourced from ", 1)
            normalized.append({
                "claim":       parts[0].strip(),
                "basis":       parts[1].strip() if len(parts) > 1 else "",
                "source":      "",
                "source_type": "research",
                "confidence":  "inferred",
                "flag":        "",
            })
    if not normalized: return ""
    flags            = [a for a in normalized if a.get("flag")]
    unverified_count = len(flags)
    out  = "<details style='margin-top:8px;'>"
    out += (f"<summary style='cursor:pointer;font-size:11px;font-weight:700;color:#64748b;"
            f"padding:6px 10px;background:#f1f5f9;border-radius:6px;list-style:none;"
            f"display:flex;align-items:center;gap:8px;'>🔍 Claim sources ({len(normalized)})")
    if unverified_count:
        out += f" <span style='background:#FEF3C7;color:#D97706;padding:2px 8px;border-radius:10px;font-size:10px;'>⚠️ {unverified_count} to verify</span>"
    out += "</summary>"
    out += "<div style='margin-top:8px;border:1px solid #e2e8f8;border-radius:8px;overflow:hidden;'>"
    for ann in normalized:
        claim      = _e(ann.get("claim", ""))
        basis      = _e(ann.get("basis", ""))
        source     = ann.get("source", "")
        src_type   = _e(ann.get("source_type", ""))
        confidence = ann.get("confidence", "confirmed")
        flag       = ann.get("flag", "")
        bg_color     = "#FFF7ED" if flag else "#F8FAFF"
        border_color = "#FED7AA" if flag else "#E2E8F8"
        conf_color   = "#16A34A" if confidence == "confirmed" else "#D97706" if confidence == "inferred" else "#94A3B8"
        out += f"<div style='padding:10px 14px;background:{bg_color};border-bottom:1px solid {border_color};font-size:12px;'>"
        out += f"<div style='margin-bottom:4px;'><span style='font-weight:600;color:{NAVY};'>Claim:</span> <span style='font-style:italic;color:#334155;'>{claim}</span></div>"
        if basis: out += f"<div style='margin-bottom:4px;color:#475569;'><span style='font-weight:600;'>Basis:</span> {basis}</div>"
        out += "<div style='display:flex;gap:12px;align-items:center;flex-wrap:wrap;'>"
        if source:
            if source.startswith("http"): out += f"<a href='{_e(source)}' target='_blank' style='color:{BLUE};font-size:11px;'>↗ {src_type or 'source'}</a>"
            else: out += f"<span style='color:#64748b;font-size:11px;'>📄 {_e(source)}</span>"
        out += f"<span style='color:{conf_color};font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;'>{_e(confidence)}</span>"
        if flag: out += f"<span style='background:#FEF3C7;color:#D97706;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;'>⚠️ {_e(flag)}</span>"
        out += "</div></div>"
    out += "</div></details>"
    return out

def _talking_point_section(account_name: str, matched_drivers: list, raw: dict) -> str:
    if not matched_drivers:
        return "<div class='card'><p class='flag' style='margin:0;'>⚠️ No matched drivers — select manually.</p></div>"
    top_key        = matched_drivers[0].get("key", "") if isinstance(matched_drivers[0], dict) else matched_drivers[0]
    signals        = get_money_signals(top_key)
    driver         = get_drivers(top_key)
    label          = driver.get("label", top_key) if driver else top_key
    money_in       = signals.get("money_in", [])
    money_out      = signals.get("money_out", [])
    pain_pts       = driver.get("pain_points", []) if driver else []
    financial_hook = money_in[0] if money_in else (money_out[0] if money_out else "")
    pain_signal    = pain_pts[0] if pain_pts else ""
    _ep        = raw.get("exec_profiles", {})
    execs      = _ep.get("executives", _ep.get("profiles", []))
    exec_name  = execs[0].get("name", "") if execs and isinstance(execs[0], dict) else ""
    addressee  = _e(exec_name) if exec_name else f"your team at {_e(account_name)}"
    tp_text = ""
    if pain_signal: tp_text += f"A lot of companies like {_e(account_name)} are dealing with {_e(pain_signal.lower())}. "
    if financial_hook:
        tp_text += (f"The way {addressee} is thinking about this space, there's a real opportunity to {_e(financial_hook.lower())}. "
                    f"That's exactly where ThoughtSpot tends to land — not as another BI tool, but as the layer that makes your "
                    f"data investment actually pay off. Worth a conversation to see if the timing is right?")
    else:
        tp_text += "<span style='color:#93C5FD;'>⚠️ No financial signal found — generic value used</span>"
    out = f"<div class='talking-point'><div class='talking-point-label'>💡 Talking Point · {_e(label)}</div><p>{tp_text}</p></div>"
    wr           = raw.get("web_research", {})
    pain_sources = [p for p in wr.get("pain_points", []) if isinstance(p, dict) and p.get("source")]
    ci           = raw.get("competitor_intel", {})
    comp_sources = [t for t in ci.get("tools_confirmed", []) if isinstance(t, dict) and t.get("source")]
    citations = []
    if pain_signal and pain_sources:
        src = pain_sources[0]
        citations.append({"claim": pain_signal, "source": src.get("url", src.get("source", "")), "source_type": src.get("source_type", "web"), "confidence": "confirmed", "flag": ""})
    elif pain_signal:
        citations.append({"claim": pain_signal, "source": "inferred from web research", "source_type": "inferred", "confidence": "inferred", "flag": "VERIFY BEFORE USING"})
    if financial_hook:
        citations.append({"claim": financial_hook, "source": f"ThoughtSpot value driver: {label}", "source_type": "value_driver", "confidence": "confirmed", "flag": ""})
    for cs in comp_sources[:2]:
        citations.append({"claim": f"{cs.get('tool', '')} identified in tech stack", "source": cs.get("url", cs.get("source", "")), "source_type": cs.get("source_type", "job_posting"), "confidence": "confirmed" if cs.get("url") else "inferred", "flag": "" if cs.get("url") else "VERIFY BEFORE USING"})
    if not citations:
        out += "<div style='background:#FFF7ED;border-left:4px solid #F97316;border-radius:8px;padding:10px 14px;margin-top:8px;font-size:12px;color:#9a3412;'>⚠️ No source evidence found — verify claims manually before using.</div>"
    else:
        out += _render_claim_annotations(citations)
    return out

def _why_ts_section(matched_drivers: list, raw: dict, why_now: str = "", why_anything: str = "", why_thoughtspot: str = "") -> str:
    out = ""
    if why_now and why_now.strip():
        out += (f"<div class='why-box'><div class='why-box-label'>⚡ Why Now</div><p>{_e(why_now)}</p></div>")
    if why_anything and why_anything.strip():
        out += (f"<div class='why-box' style='border-left-color:#D97706;background:#FFFBEB;border-color:#FCD34D;'>"
                f"<div class='why-box-label' style='color:#D97706;'>🎯 Why Anything</div>"
                f"<p>{_e(why_anything)}</p></div>")
    if why_thoughtspot and why_thoughtspot.strip():
        out += (f"<div class='why-box' style='border-left-color:#16A34A;background:#F0FDF4;border-color:#86EFAC;'>"
                f"<div class='why-box-label' style='color:#16A34A;'>✅ Why ThoughtSpot</div>"
                f"<p>{_e(why_thoughtspot)}</p></div>")
    if not matched_drivers:
        out += "<p style='color:#94A3B8;'>No value driver data available.</p>"
        return out
    for m in matched_drivers:
        key    = m.get("key", "") if isinstance(m, dict) else m
        driver = get_drivers(key)
        if not driver: continue
        label     = driver.get("label", key)
        pain_pts  = driver.get("pain_points", [])
        money_in  = driver.get("money_in", [])
        money_out = driver.get("money_out", [])
        evidence  = m.get("evidence", []) if isinstance(m, dict) else []
        bullets   = [f"💰 {b}" for b in money_in[:2]] + [f"🛡️ {b}" for b in money_out[:2]]
        meta = ""
        if evidence:
            meta += f"<span style='color:#64748b;font-size:11px;'>Matched signals: {_e('; '.join(str(e) for e in evidence[:3]))}</span><br>"
            meta += f"<span style='font-size:10px;color:#16A34A;font-weight:600;'>✅ Grounded in account research</span>"
        else:
            meta += f"<span style='font-size:10px;color:#DC2626;font-weight:600;'>⚠️ No account-specific evidence — verify before presenting</span>"
        if pain_pts: meta += f"<br><span style='color:#475569;font-size:12px;'>Pain addressed: {_e(pain_pts[0])}</span>"
        out += _signal_card(_e(label), meta, bullets)
    return out

def _hiring_signals_section(raw: dict) -> str:
    t     = raw.get("tsumble", {})
    roles = t.get("role_highlights", [])
    if not roles: return "<p style='color:#94A3B8;'>No role data available.</p>"
    total  = t.get("total_open_roles", "")
    trends = t.get("hiring_trends", [])
    out    = ""
    if total or trends:
        meta = f"<b>{total} open roles</b>" if total else ""
        if trends:
            trend_text = "; ".join((tr.get("trend", "") if isinstance(tr, dict) else str(tr)) for tr in trends[:2])
            meta += (f" · {trend_text}" if meta else trend_text)
        if meta: out += _card(f"<p style='margin:0;font-size:13px;'>{meta}</p>")
    out += "<table><thead><tr><th>Role</th><th>Dept</th><th>Location</th><th>Posted</th><th>Signal</th></tr></thead><tbody>"
    for r in roles[:15]:
        if isinstance(r, dict):
            title  = _e(r.get("title", ""))
            url    = r.get("url", "") or r.get("source_url", "")
            title_link = f"<a href='{_e(url)}' target='_blank' style='color:{BLUE};'>{title} ↗</a>" if url else title
            signal = r.get("thoughtspot_signal", r.get("signal_tier", ""))
            signal_html = ""
            if signal == "HIGH": signal_html = "<span style='color:#16A34A;font-weight:700;font-size:11px;'>🔥 HIGH</span>"
            elif signal == "MEDIUM": signal_html = "<span style='color:#D97706;font-size:11px;'>⚡ MED</span>"
            out += f"<tr><td>{title_link}</td><td>{_e(r.get('department', ''))}</td><td>{_e(r.get('location', ''))}</td><td>{_e(r.get('date_posted', ''))}</td><td>{signal_html}</td></tr>"
        else:
            out += f"<tr><td colspan='5'>{_e(str(r))}</td></tr>"
    out += "</tbody></table>"
    return out

def _competitor_section(raw: dict, matched_drivers: list) -> str:
    ci        = raw.get("competitor_intel", {})
    confirmed = ci.get("tools_confirmed", [])
    suspected = ci.get("tools_suspected", ci.get("tools_inferred", []))
    disp_sum  = ci.get("displacement_summary", "")
    if not confirmed and not suspected and not disp_sum:
        return "<p style='color:#94A3B8;'>No competitor data available.</p>"
    out = ""
    if confirmed:
        for t in confirmed:
            if not isinstance(t, dict): continue
            tool     = _e(t.get("tool", ""))
            evidence = _e(_text(t.get("evidence", "")))
            angle    = _e(t.get("displacement_angle", "") or t.get("thoughtspot_angle", ""))
            fit      = _e(t.get("thoughtspot_fit", ""))
            src      = _src_badge(t)
            meta_parts = []
            if evidence: meta_parts.append(f"<b>Evidence:</b> {evidence[:180]}{src}")
            meta_parts.append(f"<b>TS Angle:</b> <span style='color:{BLUE};'>{angle}</span>" if angle else "<span class='flag'>⚠️ No ThoughtSpot angle identified</span>")
            if fit: meta_parts.append(f"<b>Fit Signal:</b> <span style='color:#16A34A;'>{fit}</span>")
            if not t.get("source") and not t.get("url"):
                meta_parts.append("<span class='flag' style='font-size:10px;'>⚠️ No source — inferred, verify before presenting</span>")
            out += _signal_card(tool, "<br>".join(meta_parts))
    if suspected:
        out += "<h3 style='font-size:13px;font-weight:700;color:#64748B;margin:16px 0 8px;letter-spacing:0.5px;text-transform:uppercase;'>Suspected / Inferred</h3><ul style='margin:0;padding-left:16px;'>"
        for t in suspected[:5]:
            if isinstance(t, dict):
                conf = t.get("confidence", "")
                pill_color = "pill-red" if conf == "high" else "pill-orange" if conf == "medium" else "pill-blue"
                out += f"<li style='margin-bottom:6px;font-size:13px;'><b>{_e(t.get('tool', ''))}</b> <span class='pill {pill_color}'>{conf}</span>{'— ' + _e(_text(t.get('evidence', ''))) if t.get('evidence') else ''}</li>"
        out += "</ul>"
    if disp_sum: out += _card(f"<p style='margin:0;font-size:13px;'><b>Summary:</b> {_e(str(disp_sum))}</p>")
    return out
def _build_deal_narrative(ts_data: dict) -> str:
    """
    v5.8: Always renders — threshold lowered to 1 Gong field.
    Returns narrative HTML from richest MEDDPICC row.
    Returns empty string only if truly no MEDDPICC data exists at all.
    """
    medd      = ts_data.get("meddpicc_detail", {})
    medd_rows = medd.get("data_rows", [])
    medd_cols = medd.get("column_names", [])
    if not medd_rows or not medd_cols: return ""

    gong_text_fields = [
        "Opportunity Gong Champion",
        "Opportunity Gong Economic Buyer",
        "Opportunity Gong Identify Pain",
        "Opportunity Gong Metrics",
        "Opportunity Gong Decision Criteria",
        "Opportunity Gong Decision Process",
        "Opportunity Gong Paper Process",
        "Opportunity Gong Competition",
        "Opportunity Gong Data Readiness",
    ]

    def richness(row):
        rec = dict(zip(medd_cols, row)) if isinstance(row, list) else row
        return sum(1 for f in gong_text_fields if rec.get(f) and str(rec.get(f)).strip() not in ["", "None", "nan"])

    richest_row = max(medd_rows, key=richness)
    rec = dict(zip(medd_cols, richest_row)) if isinstance(richest_row, list) else richest_row

    # v5.8: threshold lowered to 1 (was 3)
    if richness(richest_row) == 0: return ""

    opp_name = _e(rec.get("Opportunity Name", "this opportunity"))
    out  = f"<div class='deal-narrative'>"
    out += f"<div class='deal-narrative-label'>🎙 Gong Intelligence — {opp_name}</div>"

    field_labels = [
        ("Opportunity Gong Champion",        "Champion"),
        ("Opportunity Gong Economic Buyer",  "Economic Buyer"),
        ("Opportunity Gong Identify Pain",   "Identified Pain"),
        ("Opportunity Gong Metrics",         "Metrics"),
        ("Opportunity Gong Decision Criteria","Decision Criteria"),
        ("Opportunity Gong Decision Process", "Decision Process"),
        ("Opportunity Gong Paper Process",   "Paper Process"),
        ("Opportunity Gong Competition",     "Competition"),
        ("Opportunity Gong Data Readiness",  "Data Readiness"),
    ]

    rendered_any = False
    for field, label in field_labels:
        val = rec.get(field, "")
        if val and str(val).strip() and str(val).strip() not in ["None", "nan"]:
            out += (f"<div style='margin-bottom:10px;'>"
                    f"<span style='font-size:11px;font-weight:700;color:{NAVY};'>{_e(label)}:</span> "
                    f"<span style='font-size:13px;color:#334155;line-height:1.6;'>{_e(str(val))}</span>"
                    f"</div>")
            rendered_any = True

    out += "</div>"
    return out if rendered_any else ""

def _deal_story_section(ts_data: dict, raw: dict = None) -> str:
    import re as _re
    raw       = raw or {}
    ds_result = ts_data.get("deal_stage", {})
    ft_result  = ts_data.get("deal_funnel_timing", {})
    act_result = ts_data.get("activity_history_detail", ts_data.get("activity_history", {}))
    opp_detail = ts_data.get("opp_detail", {})
    ds_rows  = ds_result.get("data_rows", [])
    ds_cols  = ds_result.get("column_names", [])
    ft_rows  = ft_result.get("data_rows", [])
    ft_cols  = ft_result.get("column_names", [])
    act_rows = act_result.get("data_rows", [])
    act_cols = act_result.get("column_names", [])
    od_rows  = opp_detail.get("data_rows", [])
    od_cols  = opp_detail.get("column_names", [])

    if not ds_rows: return "<p style='color:#94A3B8;'>No deal stage data available.</p>"

    out = ""

    # Loop ALL opps
    for ds_rec in [dict(zip(ds_cols, r)) if isinstance(r, list) else r for r in ds_rows]:
        opp_name = (ds_rec.get("Opportunity Name", "") or
                    ds_rec.get("SFDC Opp Name URL", "") or
                    ds_rec.get("Opportunity Name with url", "") or "")
        opp_name = _e(opp_name) if opp_name else "(opportunity)"
        stage    = _e(ds_rec.get("Opportunity Stage Maximum Name", ""))
        owner    = _e(ds_rec.get("Opportunity Owner Name", ""))
        pq       = ds_rec.get("Opportunity Pipeline Qualified Flag", False)
        created  = _decode_ts_date(ds_rec.get("Month(Opportunity Created Date)",
                    ds_rec.get("Opportunity Created Date", "")))
        last_act = _decode_ts_date(ds_rec.get("Month(Opportunity Last Activity Date)",
                    ds_rec.get("Opportunity Last Activity Date", "")))

        CLASS_BADGES = {
            "ghost":    ("<span class='pill' style='background:#FEF2F2;color:#991B1B;margin-left:6px;'>👻 Ghost Opp</span>", "#DC2626"),
            "cold":     ("<span class='pill' style='background:#FFF7ED;color:#92400E;margin-left:6px;'>🧊 Gone Cold</span>", "#D97706"),
            "stalled":  ("<span class='pill' style='background:#FFFBEB;color:#B45309;margin-left:6px;'>⏸ Stalled</span>", "#B45309"),
            "sdr_only": ("<span class='pill' style='background:#EFF6FF;color:#1D4ED8;margin-left:6px;'>📞 SDR-Only</span>", "#1D4ED8"),
            "live":     ("", "#16A34A"),
            "unknown":  ("", "#64748B"),
        }
        badge_html, dc_color = CLASS_BADGES.get("unknown", ("", "#64748B"))

        out += f"<div class='opp-card' style='margin-bottom:10px;'>"
        out += f"<b style='font-size:14px;color:{NAVY};'>{opp_name}</b>"
        if stage:      out += f" <span class='pill pill-blue' style='margin-left:8px;'>{stage}</span>"
        if pq:         out += " <span class='pill pill-green'>✅ Pipeline Qualified</span>"
        if badge_html: out += badge_html

        meta = []
        if owner:    meta.append(f"Owner: {owner}")
        if created:  meta.append(f"Created: {created}")
        if last_act: meta.append(f"Last activity: {last_act}")
        if meta:
            out += f"<div style='font-size:12px;color:#64748b;margin-top:6px;line-height:1.8;'>" + " &nbsp;·&nbsp; ".join(meta) + "</div>"
        out += "</div>"

    # MEDDPICC narrative — always render if any data exists
    deal_narrative = _build_deal_narrative(ts_data)
    if deal_narrative:
        out += deal_narrative

    # Agent-authored prose narrative (from Step 4.5 synthesis)
    agent_narrative = raw.get("deal_narrative", "")
    if agent_narrative:
        out += agent_narrative

    # Opp detail (days cold etc) from first od_row
    days_cold = prior_close = None
    if od_rows and od_cols:
        od_rec      = dict(zip(od_cols, od_rows[0])) if isinstance(od_rows[0], list) else od_rows[0]
        days_cold   = od_rec.get("Total Days from Account last touch", od_rec.get("Days from Account last touch"))
        prior_close = _decode_ts_date(od_rec.get("Month(Opportunity Prior Close Date)", od_rec.get("Opportunity Prior Close Date", "")))

    # Activity table
    if act_rows:
        type_icons = {"Call": "📞", "Email": "📧", "Live Chat": "💬", "Meeting": "📅", "Task": "✅", "Event": "🎪", "LinkedIn": "🔗"}
        date_idx  = next((act_cols.index(c) for c in ["Month(Activity Created Date)", "Activity Created Date"] if c in act_cols), -1)
        type_idx  = act_cols.index("Activity Type")      if "Activity Type"      in act_cols else -1
        subj_idx  = act_cols.index("Activity Subject")   if "Activity Subject"   in act_cols else -1
        owner_idx = act_cols.index("Activity Owner Name") if "Activity Owner Name" in act_cols else -1
        out += f"<p style='font-size:11px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;margin:20px 0 10px;'>All Account Activity ({len(act_rows)} touches)</p>"
        out += f"<table style='width:100%;border-collapse:collapse;font-size:12px;'><thead><tr style='background:#F8FAFF;'>"
        out += f"<th style='padding:8px 10px;text-align:left;color:{NAVY};font-weight:700;border-bottom:2px solid #E2E8F0;white-space:nowrap;'>Date</th>"
        out += f"<th style='padding:8px 10px;text-align:left;color:{NAVY};font-weight:700;border-bottom:2px solid #E2E8F0;'>Type</th>"
        out += f"<th style='padding:8px 10px;text-align:left;color:{NAVY};font-weight:700;border-bottom:2px solid #E2E8F0;'>Prospect Contact</th>"
        out += f"<th style='padding:8px 10px;text-align:left;color:{NAVY};font-weight:700;border-bottom:2px solid #E2E8F0;'>Subject / Notes</th>"
        out += f"<th style='padding:8px 10px;text-align:left;color:{NAVY};font-weight:700;border-bottom:2px solid #E2E8F0;'>TS Owner</th>"
        out += "</tr></thead><tbody>"
        for i, row in enumerate(act_rows[:50]):
            row = row if isinstance(row, list) else list(row.values())
            date_val   = _decode_ts_date(row[date_idx])  if date_idx  >= 0 and len(row) > date_idx  else ""
            atype_raw  = str(row[type_idx])               if type_idx  >= 0 and len(row) > type_idx  else ""
            subj_raw   = str(row[subj_idx])               if subj_idx  >= 0 and len(row) > subj_idx  else ""
            owner_val  = str(row[owner_idx])              if owner_idx >= 0 and len(row) > owner_idx else ""
            subj_clean = _re.sub(r'\[Gong Templates\]\s*|\[Outreach\]\s*|\[\w+\]\s*', "", subj_raw).strip()
            prospect   = "Unknown Contact"
            nm = _re.search(r'(?:Call|Email|to|for)\s+([A-Z][a-z]+ [A-Z][a-z]+)', subj_raw)
            if nm: prospect = nm.group(1)
            icon = type_icons.get(atype_raw, "📌")
            bg   = "#FAFBFF" if i % 2 == 0 else "#FFFFFF"
            dim  = "opacity:0.55;" if owner_val in ("Salesforce Automation", "") else ""
            out += (f"<tr style='background:{bg};{dim}'>"
                    f"<td style='padding:7px 10px;color:#64748B;border-bottom:1px solid #F1F5F9;white-space:nowrap;'>{_e(date_val)}</td>"
                    f"<td style='padding:7px 10px;color:#475569;border-bottom:1px solid #F1F5F9;white-space:nowrap;'>{icon} {_e(atype_raw)}</td>"
                    f"<td style='padding:7px 10px;font-weight:600;color:{NAVY};border-bottom:1px solid #F1F5F9;'>{_e(prospect)}</td>"
                    f"<td style='padding:7px 10px;color:#475569;border-bottom:1px solid #F1F5F9;'>{_e(subj_clean)}</td>"
                    f"<td style='padding:7px 10px;color:#64748B;border-bottom:1px solid #F1F5F9;font-style:italic;'>{_e(owner_val)}</td>"
                    f"</tr>")
        out += "</tbody></table>"

    # Funnel timing
    ft_rec = {}
    has_ft_values = False
    if ft_rows:
        ft_rec = dict(zip(ft_cols, ft_rows[0])) if isinstance(ft_rows[0], list) else ft_rows[0]
        has_ft_values = any(
            ft_rec.get(k, 0) not in (0, None, "", "0")
            for k in ["Total f Opportunity S1 Duration", "Total f Opportunity S2 Duration",
                      "Total f Opportunity S3 Duration", "Total f Opportunity M0 to S7 Duration",
                      "f Opportunity S1 Duration", "f Opportunity S2 Duration",
                      "f Opportunity S3 Duration", "f Opportunity M0 to S7 Duration"]
        )
    if has_ft_values:
        timing_map = [
            ("Total f Opportunity S1 Duration", "f Opportunity S1 Duration", "S1"),
            ("Total f Opportunity S2 Duration", "f Opportunity S2 Duration", "S2"),
            ("Total f Opportunity S3 Duration", "f Opportunity S3 Duration", "S3"),
            ("Total f Opportunity M0 to S7 Duration", "f Opportunity M0 to S7 Duration", "M0→S7"),
            ("Opportunity Current Stage Duration", "Opportunity Current Stage Duration", "Current Stage"),
        ]
        out += f"<h3 style='font-size:13px;font-weight:700;color:#64748B;margin:20px 0 8px;'>Funnel Timing</h3>"
        out += f"<table style='border-collapse:collapse;font-size:12px;'><thead><tr style='background:#F8FAFF;'><th style='padding:7px 12px;color:{NAVY};font-weight:700;border-bottom:2px solid #E2E8F0;'>Stage</th><th style='padding:7px 12px;color:{NAVY};font-weight:700;border-bottom:2px solid #E2E8F0;'>Days</th></tr></thead><tbody>"
        for primary_key, fallback_key, label in timing_map:
            val = ft_rec.get(primary_key) or ft_rec.get(fallback_key)
            if val not in (0, None, "", "0"):
                out += (f"<tr><td style='padding:6px 12px;color:#475569;border-bottom:1px solid #F1F5F9;'>{label}</td>"
                        f"<td style='padding:6px 12px;font-weight:600;color:{NAVY};border-bottom:1px solid #F1F5F9;'>{_e(str(val))}</td></tr>")
        out += "</tbody></table>"

    # Re-engagement context if cold
    if days_cold is not None:
        try:
            dc = int(days_cold)
        except (TypeError, ValueError):
            dc = 0
        if dc > 60:
            dc_color = "#DC2626" if dc > 365 else "#D97706" if dc > 180 else "#B45309"
            dc_label = "ghost" if dc > 365 else "cold" if dc > 180 else "stalled"
            prior_li = f"<li>Prior close date <strong>{prior_close}</strong> has passed — update or re-create opp after re-qualification</li>" if prior_close else ""
            out += f"""
<div style='margin-top:20px;background:#FFF7ED;border-left:4px solid #D97706;border-radius:8px;padding:16px 18px;'>
  <div style='font-size:11px;font-weight:700;color:#D97706;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;'>⚡ Re-Engagement Context</div>
  <ul style='margin:0;padding-left:18px;font-size:13px;color:#475569;line-height:1.7;'>
    <li>Account {dc_label} for <strong style='color:{dc_color};'>{dc}d</strong> — treat as net-new re-engage</li>
    <li>Recommend AE-led outreach with a fresh angle tied to what has changed since last contact</li>
  </ul>
</div>
<div style='margin-top:12px;background:#FFF1F2;border-left:4px solid #E11D48;border-radius:8px;padding:14px 16px;'>
  <div style='font-size:11px;font-weight:700;color:#E11D48;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;'>⚠️ Risk Flags</div>
  <ul style='margin:0;padding-left:18px;font-size:12px;color:#475569;line-height:1.6;'>
    <li>Verify opp owner is still active and aware before any outreach</li>
    {prior_li}
    <li>Re-qualify from scratch — do not assume prior context carries over</li>
  </ul>
</div>"""
    return out

def _sales_call_section(ts_data: dict) -> str:
    flags     = ts_data.get("meddpicc_flags", {})
    detail    = ts_data.get("meddpicc_detail", {})
    flag_rows = flags.get("data_rows", [])
    flag_cols = flags.get("column_names", [])
    det_rows  = detail.get("data_rows", [])
    det_cols  = detail.get("column_names", [])
    if not flag_rows and not det_rows: return "<p style='color:#94A3B8;'>No sales call data available.</p>"
    flag_rec = {}
    det_rec  = {}
    if flag_rows and flag_cols: flag_rec = dict(zip(flag_cols, flag_rows[0])) if isinstance(flag_rows[0], list) else flag_rows[0]
    if det_rows and det_cols:   det_rec  = dict(zip(det_cols,  det_rows[0]))  if isinstance(det_rows[0], list)  else det_rows[0]
    gong_fields = [
        ("Opportunity Gong Champion Validated",        "Opportunity Gong Champion",         "Champion"),
        ("Opportunity Gong Economic Buyer Validated",  "Opportunity Gong Economic Buyer",   "Economic Buyer"),
        ("Opportunity Gong Identify Pain Validated",   "Opportunity Gong Identify Pain",    "Identified Pain"),
        ("Opportunity Gong Metrics Validated",         "Opportunity Gong Metrics",          "Metrics"),
        ("Opportunity Gong Decision Criteria Validated","Opportunity Gong Decision Criteria","Decision Criteria"),
        ("Opportunity Gong Decision Process Validated", "Opportunity Gong Decision Process", "Decision Process"),
        ("Opportunity Gong Paper Process Validated",   "Opportunity Gong Paper Process",    "Paper Process"),
        ("Opportunity Gong Competition Validated",     "Opportunity Gong Competition",      "Competition"),
        ("Opportunity Gong Data Readiness Validated",  "Opportunity Gong Data Readiness",   "Data Readiness"),
    ]
    out  = "<table><thead><tr><th>Signal</th><th>Validated</th><th>Detail</th></tr></thead><tbody>"
    for flag_field, detail_field, label in gong_fields:
        validated   = flag_rec.get(flag_field, False)
        detail_text = det_rec.get(detail_field, "")
        v_display   = "<span style='color:#16a34a;font-weight:700;'>✅ Yes</span>" if validated else "<span style='color:#94A3B8;'>❌ No</span>"
        if label in ("Champion", "Economic Buyer") and validated and not detail_text:
            d_display = f"<span class='flag'>⚠️ {label} validated but name not captured</span>"
        else:
            d_display = _e(detail_text) if detail_text else "—"
        out += f"<tr><td><b>{_e(label)}</b></td><td>{v_display}</td><td>{d_display}</td></tr>"
    out += "</tbody></table>"
    return out

def _gong_calls_section(raw: dict) -> str:
    calls = raw.get("sales_calls", {})
    if not calls: return "<p style='color:#94A3B8;'>No Gong call data available.</p>"
    signals    = calls.get("signals",         calls.get("call_summaries", []))
    total      = calls.get("total_rows",      calls.get("total_calls_found", 0))
    meaningful = calls.get("meaningful_count", calls.get("meaningful_calls", 0))
    voicemail  = calls.get("voicemail_count",  calls.get("voicemail_calls", 0))
    no_content = calls.get("no_content_count", calls.get("no_content_calls", 0))
    next_steps = calls.get("consolidated_next_steps", [])

    if not signals and total == 0:
        return "<p style='color:#94A3B8;'>No Gong call data available — sales_call_analyzer may not have completed.</p>"

    out = _card(
        f"<div style='display:flex;gap:32px;flex-wrap:wrap;'>"
        f"<div><div style='font-size:22px;font-weight:800;color:{NAVY};'>{total}</div><div style='font-size:10px;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;'>Total</div></div>"
        f"<div><div style='font-size:22px;font-weight:800;color:#16A34A;'>{meaningful}</div><div style='font-size:10px;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;'>Meaningful</div></div>"
        f"<div><div style='font-size:22px;font-weight:800;color:#D97706;'>{voicemail}</div><div style='font-size:10px;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;'>Voicemail</div></div>"
        f"<div><div style='font-size:22px;font-weight:800;color:#94A3B8;'>{no_content}</div><div style='font-size:10px;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;'>No Content</div></div>"
        f"</div>"
    )
    if signals:
        out += "<h3 style='font-size:13px;font-weight:700;color:#64748B;margin:20px 0 10px;'>Call Signals</h3>"
        for s in signals:
            if not isinstance(s, dict): continue
            sentiment  = s.get("sentiment", "")
            sent_color = "#16a34a" if sentiment == "POSITIVE" else "#ef4444" if sentiment == "NEGATIVE" else "#d97706"
            contact    = _e(s.get("contact_name", "") or s.get("contact_email", ""))
            call_name  = _e(s.get("call_name", ""))
            call_date  = _e(s.get("call_date", ""))
            summary    = _e(s.get("brief_summary", "") or s.get("summary", ""))
            next_s     = _e(s.get("next_steps", "") or s.get("highlights_next_steps", ""))
            action     = _e(s.get("recommended_action", ""))
            out += "<div class='signal-card'>"
            out += f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;'><span style='color:{sent_color};font-weight:700;'>{sentiment}</span>"
            if contact:   out += f"<b style='color:{NAVY};'>{contact}</b>"
            if call_name: out += f"<span style='color:#94A3B8;font-size:11px;'>· {call_name}</span>"
            if call_date: out += f"<span style='color:#94A3B8;font-size:11px;'>· {call_date}</span>"
            out += "</div>"
            if summary: out += f"<p style='margin:0 0 6px;font-size:13px;'>{summary}</p>"
            if next_s:  out += f"<p style='margin:0 0 4px;font-size:12px;'><b>Next Steps:</b> {next_s}</p>"
            if action:  out += f"<p style='margin:0;font-size:12px;color:{BLUE};'><b>→ {action}</b></p>"
            out += "</div>"
    if next_steps:
        out += "<h3 style='font-size:13px;font-weight:700;color:#64748B;margin:20px 0 10px;'>Consolidated Next Steps</h3>"
        out += "<table><thead><tr><th>Priority</th><th>Contact</th><th>Action</th><th>Owner</th></tr></thead><tbody>"
        for ns in next_steps:
            if not isinstance(ns, dict): continue
            priority = ns.get("priority", "")
            p_color  = "#DC2626" if priority == "HIGH" else "#D97706" if priority == "MED" else "#94A3B8"
            out += (f"<tr><td><span style='color:{p_color};font-weight:700;font-size:12px;'>{_e(priority)}</span></td>"
                    f"<td>{_e(ns.get('contact', ''))}</td><td>{_e(ns.get('action', ''))}</td><td>{_e(ns.get('owner', ''))}</td></tr>")
        out += "</tbody></table>"
    return out

def _6sense_section(ts_data: dict) -> str:
    rows = ts_data.get("6sense_intent", {}).get("data_rows", [])
    cols = ts_data.get("6sense_intent", {}).get("column_names", [])
    if not rows: return "<p style='color:#94A3B8;'>No 6Sense intent data available.</p>"
    out = "<table><thead><tr><th>Account</th><th>Intent Grade</th><th>Reach Grade</th></tr></thead><tbody>"
    for row in rows[:10]:
        rec = dict(zip(cols, row)) if isinstance(row, list) else row
        intent_score = (rec.get("Person 6S Intent Score")
                        or rec.get("Total Person 6S Intent Score")
                        or "")
        intent_grade = _score_to_grade(intent_score)
        reach_raw    = rec.get("Account Snapshot 6S Reach Score", "")
        reach_grade  = reach_raw if reach_raw else "N/A"  # already High/Med/Low string

        grade_color  = "#16A34A" if "A" in intent_grade else "#D97706" if intent_grade == "B" else "#94A3B8"
        out += (f"<tr><td>{_e(rec.get('Account Name', ''))}</td>"
                f"<td><span style='color:{grade_color};font-weight:700;font-size:16px;'>{_e(intent_grade)}</span></td>"
                f"<td><span style='font-weight:600;'>{_e(reach_grade)}</span></td></tr>")
    out += "</tbody></table>"
    return out

def _case_studies_section(raw: dict, account_name: str) -> str:
    # GTM Buddy is the authoritative source — use pre-rendered cards when available
    gtmbuddy_html = raw.get("gtmbuddy_cards_html", "")
    if gtmbuddy_html:
        source_header = (
            "<div style='margin-bottom:12px;padding:8px 12px;background:#f8fafc;"
            "border:1px solid #e2e8f0;border-radius:6px;'>"
            "<span style='font-size:11px;color:#64748b;font-weight:600;'>"
            "📚 Source: "
            "<a href='https://thoughtspot.gtmbuddy.io' target='_blank' "
            "style='color:#3b82f6;text-decoration:none;'>GTM Buddy</a>"
            " — assets matched to this account’s competitor, vertical, and use case"
            "</span></div>"
        )
        return source_header + gtmbuddy_html

    # Fallback: web-researched case studies
    studies = raw.get("case_studies", {}).get("recommended_case_studies", [])
    if not studies: return "<p style='color:#94A3B8;'>No case studies matched.</p>"
    out = "<div style='display:flex;flex-direction:column;gap:12px;'>"
    for s in studies[:5]:
        if not isinstance(s, dict): continue
        company = _e(_text(s.get("company", s.get("customer", ""))))
        url     = s.get("url", "") or s.get("source_url", "") or s.get("link", "")
        why     = _e(_text(s.get("why_chosen", s.get("rationale", ""))))
        metric  = _e(_text(s.get("key_metric", s.get("metric", ""))))
        link    = (f"<a href='{_e(url)}' target='_blank' style='color:{BLUE};font-weight:700;font-size:15px;'>{company} ↗</a>"
                   if url else f"<b style='font-size:15px;'>{company}</b>")
        out += "<div class='card'>"
        out += f"<div style='margin-bottom:8px;'>{link}</div>"
        if metric: out += f"<div style='font-size:20px;font-weight:800;color:{BLUE};margin-bottom:8px;'>📊 {metric}</div>"
        if why:    out += f"<p style='margin:0;font-size:12px;color:#475569;font-style:italic;'><b>Why for {_e(account_name)}:</b> {why}</p>"
        if not url: out += "<span class='flag' style='font-size:11px;'>⚠️ No URL — cannot link to case study</span>"
        out += "</div>"
    out += "</div>"
    return out

def _exec_profiles_section(raw: dict) -> str:
    _ep = raw.get("exec_profiles", {})
    execs = _ep.get("executives", _ep.get("profiles", []))
    execs = [e for e in execs if isinstance(e, dict) and e.get("name") and "Incumbent TBD" not in e.get("name", "")]
    if not execs: return "<p style='color:#94A3B8;'>No executive profiles available.</p>"
    out = ""
    for exec_data in execs[:8]:
        if not isinstance(exec_data, dict): continue
        name     = _e(exec_data.get("name", ""))
        title    = _e(exec_data.get("title", ""))
        li_url   = exec_data.get("linkedin_url", "")
        bio      = exec_data.get("bio_summary", {}) or exec_data.get("background", "")
        quotes   = exec_data.get("public_quotes", exec_data.get("relevant_quotes", []))
        activity = exec_data.get("recent_activity", [])
        in_sfdc  = exec_data.get("in_sfdc", True)
        src_type = exec_data.get("source_type", "")

        border_color = "#e2e8f8" if in_sfdc else "#FED7AA"
        out += f"<div class='exec-card' style='border-color:{border_color};'>"
        out += (f"<div style='display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px;'>"
                f"<div><div style='font-weight:700;font-size:15px;color:{NAVY};'>{name}")
        if not in_sfdc:
            out += " <span class='badge badge-researched' title='Not found in recent SFDC activity data. A Salesforce record may still exist — verify before outreach.'>🔍 Not in activity</span>"
        out += "</div>"
        if title: out += f"<div style='font-size:12px;color:#64748b;margin-top:2px;'>{title}</div>"
        if not in_sfdc:
            src_label = "LinkedIn" if li_url else (src_type or "Web research")
            out += f"<div style='font-size:10px;color:#D97706;margin-top:2px;'>Source: {_e(src_label)} — not in recent SFDC activity; record may exist</div>"
        out += "</div>"
        if li_url: out += f"<a href='{_e(li_url)}' target='_blank' style='color:{BLUE};font-size:11px;font-weight:600;flex-shrink:0;'>LinkedIn ↗</a>"
        out += "</div>"
        bio_text = _text(bio)
        if bio_text: out += f"<p style='font-size:13px;color:#334155;margin:0 0 10px;'>{_e(bio_text)}{_src_badge(bio)}</p>"
        if activity:
            out += "<p style='font-size:11px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;margin:10px 0 6px;'>Recent Activity</p>"
            out += "<ul style='margin:0;padding-left:16px;'>"
            for act in activity[:3]: out += f"<li style='font-size:12px;margin-bottom:4px;'>{_render_item(act)}</li>"
            out += "</ul>"
        if quotes:
            out += "<p style='font-size:11px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;margin:12px 0 6px;'>Public Quotes</p>"
            for q in quotes[:2]:
                if isinstance(q, dict):
                    quote_text = _e(q.get("quote", ""))
                    context    = _e(q.get("context", ""))
                    if quote_text:
                        out += (f"<blockquote style='border-left:3px solid {BLUE};margin:0 0 8px;"
                                f"padding:8px 14px;background:#f8faff;border-radius:0 8px 8px 0;"
                                f"font-size:13px;color:#334155;'>{quote_text}{_src_badge(q)}")
                        if context: out += f"<div style='font-size:11px;color:#94A3B8;margin-top:4px;'>{context}</div>"
                        out += "</blockquote>"
        out += "</div>"
    return out

def _normalize_outreach(outreach_data: dict) -> dict:
    if not outreach_data: return {}
    if "sequences" in outreach_data and isinstance(outreach_data["sequences"], list):
        for seq in outreach_data["sequences"]:
            if "linkedin" in seq and "linkedin_messages" not in seq:
                linkedin = seq.pop("linkedin")
                if isinstance(linkedin, dict):
                    body = linkedin.get("message", linkedin.get("body", str(linkedin)))
                    seq["linkedin_messages"] = [{"body": body, "claim_annotations": []}]
                elif isinstance(linkedin, list): seq["linkedin_messages"] = linkedin
                else: seq["linkedin_messages"] = [{"body": str(linkedin), "claim_annotations": []}]
            if "linkedin_messages" not in seq: seq["linkedin_messages"] = []
            seq_annotations = seq.get("claim_annotations", [])
            if seq_annotations:
                emails = seq.get("emails", [])
                if emails and isinstance(emails[0], dict):
                    if len(emails) == 1:
                        if not emails[0].get("claim_annotations"): emails[0]["claim_annotations"] = seq_annotations
                    else:
                        chunk = max(1, len(seq_annotations) // len(emails))
                        for i, email in enumerate(emails):
                            if not email.get("claim_annotations"):
                                start = i * chunk
                                end   = start + chunk if i < len(emails) - 1 else len(seq_annotations)
                                email["claim_annotations"] = seq_annotations[start:end]
            for email in seq.get("emails", []):
                if isinstance(email, dict) and "claim_annotations" not in email: email["claim_annotations"] = []
            for msg in seq.get("linkedin_messages", []):
                if isinstance(msg, dict) and "claim_annotations" not in msg: msg["claim_annotations"] = []
        return outreach_data
    if "contacts" in outreach_data:
        seq_data    = outreach_data.get("sequences", {})
        annotations = outreach_data.get("claim_annotations", [])
        sequences   = []
        for contact in outreach_data.get("contacts", []):
            emails  = []
            li_msgs = []
            if isinstance(seq_data, dict):
                for key in sorted(seq_data.keys()):
                    item = seq_data[key]
                    if key.startswith("email"):
                        if isinstance(item, str): emails.append({"subject": f"Email {len(emails)+1}", "body": item, "claim_annotations": []})
                        elif isinstance(item, dict):
                            if "claim_annotations" not in item: item["claim_annotations"] = []
                            emails.append(item)
                    elif key in ("linkedin", "linkedin_messages") or key.startswith("linkedin"):
                        if isinstance(item, str): li_msgs.append({"body": item, "claim_annotations": []})
                        elif isinstance(item, dict):
                            body = item.get("message", item.get("body", str(item)))
                            li_msgs.append({"body": body, "claim_annotations": []})
                        elif isinstance(item, list):
                            for m in item:
                                if isinstance(m, dict) and "claim_annotations" not in m: m["claim_annotations"] = []
                            li_msgs.extend(item)
            if annotations and emails and not emails[0].get("claim_annotations"): emails[0]["claim_annotations"] = annotations
            sequences.append({"contact_name": contact.get("name", ""), "contact_title": contact.get("title", ""),
                               "contact_linkedin": contact.get("linkedin_url", ""), "emails": emails, "linkedin_messages": li_msgs})
        return {"sequences": sequences}
    emails = []
    li_msgs = []
    annotations = outreach_data.get("claim_annotations", [])
    for key in sorted(outreach_data.keys()):
        if key == "claim_annotations": continue
        item = outreach_data[key]
        if key.startswith("email"):
            if isinstance(item, str): emails.append({"subject": f"Email {len(emails)+1}", "body": item, "claim_annotations": []})
            elif isinstance(item, dict):
                if "claim_annotations" not in item: item["claim_annotations"] = []
                emails.append(item)
        elif key in ("linkedin", "linkedin_messages") or key.startswith("linkedin"):
            if isinstance(item, str): li_msgs.append({"body": item, "claim_annotations": []})
            elif isinstance(item, dict):
                body = item.get("message", item.get("body", str(item)))
                li_msgs.append({"body": body, "claim_annotations": []})
            elif isinstance(item, list):
                for m in item:
                    if isinstance(m, dict) and "claim_annotations" not in m: m["claim_annotations"] = []
                li_msgs.extend(item)
    if annotations and emails and not emails[0].get("claim_annotations"): emails[0]["claim_annotations"] = annotations
    if emails or li_msgs:
        return {"sequences": [{"contact_name": "", "contact_title": "", "emails": emails, "linkedin_messages": li_msgs}]}
    return outreach_data

def _outreach_section(outreach_data: dict) -> str:
    outreach_data = _normalize_outreach(outreach_data)
    if not outreach_data: return "<p style='color:#94A3B8;'>Gong Templates not yet generated.</p>"
    sequences = outreach_data.get("sequences", [])
    if not sequences: return "<p style='color:#94A3B8;'>No Gong Templates available.</p>"
    out = ""
    for seq in sequences:
        if not isinstance(seq, dict): continue
        name    = _e(seq.get("contact_name", "") or seq.get("name", ""))
        title   = _e(seq.get("contact_title", "") or seq.get("title", ""))
        emails  = seq.get("emails", [])
        li_msgs = seq.get("linkedin_messages", [])
        out += "<div class='exec-card'>"
        out += f"<div style='font-weight:700;font-size:15px;color:{NAVY};margin-bottom:4px;'>✉️ {name}"
        if title: out += f" <span style='font-weight:400;font-size:13px;color:#64748b;'>— {title}</span>"
        out += "</div>"
        if emails:
            out += f"<p style='font-size:11px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;margin:14px 0 8px;'>Email Sequence</p>"
            for i, email in enumerate(emails, 1):
                if isinstance(email, dict):
                    subj        = _e(email.get("subject", f"Email {i}"))
                    body        = _e(email.get("body", ""))
                    annotations = email.get("claim_annotations", [])
                    out += f"<div style='margin-bottom:20px;'>"
                    out += f"<b style='font-size:13px;color:{NAVY};'>Email {i}: {subj}</b>"
                    out += f"<pre style='background:#f8faff;border:1px solid #e2e8f8;border-radius:8px;padding:14px;font-size:12px;margin-top:6px;white-space:pre-wrap;font-family:inherit;'>{body}</pre>"
                    if annotations: out += _render_claim_annotations(annotations)
                    out += "</div>"
                else:
                    out += f"<pre style='background:#f8faff;border-radius:8px;padding:14px;font-size:12px;'>{_e(str(email))}</pre>"
        if li_msgs:
            out += f"<p style='font-size:11px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;margin:14px 0 8px;'>LinkedIn Sequence</p>"
            for i, msg in enumerate(li_msgs, 1):
                if isinstance(msg, dict):
                    body        = _e(msg.get("body", "") or msg.get("message", ""))
                    annotations = msg.get("claim_annotations", [])
                    out += f"<div style='margin-bottom:16px;'>"
                    out += f"<b style='font-size:13px;color:{NAVY};'>LinkedIn {i}</b>"
                    out += f"<pre style='background:#f8faff;border:1px solid #e2e8f8;border-radius:8px;padding:14px;font-size:12px;margin-top:6px;white-space:pre-wrap;font-family:inherit;'>{body}</pre>"
                    if annotations: out += _render_claim_annotations(annotations)
                    out += "</div>"
                else:
                    out += f"<pre style='background:#f8faff;border-radius:8px;padding:14px;font-size:12px;'>{_e(str(msg))}</pre>"
        refs = seq.get("gtmbuddy_refs", [])
        if refs:
            out += f"<p style='font-size:11px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:1px;margin:14px 0 8px;'>Referenced Assets</p>"
            out += "<ul style='margin:0;padding-left:16px;'>"
            for r in refs:
                if not isinstance(r, dict): continue
                r_title = _e(r.get("title", "") or r.get("id", "Untitled asset"))
                r_url   = r.get("url", "")
                r_cat   = _e(r.get("category", ""))
                cat_suffix = f" <span style='color:#94A3B8;'>· {r_cat}</span>" if r_cat else ""
                if r_url:
                    out += f"<li style='margin-bottom:4px;font-size:12px;'><a href='{_e(r_url)}' target='_blank' style='color:{BLUE};'>{r_title} ↗</a>{cat_suffix}</li>"
                else:
                    out += f"<li style='margin-bottom:4px;font-size:12px;'>{r_title}{cat_suffix}</li>"
            out += "</ul>"
        out += "</div>"
    return out
def _get_tabs() -> list:
    return [
        ("overview",      "🏢 Overview"),
        ("stakeholders",  "👥 Stakeholders"),
        ("why_ts",        "🎯 Why ThoughtSpot"),
        ("competitor",    "🔍 Competitors"),
        ("deal_story",    "📋 Deal Story"),
        ("sales_call",    "📞 Sales Signals"),
        ("gong_calls",    "🎙 Gong Calls"),
        ("6sense",        "📡 6Sense"),
        ("roles",         "💼 Hiring"),
        ("case_studies",  "📚 Case Studies"),
        ("exec_profiles", "🧑 Exec Profiles"),
        ("outreach",      "✉️ Gong Templates"),
    ]

def _build_html_page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title>"
        + _CSS + "</head><body>" + body + _JS + "</body></html>"
    )

def normalize_raw(raw: dict) -> dict:
    """
    Normalize all subagent output key names to canonical form.
    Called automatically at the top of build_pg_report() and build_onepager().
    """
    ep = raw.get("exec_profiles", {})
    if isinstance(ep, dict):
        if "profiles" in ep and "executives" not in ep:
            ep["executives"] = ep.pop("profiles")

    ci = raw.get("competitor_intel", {})
    if isinstance(ci, dict):
        if "tools_inferred" in ci and "tools_suspected" not in ci:
            ci["tools_suspected"] = ci.pop("tools_inferred")
        for tool in ci.get("tools_confirmed", []):
            if isinstance(tool, dict):
                if "thoughtspot_angle" in tool and "displacement_angle" not in tool:
                    tool["displacement_angle"] = tool["thoughtspot_angle"]

    ts = raw.get("tsumble", {})
    if isinstance(ts, dict):
        for role in ts.get("role_highlights", []):
            if isinstance(role, dict):
                if "source_url" in role and "url" not in role:
                    role["url"] = role["source_url"]
                if "signal_tier" in role and "thoughtspot_signal" not in role:
                    role["thoughtspot_signal"] = role["signal_tier"]
        ht = ts.get("hiring_trends")
        if isinstance(ht, dict):
            summary = ht.get("summary", "")
            themes  = ht.get("themes", ht.get("key_themes", []))
            trends_list = []
            if summary:
                trends_list.append({"trend": summary, "source": ""})
            for t in (themes or [])[:3]:
                trends_list.append({"trend": t if isinstance(t, str) else t.get("theme", str(t)), "source": ""})
            ts["hiring_trends"] = trends_list if trends_list else [{"trend": str(ht), "source": ""}]
        elif ht is None:
            ts["hiring_trends"] = []

    wr = raw.get("web_research", {})
    if isinstance(wr, dict):
        for sig in wr.get("thoughtspot_fit_signals", []):
            if isinstance(sig, dict) and "signal_tier" not in sig and "tier" in sig:
                sig["signal_tier"] = sig["tier"]
        for pp in wr.get("pain_points", []):
            if isinstance(pp, dict) and "source" not in pp:
                pp["source"] = pp.get("url", "inferred")

    cs = raw.get("case_studies", {})
    if isinstance(cs, dict):
        for study in cs.get("recommended_case_studies", []):
            if isinstance(study, dict):
                if "customer" in study and "company" not in study:
                    study["company"] = study["customer"]
                if "metric" in study and "key_metric" not in study:
                    study["key_metric"] = study["metric"]
                if "rationale" in study and "why_chosen" not in study:
                    study["why_chosen"] = study["rationale"]
                if "why_relevant" in study and "why_chosen" not in study:
                    study["why_chosen"] = study["why_relevant"]
                if "headline_metric" in study and "key_metric" not in study:
                    study["key_metric"] = study["headline_metric"]
                if "why_chosen_for_account" in study and "why_chosen" not in study:
                    study["why_chosen"] = study["why_chosen_for_account"]

    sc = raw.get("sales_calls", {})
    if isinstance(sc, dict):
        # Remap top-level call list keys → "signals"
        if "calls" in sc and "signals" not in sc:
            sc["signals"] = sc["calls"]
        elif "call_summaries" in sc and "signals" not in sc:
            sc["signals"] = sc["call_summaries"]
        # Remap count fields
        if "call_count" in sc and "total_rows" not in sc:
            sc["total_rows"] = sc["call_count"]
        if "total_calls_found" in sc and "total_rows" not in sc:
            sc["total_rows"] = sc["total_calls_found"]
        if "meaningful_calls" in sc and "meaningful_count" not in sc:
            sc["meaningful_count"] = sc["meaningful_calls"]
        if "voicemail_calls" in sc and "voicemail_count" not in sc:
            sc["voicemail_count"] = sc["voicemail_calls"]
        for sig in sc.get("signals", []):
            if not isinstance(sig, dict):
                continue
            # brief → brief_summary (full text, no truncation)
            if "brief" in sig and "brief_summary" not in sig:
                sig["brief_summary"] = sig["brief"]
            # summary → brief_summary fallback
            if "summary" in sig and "brief_summary" not in sig:
                sig["brief_summary"] = sig["summary"]
            # Extract contact_name from participants when absent
            if "contact_name" not in sig or not sig["contact_name"]:
                participants = sig.get("participants", "")
                if participants:
                    for part in participants.split(","):
                        part = part.strip()
                        if "ThoughtSpot" not in part and part:
                            sig["contact_name"] = part.split("(")[0].strip()
                            break
            # key_points → next_steps fallback
            if "key_points" in sig and "next_steps" not in sig:
                sig["next_steps"] = sig["key_points"]

    return raw

def build_pg_report(
    slug:            str,
    account_name:    str,
    raw:             dict,
    ts_data:         dict,
    matched_drivers: list,
    header_data:     dict,
    outreach_data:   dict = None,
    output_dir:      str  = "/sandbox",
) -> dict:
    """
    v5.8: Single-phase delivery — always includes Gong Templates if available.
    Verification logs gaps in report header but never crashes session.
    Returns {"filename", "html", "slug", "data_gaps"}
    """
    raw           = normalize_raw(raw or {})
    outreach_data = outreach_data or {}
    tabs          = _get_tabs()
    owner         = header_data.get("owner_name", "AE")
    region        = header_data.get("region", "")
    now           = datetime.datetime.utcnow().strftime("%B %d, %Y")
    why_now         = header_data.get("why_now", "")
    why_anything    = header_data.get("why_anything", "")
    why_thoughtspot = header_data.get("why_thoughtspot", "")
    opp_stage       = header_data.get("opp_stage", "")
    opp_name      = header_data.get("opp_name", "")

    # Auto-extract stage/opp from ts_data if not in header
    if not opp_stage or not opp_name:
        ds = ts_data.get("deal_stage", {})
        ds_rows = ds.get("data_rows", [])
        ds_cols = ds.get("column_names", [])
        if ds_rows and ds_cols:
            ds_rec    = dict(zip(ds_cols, ds_rows[0])) if isinstance(ds_rows[0], list) else ds_rows[0]
            opp_stage = opp_stage or ds_rec.get("Opportunity Stage Maximum Name", "")
            opp_name  = opp_name  or ds_rec.get("Opportunity Name", "")

    # Run verification checks — collect gaps, never raise
    import re as _re_check
    html_preview = ""
    ds      = ts_data.get("deal_stage", {})
    ds_rows = ds.get("data_rows", [])
    data_gaps = []

    # We'll verify after body is built — placeholder for now
    # Body build starts here

    body  = "<div class='page'>"

    # Hero
    body += "<div class='hero'>"
    body += "<div class='hero-eyebrow'>Account Intelligence Report</div>"
    body += f"<h1>{_e(account_name)}</h1>"
    body += f"<p class='hero-sub'>ThoughtSpot PG Plan · {now}</p>"
    body += "<div class='hero-meta'>"
    body += f"<div class='hero-meta-item'><span class='hero-meta-label'>Prepared By</span><span class='hero-meta-value'>{_e(owner)}</span></div>"
    if region:
        body += f"<div class='hero-meta-item'><span class='hero-meta-label'>Region</span><span class='hero-meta-value'>{_e(region)}</span></div>"
    body += f"<div class='hero-meta-item'><span class='hero-meta-label'>Generated</span><span class='hero-meta-value'>{now}</span></div>"
    if opp_stage:
        body += f"<div class='hero-meta-item'><span class='hero-meta-label'>Stage</span><span class='hero-meta-value'>{_e(opp_stage)}</span></div>"
    if opp_name:
        body += f"<div class='hero-meta-item'><span class='hero-meta-label'>Opportunity</span><span class='hero-meta-value'>{_e(opp_name)}</span></div>"
    body += "</div></div>"

    # Data gaps banner — injected after build
    DATA_GAPS_PLACEHOLDER = "<!--DATA_GAPS_PLACEHOLDER-->"
    body += DATA_GAPS_PLACEHOLDER

    # Tab navigation
    body += "<div class='tab-nav'>"
    for i, (tab_id, tab_label) in enumerate(tabs):
        active = "active" if i == 0 else ""
        body += (f"<button class='tab-btn tab-btn-{_e(slug)} {active}' "
                 f"data-tab='{_e(tab_id)}' "
                 f"onclick='showTab(\"{_e(slug)}\",\"{_e(tab_id)}\")'>"
                 f"{_e(tab_label)}</button>")
    body += "</div>"

    # Tab content
    body += "<div class='content'>"
    for i, (tab_id, _) in enumerate(tabs):
        active = "active" if i == 0 else ""
        body += f"<div id='{_e(slug)}_{_e(tab_id)}' class='tab-content tab-content-{_e(slug)} {active}'>"

        if tab_id == "overview":
            body += _company_overview_section(raw)

        elif tab_id == "stakeholders":
            body += _section_header("👥", "4-Leg Stool")
            body += _four_leg_stool_section(ts_data, account_name, raw)
            body += _section_header("🗺️", "Stakeholder Map")
            body += _stakeholder_map_section(ts_data)
            body += _section_header("💡", "ThoughtSpot Talking Point")
            body += _talking_point_section(account_name, matched_drivers, raw)

        elif tab_id == "why_ts":
            body += _section_header("🎯", "Why ThoughtSpot")
            body += _why_ts_section(matched_drivers, raw, why_now=why_now, why_anything=why_anything, why_thoughtspot=why_thoughtspot)

        elif tab_id == "competitor":
            body += _section_header("🔍", "Competitor Landscape")
            body += _competitor_section(raw, matched_drivers)

        elif tab_id == "deal_story":
            body += _section_header("📋", "Deal Story")
            body += _deal_story_section(ts_data, raw=raw)

        elif tab_id == "sales_call":
            body += _section_header("📞", "Sales Call Analysis")
            body += _sales_call_section(ts_data)

        elif tab_id == "gong_calls":
            body += _section_header("🎙", "Gong Call Signals")
            body += _gong_calls_section(raw)

        elif tab_id == "6sense":
            body += _section_header("📡", "6Sense Intent")
            body += _6sense_section(ts_data)

        elif tab_id == "roles":
            body += _section_header("💼", "Hiring Signals")
            body += _hiring_signals_section(raw)

        elif tab_id == "case_studies":
            body += _section_header("📚", "Recommended Case Studies")
            body += _case_studies_section(raw, account_name)

        elif tab_id == "exec_profiles":
            body += _section_header("🧑", "Executive Profiles")
            body += _exec_profiles_section(raw)

        elif tab_id == "outreach":
            body += _section_header("✉️", "Gong Templates")
            if outreach_data:
                body += _outreach_section(outreach_data)
            else:
                body += "<p style='color:#94A3B8;'>Gong Templates generating — check back shortly.</p>"

        body += "</div>"

    body += "</div></div>"

    # Build full HTML
    html = _build_html_page(f"PG Report — {account_name}", body)

    # Post-build verification — log gaps, never crash
    _opp_cards_in_html = len(_re_check.findall(r"opp-card", html))
    _empty_details     = [b for b in _re_check.findall(r'<details[^>]*>.*?</details>', html, _re_check.DOTALL)
                          if len(b[b.find('</summary>')+10:].strip()) < 60]

    checks = {
        "4-Leg Stool present":       "stool-grid" in html,
        "Talking Point present":     "talking-point" in html,
        "MEDDPICC absent":           "MEDDPICC" not in html,
        "Case study URLs present":   'href=' in html and ("Why for" in html or "why_chosen" in html or "case-study" in html.lower()),
        "Activity section present":  "Prospect Contact" in html or "No deal stage" in html or not ts_data.get("activity_history", {}).get("data_rows"),
        "Champion shown or flagged": "Champion" in html,
        "EB shown or flagged":       "Economic Buyer" in html,
        "Deal story has opp data":   "opp-card" in html or "No deal stage" in html,
        "All opps in deal table":    _opp_cards_in_html >= len(ds_rows),
        "Funnel timing rendered":    "Funnel Timing" in html or not ts_data.get("deal_funnel_timing", {}).get("data_rows"),
        "Gong calls rendered":       "signal-card" in html or "No Gong call" in html or not raw.get("sales_calls"),
        "No empty claim bodies":     len(_empty_details) == 0,
        "Why Now rendered":          "Why Now" in html or not why_now,
        "Why Anything rendered":     "Why Anything" in html or not why_anything,
        "Why ThoughtSpot rendered": "Why ThoughtSpot" in html or not why_thoughtspot,
    }

    failed = [k for k, v in checks.items() if not v]
    for rule, passed in checks.items():
        print(f"{'✅' if passed else '⚠️ GAP'} {rule}")

    # Inject data gaps banner if any checks failed
    if failed:
        gaps_html = (
            f"<div class='data-gaps'>"
            f"<div class='data-gaps-label'>⚠️ Data gaps detected ({len(failed)})</div>"
            + "".join(f"<div>• {_e(f)}</div>" for f in failed)
            + "</div>"
        )
        print(f"⚠️ {len(failed)} gap(s) flagged in report header — delivering with flags")
    else:
        gaps_html = ""
        print("✅ All verification checks passed")

    html = html.replace(DATA_GAPS_PLACEHOLDER, gaps_html)

    filename = f"{slug}_pg_report.html"
    print(f"[pg_report_builder v5.8] Report built → {filename}")
    return {"filename": filename, "html": html, "slug": slug, "data_gaps": failed}
def build_onepager(
    slug:            str,
    account_name:    str,
    raw:             dict,
    matched_drivers: list,
    output_dir:      str = "/sandbox",
) -> dict:
    """
    v5.8: Hardened text extraction — no more dict repr in output.
    Case study links fixed — tries url / source_url / link.
    Returns {"filename", "html", "slug"}
    """
    raw      = normalize_raw(raw or {})
    wr       = raw.get("web_research", {})
    cs_data  = raw.get("case_studies", {})

    # v5.8: explicit text extraction — never str(dict)
    desc_obj = wr.get("description", {})
    if isinstance(desc_obj, dict):
        desc = desc_obj.get("text", "") or desc_obj.get("summary", "") or ""
    elif isinstance(desc_obj, str):
        desc = desc_obj
    else:
        desc = ""

    pain_pts = []
    for p in wr.get("pain_points", []):
        pt = _text(p)
        if pt and pt not in pain_pts:
            pain_pts.append(pt)

    studies  = cs_data.get("recommended_case_studies", [])

    challenges = [p for p in pain_pts[:4] if p]

    value_statements = []
    for m in matched_drivers[:4]:
        key    = m.get("key", "") if isinstance(m, dict) else m
        driver = get_drivers(key)
        if driver:
            signals = get_money_signals(key)
            mi      = signals.get("money_in", [])
            mo      = signals.get("money_out", [])
            hook    = mi[0] if mi else (mo[0] if mo else "")
            if hook and isinstance(hook, str):
                value_statements.append(hook)

    proof_points = []
    # GTM Buddy is the authoritative source for proof points
    gtmbuddy_assets = raw.get("gtmbuddy_assets", [])
    if gtmbuddy_assets:
        for a in gtmbuddy_assets[:4]:
            if not isinstance(a, dict): continue
            proof_points.append({
                "company":  a.get("title", ""),
                "metric":   a.get("rationale", ""),
                "url":      a.get("url", ""),
                "source":   "GTM Buddy",
            })
    else:
        # Fallback: web-researched case studies
        for s in studies[:3]:
            if not isinstance(s, dict): continue
            company = _text(s.get("company", s.get("customer", s.get("name", ""))))
            metric  = _text(s.get("key_metric", s.get("metric", s.get("outcome", ""))))
            url     = s.get("url", "") or s.get("source_url", "") or s.get("link", "")
            if company and metric:
                proof_points.append({"company": company, "metric": metric, "url": url})

    onepager_css = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*, *::before, *::after {{ box-sizing: border-box; }}
body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #f0f4ff; color: #0f172a; font-size: 14px; line-height: 1.6; }}
.page {{ max-width: 800px; margin: 40px auto; background: white; border-radius: 20px; box-shadow: 0 8px 48px rgba(29,45,80,0.12); overflow: hidden; }}
.hero {{ background: linear-gradient(135deg, #060d1f 0%, {NAVY} 45%, #2347c8 100%); padding: 52px 56px 44px; color: white; position: relative; overflow: hidden; }}
.hero::before {{ content: ''; position: absolute; top: -80px; right: -80px; width: 360px; height: 360px; background: radial-gradient(circle, rgba(46,92,229,0.25) 0%, transparent 70%); pointer-events: none; }}
.hero-eyebrow {{ font-size: 10px; font-weight: 700; letter-spacing: 2.5px; text-transform: uppercase; color: {LIGHT_BLUE}; margin-bottom: 14px; position: relative; z-index: 1; }}
.hero h1 {{ font-size: 36px; font-weight: 800; margin: 0 0 10px; line-height: 1.1; letter-spacing: -0.5px; position: relative; z-index: 1; }}
.hero-sub {{ font-size: 15px; opacity: 0.7; margin: 0; position: relative; z-index: 1; }}
.content {{ padding: 48px 56px; }}
h2 {{ font-size: 18px; font-weight: 700; color: {NAVY}; margin: 36px 0 16px; padding-bottom: 10px; border-bottom: 2px solid {LIGHT_BLUE}; letter-spacing: -0.2px; }}
h2:first-of-type {{ margin-top: 0; }}
ul {{ padding-left: 0; margin: 0; list-style: none; }}
li {{ display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; font-size: 14px; line-height: 1.6; color: #334155; }}
li::before {{ content: '→'; color: {BLUE}; font-weight: 700; flex-shrink: 0; margin-top: 1px; }}
.value-card {{ background: linear-gradient(135deg, {LIGHT_BLUE}, #f0f7ff); border: 1px solid #c7d7f8; border-left: 4px solid {BLUE}; border-radius: 10px; padding: 14px 18px; margin-bottom: 10px; font-size: 13px; line-height: 1.65; color: #1e3a5f; }}
.proof-card {{ background: linear-gradient(135deg, #f0fdf4, #ecfdf5); border: 1px solid #86efac; border-left: 4px solid #16a34a; border-radius: 10px; padding: 14px 18px; margin-bottom: 10px; font-size: 14px; font-weight: 600; color: #14532d; display: flex; align-items: center; gap: 12px; text-decoration: none; }}
.proof-card:hover {{ background: linear-gradient(135deg, #dcfce7, #d1fae5); }}
.proof-metric {{ font-size: 16px; }}
.proof-company {{ font-size: 13px; font-weight: 400; color: #166534; }}
.cta {{ background: linear-gradient(135deg, {NAVY}, {BLUE}); color: white; border-radius: 14px; padding: 32px 40px; text-align: center; margin-top: 40px; position: relative; overflow: hidden; }}
.cta::before {{ content: ''; position: absolute; top: -40px; right: -40px; width: 200px; height: 200px; background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 70%); pointer-events: none; }}
.cta-label {{ font-size: 10px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; opacity: 0.6; margin-bottom: 12px; }}
.cta a {{ display: inline-block; color: white; font-size: 20px; font-weight: 800; text-decoration: none; letter-spacing: -0.3px; position: relative; z-index: 1; }}
.cta p {{ color: rgba(255,255,255,0.7); font-size: 13px; margin: 10px 0 0; position: relative; z-index: 1; }}
.footer {{ padding: 20px 56px; background: #f8faff; border-top: 1px solid #e2e8f8; display: flex; align-items: center; justify-content: space-between; }}
.footer-logo {{ font-size: 13px; font-weight: 700; color: {NAVY}; letter-spacing: -0.3px; }}
.footer-tagline {{ font-size: 11px; color: #94A3B8; }}
@media print {{
    body {{ background: white; font-size: 12px; }}
    .page {{ max-width: 100%; margin: 0; border-radius: 0; box-shadow: none; }}
    .hero {{ padding: 36px 44px 30px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .content {{ padding: 36px 44px; }}
    .cta {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; break-inside: avoid; }}
    .value-card, .proof-card {{ break-inside: avoid; }}
    .footer {{ padding: 16px 44px; }}
}}
</style>
"""

    body  = "<div class='page'>"
    body += "<div class='hero'>"
    body += "<div class='hero-eyebrow'>ThoughtSpot · Account Brief</div>"
    body += f"<h1>{_e(account_name)}</h1>"
    if desc: body += f"<p class='hero-sub'>{_e(desc[:200])}</p>"
    body += "</div>"
    body += "<div class='content'>"

    if challenges:
        body += "<h2>Business Challenges</h2><ul>"
        for c in challenges: body += f"<li>{_e(c)}</li>"
        body += "</ul>"
    else:
        body += "<h2>Business Challenges</h2><p style='color:#64748b;font-size:14px;'>Contact your ThoughtSpot AE for a tailored analysis.</p>"

    if value_statements:
        body += "<h2>How ThoughtSpot Helps</h2>"
        for v in value_statements: body += f"<div class='value-card'>{_e(v)}</div>"
    else:
        body += ("<h2>How ThoughtSpot Helps</h2>"
                 "<div class='value-card'>ThoughtSpot delivers AI-powered analytics that help business "
                 "teams get answers from data instantly — no SQL, no waiting for reports.</div>")

    if proof_points:
        body += "<h2>Customer Proof Points</h2>"
        gtmbuddy_assets = raw.get("gtmbuddy_assets", [])
        if gtmbuddy_assets:
            body += ("<div style='margin-bottom:10px;font-size:11px;color:#64748b;font-weight:600;'>"
                     "📚 Source: <a href='https://thoughtspot.gtmbuddy.io' target='_blank' "
                     "style='color:#3b82f6;text-decoration:none;'>GTM Buddy</a></div>")
        for p in proof_points:
            url     = p["url"]
            company = _e(p["company"])
            metric  = _e(p["metric"])
            source  = p.get("source", "")
            if url:
                body += (f"<a href='{_e(url)}' target='_blank' class='proof-card'>"
                         f"<span class='proof-metric'>📊 {metric}</span>"
                         f"<span class='proof-company'>{company} →</span>"
                         f"</a>")
            else:
                body += (f"<div class='proof-card'>"
                         f"<span class='proof-metric'>📊 {metric}</span>"
                         f"<span class='proof-company'>{company}</span>"
                         f"</div>")

    body += ("<div class='cta'>"
             "<div class='cta-label'>Ready to see it in action?</div>"
             "<a href='https://www.thoughtspot.com/demo' target='_blank'>Request a Demo →</a>"
             "<p>thoughtspot.com/demo</p>"
             "</div>")

    body += "</div>"
    body += (f"<div class='footer'>"
             f"<span class='footer-logo'>ThoughtSpot</span>"
             f"<span class='footer-tagline'>AI-Powered Analytics</span>"
             f"</div>")
    body += "</div>"

    filename = f"{slug}_onepager.html"
    full_html = (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(account_name)} — ThoughtSpot</title>"
        + onepager_css
        + "</head><body>"
        + body
        + "</body></html>"
    )

    print(f"[pg_report_builder v5.8] One-pager built → {filename}")
    return {"filename": filename, "html": full_html, "slug": slug}
