"""IPL Batters Analytics Dashboard.

Run from the repository root:
    python -m streamlit run dashboard/app.py

Data source: SQLite database (default ``data/raw/ipl.db``), opened read-only.
Tables used: ``deliveries`` (ball-by-ball) joined one-to-one to ``matches`` on
match_id for the season. Column names are discovered from the real schema at
start-up (see ROLE_CANDIDATES) and can be overridden in the sidebar.

Definitions (aligned with the repository's v_ball view and verification log):
  * Super-over deliveries are excluded (is_super_over = 0) when that column exists.
  * Balls faced = deliveries that are not wides (no-balls ARE faced by the batter).
  * Runs = runs off the bat only (byes, leg-byes, penalty runs, wides excluded).
  * Strike rate = runs / balls faced x 100.
  * Batting average = runs / dismissals (needs a dismissed-player column).
  * Phase (from over number): Powerplay overs 0-5, Middle 6-14, Death 15+
    (same cut-offs as the repository's v_ball view; shifted if overs are 1-based).
  * Fours / sixes = deliveries where runs off the bat equal 4 / 6.
"""
from __future__ import annotations

import html
import os
import re
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="IPL Batters Analytics",
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
DB_CANDIDATES = [
    os.environ.get("IPL_DB_PATH"),
    ROOT / "data" / "raw" / "ipl.db",
    ROOT / "data" / "ipl.db",
    ROOT / "ipl.db",
]
VERIFICATION_LOG = ROOT / "reports" / "verification_log.md"

CYAN, INDIGO, ROSE = "#42d9f5", "#34e889", "#8af5b5"      # neon green + cyan accents
MINT, AMBER, VIOLET, MUTED = "#4ade80", "#a3e635", "#22d3ee", "#91a99b"
ACCENTS = [CYAN, INDIGO, ROSE, MINT, AMBER, VIOLET]
GRID = "rgba(148,163,184,0.14)"
PHASES = ["Powerplay", "Middle", "Death"]
INNINGS_OPTS = [1, 2]

# role -> candidate column names (first match in `deliveries` wins)
ROLE_CANDIDATES = {
    "match_id": ["match_id"],
    "batter": ["batter", "batsman", "striker", "batter_name", "batsman_name"],
    "bat_runs": ["batter_runs", "runs_batter", "runs_off_bat", "batsman_runs", "batsman_run", "runs_batsman", "bat_runs"],
    "over": ["over_number", "over", "overs"],
    "innings": ["innings", "inning"],
    "batting_team": ["batting_team"],
    "bowling_team": ["bowling_team"],
    "wide": ["is_wide_ball", "is_wide", "wide", "is_wide_delivery"],
    "wicket": ["is_wicket", "wicket"],
    "wicket_kind": ["wicket_kind", "dismissal_kind", "player_dismissal_kind"],
    "player_out": ["player_out", "player_dismissed", "dismissed_player", "player_out_name"],
    "super_over": ["is_super_over", "super_over"],
    "hand": ["batting_hand", "batter_hand", "batting_style", "striker_batting_hand", "batsman_hand", "bat_hand"],
}
REQUIRED_ROLES = ["match_id", "batter", "bat_runs", "over", "innings", "batting_team", "bowling_team"]
SEASON_CANDIDATES = ["season", "season_year", "year"]

# per-chart look: (entrance animation, accent colour, tint position, tint angle, entrance easing, accent-sweep seconds)
BOX_STYLES = {
    "ov_hero":       ("zoomIn",     "#22d3ee", "0% 0%",     160, "cubic-bezier(.2,.8,.2,1)",  7.0),
    "ov_bar":        ("slideLeft",  "#818cf8", "100% 0%",   200, "cubic-bezier(.2,.8,.2,1)",  8.5),
    "ov_scatter":    ("slideRight", "#34d399", "0% 100%",   120, "cubic-bezier(.2,.8,.2,1)",  9.5),
    "ov_heatmap":    ("flipX",      "#a78bfa", "100% 100%", 220, "ease-out",                  8.0),
    "ov_table":      ("dropIn",     "#fbbf24", "50% 0%",    180, "cubic-bezier(.34,1.4,.64,1)", 10.0),
    "bt_hero":       ("blurReveal", "#fb7185", "100% 0%",   150, "ease-out",                  7.5),
    "bt_phase":      ("skewIn",     "#38bdf8", "0% 50%",    100, "cubic-bezier(.2,.8,.2,1)",  9.0),
    "bt_opp":        ("swingIn",    "#2dd4bf", "50% 100%",  210, "cubic-bezier(.2,.8,.2,1)",  8.0),
    "bt_dismissal":  ("spinZoom",   "#f472b6", "50% 50%",   135, "cubic-bezier(.2,.8,.2,1)", 10.5),
    "bt_boundaries": ("riseBounce", "#a3e635", "0% 0%",     190, "cubic-bezier(.34,1.4,.64,1)", 9.0),
}

BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700;800&family=Manrope:wght@400;500;600;700;800&display=swap');
:root {
  --ink:#f7f9fc; --muted:#b7c4d4; --orange:#22c55e; --lime:#86efac;
  --panel:#092218; --line:rgba(255,255,255,.12);
}
@keyframes stadiumGlow { 0%,100%{opacity:.4;transform:translate3d(0,0,0)} 50%{opacity:.8;transform:translate3d(10px,8px,0)} }
@keyframes cardEnter { from{opacity:0;transform:translateY(22px) scale(.985)} to{opacity:1;transform:none} }
@keyframes orangeSweep { 0%{background-position:-180% 0} 100%{background-position:220% 0} }
@keyframes softPulse { 0%,100%{box-shadow:0 0 0 rgba(34,197,94,0)} 50%{box-shadow:0 0 26px rgba(34,197,94,.12)} }
@keyframes riseIn { from{opacity:0;transform:translateY(16px)} to{opacity:1;transform:none} }
@keyframes sweep { 0%{background-position:-160% 0} 100%{background-position:260% 0} }
@keyframes zoomIn { from{opacity:0;transform:scale(.96)} to{opacity:1;transform:none} }
@keyframes slideLeft { from{opacity:0;transform:translateX(-26px)} to{opacity:1;transform:none} }
@keyframes slideRight { from{opacity:0;transform:translateX(26px)} to{opacity:1;transform:none} }
@keyframes flipX { from{opacity:0;transform:perspective(900px) rotateX(-22deg);transform-origin:top} to{opacity:1;transform:none} }
@keyframes dropIn { from{opacity:0;transform:translateY(-22px)} to{opacity:1;transform:none} }
@keyframes blurReveal { from{opacity:0;filter:blur(7px)} to{opacity:1;filter:none} }
@keyframes skewIn { from{opacity:0;transform:skewX(-3deg) translateX(-20px)} to{opacity:1;transform:none} }
@keyframes swingIn { from{opacity:0;transform:perspective(900px) rotateY(12deg);transform-origin:left} to{opacity:1;transform:none} }
@keyframes spinZoom { from{opacity:0;transform:rotate(-2deg) scale(.96)} to{opacity:1;transform:none} }
@keyframes riseBounce { from{opacity:0;transform:translateY(26px)} to{opacity:1;transform:none} }

.stApp {
 color:var(--ink);
 background:
  radial-gradient(ellipse 650px 420px at 0% 0%,rgba(34,197,94,.17),transparent 68%),
  radial-gradient(ellipse 700px 500px at 100% 8%,rgba(134,239,172,.10),transparent 68%),
  linear-gradient(135deg,#06130e 0%,#071c14 52%,#050d0a 100%);
 background-attachment:fixed;
}
.stApp::before {
 content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
 background:repeating-linear-gradient(135deg,rgba(255,255,255,.018) 0 1px,transparent 1px 14px);
 mask-image:linear-gradient(to bottom,black,transparent 88%);
}
[data-testid="stAppViewContainer"] > .main {position:relative;z-index:1;}
section[data-testid="stSidebar"] {
 background:linear-gradient(180deg,#092218 0%,#06150f 100%) !important;
 border-right:1px solid rgba(34,197,94,.38);
}
section[data-testid="stSidebar"] > div {padding-top:1.25rem;}
.block-container {padding-top:1.5rem;padding-bottom:3rem;max-width:1600px;}
html,body,[class*="css"] {font-family:"Manrope","Segoe UI",system-ui,sans-serif;color:var(--ink);}
h1,h2,h3 {font-family:"Barlow Condensed","Manrope",sans-serif;letter-spacing:.015em;color:#fff;}
p,label,.stCaption,[data-testid="stWidgetLabel"] {color:#c0ccda;}
.hdr {
 position:relative;overflow:hidden;padding:1.6rem 1.8rem 1.35rem;margin-bottom:1.4rem;
 border-radius:8px 22px 8px 22px;border:1px solid rgba(34,197,94,.48);
 border-left:5px solid var(--orange);
 background:linear-gradient(105deg,rgba(25,36,52,.98),rgba(12,21,34,.94));
 box-shadow:0 18px 48px rgba(0,0,0,.34);animation:cardEnter .7s cubic-bezier(.2,.8,.2,1) both;
}
.hdr:after {
 content:"";position:absolute;right:-65px;top:-110px;width:330px;height:300px;border-radius:50%;
 background:radial-gradient(circle,rgba(34,197,94,.22),transparent 67%);animation:stadiumGlow 7s ease-in-out infinite;
}
.hdr h1 {
 position:relative;z-index:1;margin:0;font-family:"Barlow Condensed",sans-serif;
 font-size:clamp(2rem,3.5vw,3rem);font-weight:800;letter-spacing:.075em;text-transform:uppercase;
 color:#fff;background:none;-webkit-text-fill-color:#fff;
}
.hdr p {position:relative;z-index:1;margin:.5rem 0 0;color:#c0ccda;font-size:.92rem;letter-spacing:.015em;}
.kpi {
 position:relative;overflow:hidden;background:linear-gradient(145deg,#0c281b,#081a12) !important;
 border:1px solid rgba(255,255,255,.13);border-radius:8px 18px 8px 18px;padding:20px 22px 19px 25px;
 min-height:138px;box-shadow:0 14px 32px rgba(0,0,0,.28);animation:cardEnter .65s cubic-bezier(.2,.8,.2,1) both;
 transition:transform .22s ease,border-color .22s ease,box-shadow .22s ease;
}
.kpi:hover {transform:translateY(-5px);border-color:rgba(34,197,94,.65);box-shadow:0 20px 40px rgba(0,0,0,.35),0 0 22px rgba(34,197,94,.08);}
.kpi::before {content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--k);}
.kpi::after {content:"";position:absolute;right:-40px;top:-48px;width:125px;height:125px;border-radius:50%;background:var(--k);opacity:.10;}
.kpi .lbl {color:#c3cfde;font-size:.72rem;text-transform:uppercase;letter-spacing:.13em;font-weight:800;}
.kpi .val {position:relative;z-index:1;font-family:"Barlow Condensed",sans-serif;font-size:clamp(1.8rem,2.6vw,2.45rem);font-weight:800;line-height:1.15;margin:.4rem 0;color:var(--k);}
.kpi .sub {color:#c0ccda;font-size:.81rem;line-height:1.55;}
.kpi.cyan {--k:#86efac;} .kpi.indigo {--k:#a9b7ff;} .kpi.rose {--k:#ff9a52;}
.tag {display:inline-flex;align-items:center;gap:7px;font-size:.68rem;letter-spacing:.13em;text-transform:uppercase;color:#d7fa91;background:rgba(134,239,172,.09);border:1px solid rgba(134,239,172,.35);border-radius:5px;padding:6px 11px;margin-bottom:10px;}
.note {color:#b7c4d4;font-size:.82rem;}
.stTabs [data-baseweb="tab-list"] {gap:7px;border-bottom:1px solid rgba(255,255,255,.12);}
.stTabs [data-baseweb="tab"] {border-radius:8px 8px 0 0;padding:10px 18px;background:rgba(255,255,255,.04);border:1px solid transparent;color:#c0ccda;transition:all .2s ease;}
.stTabs [data-baseweb="tab"]:hover {background:rgba(34,197,94,.11);color:#fff;}
.stTabs [data-baseweb="tab"][aria-selected="true"] {background:rgba(34,197,94,.13);color:#86efac;border-color:rgba(34,197,94,.4);border-bottom-color:#22c55e;}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"] {display:none;}
div[data-baseweb="select"] > div {background:#10271b !important;border:1px solid #28533c !important;border-radius:8px;color:#f7f9fc !important;}
div[data-baseweb="select"] > div:hover {border-color:#22c55e !important;}
div[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {background:#22c55e;border-color:#22c55e;}
div[data-testid="stVerticalBlockBorderWrapper"] {background:rgba(17,27,43,.78);border:1px solid rgba(255,255,255,.13);border-radius:12px;}
div[data-testid="stExpander"] {border:1px solid rgba(255,255,255,.15);border-radius:10px;background:rgba(255,255,255,.035);}
.stButton > button {border:1px solid rgba(34,197,94,.55);border-radius:8px;background:linear-gradient(110deg,#22c55e,#4ade80);color:#101723;font-weight:800;transition:all .2s ease;}
.stButton > button:hover {border-color:#bbf7d0;background:#86efac;color:#101723;transform:translateY(-2px);box-shadow:0 9px 24px rgba(34,197,94,.18);}
div[data-testid="stDataFrame"],div[data-testid="stTable"] {border:1px solid rgba(255,255,255,.15);border-radius:10px;overflow:hidden;}
hr {border-color:rgba(255,255,255,.13);}
@media(max-width:768px) {.block-container{padding:1rem .8rem 2rem}.hdr{padding:1.1rem;border-radius:8px 16px 8px 16px}.kpi{min-height:120px;padding:16px 17px 16px 21px}}
@media(prefers-reduced-motion:reduce) {* ,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
"""


def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def build_css() -> str:
    """Clean glass theme + one rule per chart box: its own tinted background, entrance animation and accent sweep."""
    rules = []
    for name, (anim, accent, pos, angle, ease, sweep_s) in BOX_STYLES.items():
        rules.append(
            f'.st-key-box_{name} {{ position:relative; border:1px solid {_rgba(accent, .22)}; border-radius:18px; '
            f'padding:16px 18px 8px 18px; backdrop-filter: blur(10px); box-shadow:0 14px 40px -18px rgba(0,0,0,.7); '
            f'background: radial-gradient(900px 420px at {pos}, {_rgba(accent, .13)}, transparent 62%), '
            f'linear-gradient({angle}deg, rgba(255,255,255,.05), rgba(255,255,255,.012)), rgba(5,22,14,.72); '
            f'animation: {anim} .85s {ease} both; }}\n'
            f'.st-key-box_{name}::before {{ content:""; position:absolute; left:18px; right:18px; top:0; height:2px; '
            f'border-radius:2px; background: linear-gradient(90deg, transparent, {accent}, transparent); '
            f'background-size:60% 100%; background-repeat:no-repeat; animation: sweep {sweep_s}s linear infinite; }}')
    return "<style>" + BASE_CSS + "\n".join(rules) + "\n</style>"


CSS = build_css()


# --------------------------------------------------------------------------
# Database access
# --------------------------------------------------------------------------
def find_db() -> Path | None:
    for cand in DB_CANDIDATES:
        if cand and Path(cand).is_file():
            return Path(cand).resolve()
    return None


DB_PATH = find_db()
CSV_DELIVERIES = ROOT / "data" / "raw" / "tables" / "deliveries.csv"
CSV_MATCHES = ROOT / "data" / "raw" / "tables" / "matches.csv"
CSV_LOAD_ERROR = None
CSV_MODE = False


@st.cache_resource(show_spinner="Preparing IPL data for the dashboard...")
def build_db_from_csv(deliveries_path: str, matches_path: str,
                      deliveries_mtime: float, matches_mtime: float) -> Path:
    """Build a temporary SQLite database from the deployable source CSVs."""
    fd, db_path = tempfile.mkstemp(prefix="ipl-dashboard-", suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(db_path)
        try:
            pd.read_csv(matches_path).to_sql("matches", con, index=False, if_exists="replace")
            for chunk in pd.read_csv(deliveries_path, chunksize=100_000):
                chunk.to_sql("deliveries", con, index=False, if_exists="append", chunksize=10_000)
        finally:
            con.close()
        return Path(db_path)
    except Exception:
        Path(db_path).unlink(missing_ok=True)
        raise


if DB_PATH is None and CSV_DELIVERIES.is_file() and CSV_MATCHES.is_file():
    try:
        DB_PATH = build_db_from_csv(
            str(CSV_DELIVERIES), str(CSV_MATCHES),
            CSV_DELIVERIES.stat().st_mtime, CSV_MATCHES.stat().st_mtime,
        )
        CSV_MODE = True
    except Exception as exc:
        CSV_LOAD_ERROR = str(exc)


def qi(name: str) -> str:
    """Quote an identifier that came from the inspected schema."""
    return '"' + str(name).replace('"', '""') + '"'


@st.cache_data(show_spinner=False)
def _run(sql: str, params: tuple, db_path: str, mtime: float) -> pd.DataFrame:
    # mtime is part of the cache key, so replacing/updating the DB invalidates the cache.
    uri = Path(db_path).as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def q(sql: str, params=()) -> pd.DataFrame:
    """Parameterised read-only query helper (mirrors the notebooks' q())."""
    try:
        return _run(sql, tuple(params), str(DB_PATH), DB_PATH.stat().st_mtime)
    except Exception as exc:  # sqlite3.Error, pandas errors
        st.error(f"SQL error: {exc}")
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def discover_schema(db_path: str, mtime: float) -> dict[str, list[str]]:
    con = sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
        return {n: [r[1] for r in con.execute(f"PRAGMA table_info({qi(n)})")] for n in names}
    finally:
        con.close()


def detect(cols: list[str], candidates: list[str]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


# --------------------------------------------------------------------------
# Guard rails: database + schema
# --------------------------------------------------------------------------
st.markdown(CSS, unsafe_allow_html=True)
st.markdown('<div class="hdr"><h1>IPL Batters Analytics</h1>'
            '<p>Ball-by-ball batting analysis · super overs excluded · read-only database</p></div>',
            unsafe_allow_html=True)

if DB_PATH is None:
    tried = ", ".join(str(c) for c in DB_CANDIDATES if c)
    if CSV_LOAD_ERROR:
        st.error(f"Could not build the dashboard database from the source CSV files: {CSV_LOAD_ERROR}")
    else:
        st.error("No IPL data found. Expected a SQLite database at `data/raw/ipl.db` "
                 f"(looked in: {tried}) or both `data/raw/tables/deliveries.csv` and "
                 "`data/raw/tables/matches.csv`. Include the CSV files in your repository, "
                 "or set the `IPL_DB_PATH` environment variable to a database file.")
    st.stop()

SCHEMA = discover_schema(str(DB_PATH), DB_PATH.stat().st_mtime)
if "deliveries" not in SCHEMA or "matches" not in SCHEMA:
    st.error(f"Tables `deliveries` and `matches` are required. Found: {', '.join(SCHEMA) or 'none'}.")
    st.stop()

D_COLS, M_COLS = SCHEMA["deliveries"], SCHEMA["matches"]
SEASON_COL = detect(M_COLS, SEASON_CANDIDATES)

# --- sidebar: column mapping (auto-detected, user-overridable) ---
MAP: dict[str, str | None] = {}
with st.sidebar:
    st.markdown("### Data source")
    if CSV_MODE:
        st.caption("Imported from `data/raw/tables/deliveries.csv` and `matches.csv`")
    else:
        try:
            shown = DB_PATH.relative_to(ROOT)
        except ValueError:
            shown = DB_PATH
        st.caption(f"`{shown}`")
    with st.expander("Column mapping (auto-detected)"):
        st.caption("Detected from the real `deliveries` schema. Change only if a column was mis-detected.")
        for role, cands in ROLE_CANDIDATES.items():
            auto = detect(D_COLS, cands)
            opts = ["(none)"] + D_COLS
            idx = opts.index(auto) if auto in opts else 0
            pick = st.selectbox(role, opts, index=idx, key=f"map_{role}")
            MAP[role] = None if pick == "(none)" else pick

missing = [r for r in REQUIRED_ROLES if not MAP.get(r)]
if missing or not SEASON_COL or "match_id" not in M_COLS:
    st.error("Cannot build the dashboard: required columns are not mapped — "
             f"{', '.join(missing) or 'matches.season / matches.match_id'}. "
             f"`deliveries` columns: {', '.join(D_COLS)}. `matches` columns: {', '.join(M_COLS)}. "
             "Use the sidebar column mapping to point each role at a real column.")
    st.stop()

HAS_HAND = bool(MAP.get("hand"))
HAS_OUT = bool(MAP.get("player_out"))
HAS_KIND = bool(MAP.get("wicket_kind"))
HAS_WIDE = bool(MAP.get("wide"))


# --------------------------------------------------------------------------
# SQL expression builders (identifiers come from the inspected schema)
# --------------------------------------------------------------------------
def d(role: str) -> str:
    return f"d.{qi(MAP[role])}"


def team_expr(role: str) -> str:
    # repository rule (02_merge_categories.sql): merge Rising Pune Supergiants spelling
    return (f"(CASE WHEN {d(role)} = 'Rising Pune Supergiants' THEN 'Rising Pune Supergiant' "
            f"ELSE {d(role)} END)")


_over_min = q(f"SELECT MIN(CAST({d('over')} AS INTEGER)) AS lo FROM deliveries d")
OVER_OFFSET = 1 if (not _over_min.empty and _over_min["lo"].iloc[0] == 1) else 0

E = {
    "batter": d("batter"),
    "runs": f"COALESCE(CAST({d('bat_runs')} AS INTEGER),0)",
    "faced": (f"(CASE WHEN COALESCE(CAST({d('wide')} AS INTEGER),0)=0 THEN 1 ELSE 0 END)" if HAS_WIDE else "1"),
    "season": f"m.{qi(SEASON_COL)}",
    "bat_team": team_expr("batting_team"),
    "opp_team": team_expr("bowling_team"),
    "innings": d("innings"),
    "hand": d("hand") if HAS_HAND else "NULL",
    "phase": (f"(CASE WHEN CAST({d('over')} AS INTEGER) - {OVER_OFFSET} <= 5 THEN 'Powerplay' "
              f"WHEN CAST({d('over')} AS INTEGER) - {OVER_OFFSET} <= 14 THEN 'Middle' ELSE 'Death' END)"),
}
if HAS_OUT:
    cond = f"COALESCE(TRIM({d('player_out')}),'') <> '' AND TRIM({d('player_out')}) = TRIM({E['batter']})"
    if MAP.get("wicket"):
        cond = f"COALESCE(CAST({d('wicket')} AS INTEGER),0)=1 AND " + cond
    if HAS_KIND:
        cond += f" AND COALESCE({d('wicket_kind')},'') NOT IN ('retired hurt','retired not out')"
    E["out"] = f"(CASE WHEN {cond} THEN 1 ELSE 0 END)"
else:
    E["out"] = None

FROM_SQL = f"deliveries d JOIN matches m ON m.{qi('match_id')} = {d('match_id')}"
METRICS_SQL = f"""
    SUM({E['faced']}) AS balls,
    SUM({E['runs']}) AS runs,
    SUM(CASE WHEN {E['runs']}=4 THEN 1 ELSE 0 END) AS fours,
    SUM(CASE WHEN {E['runs']}=6 THEN 1 ELSE 0 END) AS sixes,
    SUM(CASE WHEN {E['faced']}=1 AND {E['runs']}=0 THEN 1 ELSE 0 END) AS dots,
    {('SUM(' + E['out'] + ')') if E['out'] else 'NULL'} AS outs,
    COUNT(DISTINCT {d('match_id')} || '-' || {d('innings')}) AS innings_n
"""


class Where:
    """Collects parameterised WHERE fragments."""

    def __init__(self):
        self.parts: list[str] = []
        self.params: list = []
        if MAP.get("super_over"):
            self.parts.append(f"COALESCE(CAST({d('super_over')} AS INTEGER),0) = 0")

    def isin(self, expr: str, values) -> None:
        values = list(values)
        self.parts.append(f"{expr} IN ({','.join('?' * len(values))})")
        self.params.extend(values)

    def eq(self, expr: str, value) -> None:
        self.parts.append(f"{expr} = ?")
        self.params.append(value)

    @property
    def sql(self) -> str:
        return " AND ".join(self.parts) if self.parts else "1=1"


def merge_sel(a, b):
    """Combine a global and a local multiselect. None = no restriction; [] = nothing matches."""
    if a and b:
        return [x for x in b if x in set(a)]
    return a or b or None


def make_where(seasons=None, teams=None, innings=None, phases=None, hands=None, batter=None,
               opponents=None, bat_team_exact=None):
    """Return a Where, or None when any supplied list is empty (=> no data)."""
    w = Where()
    for expr, vals in [(E["season"], seasons), (E["bat_team"], teams), (E["innings"], innings),
                       (E["phase"], phases), (E["hand"], hands), (E["opp_team"], opponents)]:
        if vals is None:
            continue
        if len(vals) == 0:
            return None
        w.isin(expr, vals)
    if batter is not None:
        w.eq(E["batter"], batter)
    return w


def season_key(s: str):
    m = re.match(r"\s*(\d{4})", str(s))
    return (int(m.group(1)) if m else 9999, str(s))


def add_rates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    for c in ["balls", "runs", "fours", "sixes", "dots", "outs", "innings_n"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["sr"] = df["runs"] / df["balls"].where(df["balls"] > 0) * 100
    df["avg"] = df["runs"] / df["outs"].where(df["outs"] > 0)
    df["bnd_pct"] = (4 * df["fours"] + 6 * df["sixes"]) / df["runs"].where(df["runs"] > 0) * 100
    df["dot_pct"] = df["dots"] / df["balls"].where(df["balls"] > 0) * 100
    return df


def agg(groups: list[tuple[str, str]], w: Where | None, min_balls: int = 0) -> pd.DataFrame:
    """groups = [(alias, sql_expr)]. Aggregates over the base join (one row per delivery)."""
    if w is None:
        return pd.DataFrame()
    gsel = "".join(f"{expr} AS {alias}, " for alias, expr in groups)
    gby = (" GROUP BY " + ", ".join(a for a, _ in groups)) if groups else ""
    having = " HAVING balls >= ?" if min_balls and groups else ""
    sql = f"SELECT {gsel}{METRICS_SQL} FROM {FROM_SQL} WHERE {w.sql}{gby}{having}"
    params = list(w.params) + ([min_balls] if having else [])
    df = q(sql, params)
    return add_rates(df)


def totals(w: Where | None) -> dict | None:
    df = agg([], w)
    if df.empty or pd.isna(df["balls"].iloc[0]) or df["balls"].iloc[0] == 0:
        return None  # an ungrouped aggregate over zero rows returns one all-NULL row
    return df.iloc[0].to_dict()


# --------------------------------------------------------------------------
# Reference data (options for filters) and full-dataset scale
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _options(db_path: str, mtime: float, sql: str) -> list:
    df = _run(sql, (), db_path, mtime)
    return [v for v in df.iloc[:, 0].tolist() if v is not None and str(v).strip() != ""]


def options(sql: str) -> list:
    try:
        return _options(str(DB_PATH), DB_PATH.stat().st_mtime, sql)
    except Exception as exc:
        st.error(f"SQL error: {exc}")
        return []


SEASONS = sorted(options(f"SELECT DISTINCT {qi(SEASON_COL)} FROM matches"), key=season_key)
TEAMS = sorted(options(f"SELECT DISTINCT {E['bat_team']} FROM deliveries d"))
HANDS = sorted(options(f"SELECT DISTINCT {E['hand']} FROM deliveries d")) if HAS_HAND else []


def data_scale() -> dict:
    df = q(f"SELECT COUNT(*) AS deliveries, COUNT(DISTINCT {d('match_id')}) AS matches, "
           f"COUNT(DISTINCT {E['batter']}) AS batters FROM deliveries d")
    return df.iloc[0].to_dict() if not df.empty else {}


def verified_finding() -> dict | None:
    """Read a recorded p-value from the repository's verification log (never recomputed or invented)."""
    try:
        text = VERIFICATION_LOG.read_text(encoding="utf-8")
    except OSError:
        return None
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and re.search(r"p\s*=\s*[0-9.]+", cells[1]):
            rows.append(cells)
    if not rows:
        return None
    pick = next((r for r in rows if "toss decision" in r[1].lower()), rows[0])
    p = float(re.search(r"p\s*=\s*([0-9.]+)", pick[1]).group(1))
    return {"label": pick[1], "p": p, "row": pick[0], "verdict": pick[-1]}


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------
def fmt(x, nd: int = 1, suffix: str = "") -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x:,.{nd}f}{suffix}"


def kpi(label: str, value: str, sub: str, tone: str = "cyan") -> None:
    st.markdown(
        f'<div class="kpi {tone}"><div class="lbl">{html.escape(label)}</div>'
        f'<div class="val">{html.escape(value)}</div><div class="sub">{html.escape(sub)}</div></div>',
        unsafe_allow_html=True,
    )


def chart_box(name: str):
    """Keyed container: gets the CSS class st-key-box_<name>, styled individually in BOX_STYLES."""
    try:
        return st.container(key=f"box_{name}")
    except TypeError:  # older Streamlit without container(key=...)
        return st.container(border=True)


EASE = {"ov_hero_chart": "cubic-in-out", "ov_bar_chart": "elastic-out", "ov_scatter_chart": "back-out",
        "ov_heatmap_chart": "sin-in-out", "ov_table_chart": "quad-out", "bt_hero_chart": "exp-out",
        "bt_phase_chart": "bounce-out", "bt_opp_chart": "quad-out", "bt_dismissal_chart": "circle-out",
        "bt_boundaries_chart": "cubic-out"}

# Chart-type choices offered above each visual (only types that are meaningful for that data).
CHART_TYPES = {
    "ov_hero_type": ["Line + volume", "Area", "Bar"],
    "ov_bar_type": ["Column bar", "Horizontal bar", "Lollipop"],
    "ov_sc_type": ["Bubble scatter", "Top-15 bar"],
    "ov_hm_type": ["Heatmap", "Lines by phase", "Grouped bars"],
    "ov_tb_type": ["Table", "Bar chart"],
    "bt_hero_type": ["Bars + SR line", "Lines", "Area + SR line"],
    "bt_ph_type": ["Grouped bar", "Radar", "Line"],
    "bt_op_type": ["Horizontal bar", "Column bar", "Bubble scatter"],
    "bt_dm_type": (["Donut", "Pie", "Bar"] if (HAS_OUT and HAS_KIND) else ["Box plot", "Violin", "Median bar"]),
    "bt_bd_type": ["Grouped bar", "Stacked bar", "Line", "Area"],
}


def ctype(container, key: str) -> str:
    return container.selectbox("Chart type", CHART_TYPES[key], key=key)


def show(fig: go.Figure, key: str) -> None:
    # each chart animates its own data updates with a different Plotly easing
    fig.update_layout(transition=dict(duration=650, easing=EASE.get(key, "cubic-in-out")))
    try:
        st.plotly_chart(fig, width="stretch", key=key)
    except TypeError:  # older Streamlit without width="stretch"
        st.plotly_chart(fig, use_container_width=True, key=key)


def show_table(df: pd.DataFrame, key: str, column_config=None) -> None:
    try:
        st.dataframe(df, width="stretch", hide_index=True, key=key, column_config=column_config)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True, key=key, column_config=column_config)


def empty(msg: str = "No deliveries match the current filters. Widen the selection above this chart.") -> None:
    st.info(msg)


def style(fig: go.Figure, title: str, subtitle: str, height: int) -> go.Figure:
    fig.update_layout(
        title=dict(text=f"{title}<br><sup style='color:{MUTED}'>{subtitle}</sup>", x=0.01, xanchor="left"),
        template="plotly_dark", height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, sans-serif", size=13, color="#e6edf7"),
        margin=dict(l=10, r=10, t=78, b=10), hoverlabel=dict(bgcolor="#0e1630", font_size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, automargin=True)
    return fig


def hover_cols(df: pd.DataFrame):
    return df[["balls", "runs", "fours", "sixes", "bnd_pct", "dot_pct"]].fillna(0).to_numpy()


HOVER = ("Balls faced: %{customdata[0]:,.0f}<br>Runs: %{customdata[1]:,.0f}<br>"
         "4s: %{customdata[2]:,.0f} · 6s: %{customdata[3]:,.0f}<br>"
         "Boundary share: %{customdata[4]:.1f}%<br>Dot-ball share: %{customdata[5]:.1f}%")


def add_lollipop(fig: go.Figure, cats, vals, colour: str, horizontal: bool = False) -> None:
    """Stems from zero to each value (the marker trace is added by the caller)."""
    xs, ys = [], []
    for c, v in zip(cats, vals):
        if horizontal:
            xs += [0, v, None]
            ys += [c, c, None]
        else:
            xs += [c, c, None]
            ys += [0, v, None]
    fig.add_scatter(x=xs, y=ys, mode="lines", line=dict(color=colour, width=3), hoverinfo="skip", showlegend=False)


SCALE_SEQ = [[0, "#6366f1"], [1, "#22d3ee"]]


# ==========================================================================
# OVERVIEW TAB
# ==========================================================================
def overview_tab() -> None:
    with st.sidebar:
        st.markdown("### Overview filters")
        g_seasons = st.multiselect("Season", SEASONS, key="ov_g_seasons", placeholder="All seasons",
                                   help="Empty = all seasons. Applies to every Overview visual.")
        g_teams = st.multiselect("Batting team", TEAMS, key="ov_g_teams", placeholder="All teams",
                                 help="Empty = all teams. Applies to every Overview visual.")
        g_min_balls = st.slider("Minimum balls faced (batter eligibility)", 50, 2000, 500, 50,
                                key="ov_g_minballs",
                                help="Applies to the batter scatter and leaderboard.")
        with st.expander("Definitions"):
            st.caption(
                "Super overs excluded. Balls faced exclude wides (no-balls count). Runs are off the bat. "
                "SR = runs/balls×100. Average = runs/dismissals. Phase: Powerplay overs 0–5, Middle 6–14, "
                "Death 15+. Fours/sixes = deliveries where the batter scored 4/6.")
            if not HAS_WIDE:
                st.warning("No wide-ball column mapped: every delivery is counted as a ball faced.")

    # ---- metric cards (3) ----
    scale = data_scale()
    base_w = make_where(seasons=g_seasons or None, teams=g_teams or None)
    tot = totals(base_w)
    finding = verified_finding()

    c1, c2, c3 = st.columns(3)
    with c1:
        if scale:
            kpi("Data scale · full dataset (before filters)", f"{int(scale['deliveries']):,} deliveries",
                f"{int(scale['matches']):,} matches · {int(scale['batters']):,} distinct batters", "cyan")
    with c2:
        if tot:
            kpi("League strike rate (filtered)", fmt(tot["sr"], 2),
                f"{int(tot['balls']):,} balls · {int(tot['runs']):,} runs · "
                f"boundary share {fmt(tot['bnd_pct'], 1, '%')}", "indigo")
        else:
            kpi("League strike rate (filtered)", "—", "No deliveries for this filter", "indigo")
    with c3:
        if finding:
            sig = finding["p"] < 0.05
            kpi(f"Verified finding · log row #{finding['row']}", f"p = {finding['p']:.4f}",
                f"{finding['label'].split(',')[0]} — "
                f"{'significant' if sig else 'not significant'} at the 5% level (recorded result, not recomputed). "
                "No confidence interval recorded. About toss, not batting.", "rose")
        else:
            kpi("Verified finding", "Unavailable",
                "No tested result found in reports/verification_log.md; nothing is claimed.", "rose")

    overview_hero(g_seasons, g_teams)

    left, right = st.columns(2)
    with left:
        overview_group_bar(g_seasons, g_teams)
    with right:
        overview_scatter(g_seasons, g_teams, g_min_balls)

    left, right = st.columns(2)
    with left:
        overview_heatmap(g_seasons, g_teams)
    with right:
        overview_table(g_seasons, g_teams, g_min_balls)


def overview_hero(g_seasons, g_teams) -> None:
    with chart_box("ov_hero"):
        st.markdown('<div class="tag">Hero · strike rate by season</div>', unsafe_allow_html=True)
        cols = st.columns(5 if HAS_HAND else 4)
        kind = ctype(cols[0], "ov_hero_type")
        s = cols[1].multiselect("Season", SEASONS, key="ov_hero_seasons", placeholder="All seasons")
        ph = cols[2].multiselect("Innings phase", PHASES, key="ov_hero_phase", placeholder="All phases")
        inn = cols[3].multiselect("Innings", INNINGS_OPTS, key="ov_hero_innings", placeholder="Both")
        hd = cols[4].multiselect("Batting hand", HANDS, key="ov_hero_hand", placeholder="Both") if HAS_HAND else None
        w = make_where(seasons=merge_sel(g_seasons, s), teams=g_teams or None, innings=inn or None,
                       phases=ph or None, hands=hd or None)
        df = agg([("season", E["season"])], w)
        if df.empty or df["balls"].fillna(0).sum() == 0:
            empty()
            return
        df = df.dropna(subset=["season"]).sort_values("season", key=lambda c: c.map(season_key))
        bench = df["runs"].sum() / df["balls"].sum() * 100
        top = df.loc[df["sr"].idxmax()]
        cd = hover_cols(df)
        ht = "<b>%{x}</b><br>Strike rate: %{y:.2f}<br>" + HOVER + "<extra></extra>"
        fig = go.Figure()
        if kind == "Bar":
            fig.add_bar(x=df["season"], y=df["sr"], name="Strike rate", marker_color=CYAN,
                        text=df["sr"].round(1), textposition="outside", customdata=cd, hovertemplate=ht)
            fig.update_yaxes(title="Strike rate (runs per 100 balls)", range=[0, df["sr"].max() * 1.18])
        elif kind == "Area":
            fig.add_scatter(x=df["season"], y=df["sr"], name="Strike rate", mode="lines+markers", fill="tozeroy",
                            fillcolor="rgba(34,211,238,0.16)", line=dict(color=CYAN, width=3),
                            marker=dict(size=8, color=INDIGO), customdata=cd, hovertemplate=ht)
            fig.update_yaxes(title="Strike rate (runs per 100 balls)", rangemode="tozero")
        else:  # Line + volume
            fig.add_bar(x=df["season"], y=df["balls"], name="Balls faced (n)", yaxis="y2",
                        marker_color="rgba(129,140,248,0.22)", hovertemplate="Balls faced: %{y:,.0f}<extra></extra>")
            fig.add_scatter(x=df["season"], y=df["sr"], mode="lines+markers+text", name="Strike rate",
                            line=dict(color=CYAN, width=3), marker=dict(size=9, color=ROSE),
                            text=df["sr"].round(1), textposition="top center", customdata=cd, hovertemplate=ht)
            fig.update_layout(yaxis2=dict(title="Balls faced", overlaying="y", side="right", showgrid=False))
            fig.update_yaxes(title="Strike rate (runs per 100 balls)")
        fig.add_hline(y=bench, line=dict(color=ROSE, dash="dash"),
                      annotation_text=f"Overall SR {bench:.1f}", annotation_position="bottom right")
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=list(df["season"]), title="Season")
        style(fig, f"Scoring pace peaked in {top['season']} at {top['sr']:.1f} runs per 100 balls "
                   f"(overall {bench:.1f})",
              f"n = {int(df['balls'].sum()):,} balls across {len(df)} seasons · dashed line = overall strike rate",
              520)
        show(fig, "ov_hero_chart")


def overview_group_bar(g_seasons, g_teams) -> None:
    with chart_box("ov_bar"):
        group_opts = ["Innings phase", "Innings (1st vs 2nd)"] + (["Batting hand"] if HAS_HAND else [])
        cols = st.columns(4)
        kind = ctype(cols[0], "ov_bar_type")
        grp = cols[1].selectbox("Compare by", group_opts, key="ov_bar_group")
        s = cols[2].multiselect("Season", SEASONS, key="ov_bar_seasons", placeholder="All seasons")
        t = cols[3].multiselect("Batting team", TEAMS, key="ov_bar_teams", placeholder="All teams")
        expr = {"Innings phase": E["phase"], "Innings (1st vs 2nd)": E["innings"], "Batting hand": E["hand"]}[grp]
        w = make_where(seasons=merge_sel(g_seasons, s), teams=merge_sel(g_teams, t))
        df = agg([("grp", expr)], w)
        if df.empty or df["balls"].fillna(0).sum() == 0:
            empty()
            return
        df = df.dropna(subset=["grp"])
        if grp == "Innings phase":
            df["grp"] = pd.Categorical(df["grp"], PHASES, ordered=True)
        df = df.sort_values("grp")
        df["label"] = df["grp"].astype(str)
        if grp == "Innings (1st vs 2nd)":
            df["label"] = df["label"].map(lambda v: f"Innings {int(float(v))}")
        bench = df["runs"].sum() / df["balls"].sum() * 100
        cd = hover_cols(df)
        ht = "<b>%{" + ("y" if kind == "Horizontal bar" else "x") + "}</b><br>Strike rate: %{" + \
             ("x" if kind == "Horizontal bar" else "y") + ":.2f}<br>" + HOVER + "<extra></extra>"
        labels = [f"{v:.1f} · n={int(n):,}" for v, n in zip(df["sr"], df["balls"])]
        colours = (ACCENTS[: len(df)] if len(df) <= len(ACCENTS) else CYAN)
        fig = go.Figure()
        if kind == "Horizontal bar":
            fig.add_bar(y=df["label"], x=df["sr"], orientation="h", marker_color=colours, text=labels,
                        textposition="outside", customdata=cd, hovertemplate=ht)
            fig.add_vline(x=bench, line=dict(color=ROSE, dash="dash"),
                          annotation_text=f"Overall {bench:.1f}", annotation_position="top")
            fig.update_xaxes(title="Strike rate", range=[0, df["sr"].max() * 1.35])
        elif kind == "Lollipop":
            add_lollipop(fig, list(df["label"]), list(df["sr"]), MUTED)
            fig.add_scatter(x=df["label"], y=df["sr"], mode="markers+text", text=labels, textposition="top center",
                            marker=dict(size=16, color=colours, line=dict(width=2, color="#0b1226")),
                            customdata=cd, hovertemplate=ht, showlegend=False)
            fig.add_hline(y=bench, line=dict(color=ROSE, dash="dash"),
                          annotation_text=f"Overall {bench:.1f}", annotation_position="top left")
            fig.update_yaxes(title="Strike rate", range=[0, df["sr"].max() * 1.25])
        else:  # Column bar
            fig.add_bar(x=df["label"], y=df["sr"], marker_color=colours, text=labels, textposition="outside",
                        customdata=cd, hovertemplate=ht)
            fig.add_hline(y=bench, line=dict(color=ROSE, dash="dash"),
                          annotation_text=f"Overall {bench:.1f}", annotation_position="top left")
            fig.update_yaxes(title="Strike rate", range=[0, df["sr"].max() * 1.25])
        best = df.loc[df["sr"].idxmax()]
        style(fig, f"{best['label']} has the highest strike rate ({best['sr']:.1f})",
              f"n = {int(df['balls'].sum()):,} balls · grouped by {grp.lower()}", 400)
        show(fig, "ov_bar_chart")


def overview_scatter(g_seasons, g_teams, min_balls) -> None:
    with chart_box("ov_scatter"):
        cols = st.columns(3)
        kind = ctype(cols[0], "ov_sc_type")
        s = cols[1].multiselect("Season", SEASONS, key="ov_sc_seasons", placeholder="All seasons")
        ph = cols[2].multiselect("Innings phase", PHASES, key="ov_sc_phase", placeholder="All phases")
        w = make_where(seasons=merge_sel(g_seasons, s), teams=g_teams or None, phases=ph or None)
        df = agg([("batter", E["batter"])], w, min_balls=min_balls)
        if df.empty:
            empty(f"No batter has faced at least {min_balls:,} balls under these filters.")
            return
        bench = df["runs"].sum() / df["balls"].sum() * 100
        if kind == "Top-15 bar":
            top = df.dropna(subset=["sr"]).sort_values("sr", ascending=False).head(15).sort_values("sr")
            fig = go.Figure(go.Bar(
                y=top["batter"], x=top["sr"], orientation="h", customdata=hover_cols(top),
                marker=dict(color=top["sr"], colorscale=SCALE_SEQ),
                text=[f"{v:.1f} · n={int(n):,}" for v, n in zip(top["sr"], top["balls"])], textposition="outside",
                hovertemplate="<b>%{y}</b><br>Strike rate: %{x:.1f}<br>" + HOVER + "<extra></extra>"))
            fig.add_vline(x=bench, line=dict(color=ROSE, dash="dash"),
                          annotation_text=f"Group SR {bench:.1f}", annotation_position="top")
            fig.update_xaxes(title="Strike rate", range=[0, top["sr"].max() * 1.3])
            lead = top.iloc[-1]
            style(fig, f"{lead['batter']} leads eligible batters on strike rate ({lead['sr']:.1f})",
                  f"n = {len(df)} batters with ≥{min_balls:,} balls · top {len(top)} shown", 400)
            show(fig, "ov_scatter_chart")
            return
        xcol, xlab = ("avg", "Batting average") if HAS_OUT and df["avg"].notna().any() else ("runs", "Runs scored")
        df = df.dropna(subset=[xcol, "sr"]).sort_values("balls", ascending=False).head(250)
        if df.empty:
            empty("Not enough dismissals to compute averages under these filters.")
            return
        fig = go.Figure(go.Scatter(
            x=df[xcol], y=df["sr"], mode="markers", text=df["batter"],
            marker=dict(size=df["balls"], sizemode="area", sizeref=2.0 * df["balls"].max() / (34.0 ** 2),
                        sizemin=5, color=df["bnd_pct"], colorscale=SCALE_SEQ,
                        showscale=True, colorbar=dict(title="Boundary<br>share %"), opacity=0.85,
                        line=dict(width=0.5, color="#0b1226")),
            customdata=hover_cols(df),
            hovertemplate="<b>%{text}</b><br>" + xlab + ": %{x:.1f}<br>Strike rate: %{y:.1f}<br>" + HOVER + "<extra></extra>"))
        fig.add_hline(y=bench, line=dict(color=ROSE, dash="dash"),
                      annotation_text=f"Group SR {bench:.1f}", annotation_position="bottom right")
        fig.update_xaxes(title=xlab)
        fig.update_yaxes(title="Strike rate")
        lead = df.loc[df["sr"].idxmax()]
        style(fig, f"{lead['batter']} leads eligible batters on strike rate ({lead['sr']:.1f})",
              f"n = {len(df)} batters with ≥{min_balls:,} balls · bubble size = balls faced", 400)
        show(fig, "ov_scatter_chart")


def overview_heatmap(g_seasons, g_teams) -> None:
    min_cell = 300
    with chart_box("ov_heatmap"):
        cols = st.columns(4)
        kind = ctype(cols[0], "ov_hm_type")
        s = cols[1].multiselect("Season", SEASONS, key="ov_hm_seasons", placeholder="All seasons")
        t = cols[2].multiselect("Batting team", TEAMS, key="ov_hm_teams", placeholder="All teams")
        inn = cols[3].multiselect("Innings", INNINGS_OPTS, key="ov_hm_innings", placeholder="Both")
        w = make_where(seasons=merge_sel(g_seasons, s), teams=merge_sel(g_teams, t), innings=inn or None)
        df = agg([("season", E["season"]), ("phase", E["phase"])], w)
        if df.empty:
            empty()
            return
        df = df.dropna(subset=["season", "phase"])
        df.loc[df["balls"] < min_cell, "sr"] = float("nan")
        order = sorted(df["season"].unique(), key=season_key)
        piv = df.pivot(index="phase", columns="season", values="sr").reindex(index=PHASES, columns=order)
        cnt = df.pivot(index="phase", columns="season", values="balls").reindex(index=PHASES, columns=order).fillna(0)
        if piv.isna().all().all():
            empty(f"No season/phase cell has at least {min_cell} balls.")
            return
        fig = go.Figure()
        if kind == "Heatmap":
            fig.add_heatmap(
                z=piv.values, x=order, y=PHASES, customdata=cnt.values,
                colorscale=[[0, "#0f1733"], [0.5, INDIGO], [1, CYAN]],
                text=piv.round(0).fillna("").astype(str).replace(r"\.0$", "", regex=True).values,
                texttemplate="%{text}", colorbar=dict(title="SR"), hoverongaps=False,
                hovertemplate="<b>%{x} · %{y}</b><br>Strike rate: %{z:.1f}<br>Balls faced: %{customdata:,.0f}<extra></extra>")
        else:
            for colour, phase in zip(ACCENTS, PHASES):
                if kind == "Lines by phase":
                    fig.add_scatter(x=order, y=piv.loc[phase], name=phase, mode="lines+markers",
                                    line=dict(color=colour, width=3), customdata=cnt.loc[phase],
                                    connectgaps=False,
                                    hovertemplate="<b>%{x} · " + phase + "</b><br>Strike rate: %{y:.1f}<br>"
                                                  "Balls faced: %{customdata:,.0f}<extra></extra>")
                else:  # Grouped bars
                    fig.add_bar(x=order, y=piv.loc[phase], name=phase, marker_color=colour,
                                customdata=cnt.loc[phase],
                                hovertemplate="<b>%{x} · " + phase + "</b><br>Strike rate: %{y:.1f}<br>"
                                              "Balls faced: %{customdata:,.0f}<extra></extra>")
            fig.update_layout(barmode="group")
            fig.update_yaxes(title="Strike rate", rangemode="tozero")
        fig.update_xaxes(type="category", title="Season")
        by_phase = df.groupby("phase")[["runs", "balls"]].sum()
        by_phase["sr"] = by_phase["runs"] / by_phase["balls"] * 100
        best = by_phase["sr"].idxmax()
        style(fig, f"{best} is the fastest-scoring phase ({by_phase.loc[best, 'sr']:.1f} SR overall)",
              f"n = {int(df['balls'].sum()):,} balls · season/phase cells with <{min_cell} balls left blank", 400)
        show(fig, "ov_heatmap_chart")


def overview_table(g_seasons, g_teams, min_balls) -> None:
    with chart_box("ov_table"):
        cols = st.columns(4)
        kind = ctype(cols[0], "ov_tb_type")
        s = cols[1].multiselect("Season", SEASONS, key="ov_tb_seasons", placeholder="All seasons")
        sort_by = cols[2].selectbox("Rank by", ["Runs", "Strike rate", "Average", "Sixes", "Boundary share"],
                                    key="ov_tb_sort")
        top_n = cols[3].slider("Rows", 5, 25, 10, key="ov_tb_top")
        w = make_where(seasons=merge_sel(g_seasons, s), teams=g_teams or None)
        df = agg([("batter", E["batter"])], w, min_balls=min_balls)
        if df.empty:
            empty(f"No batter has faced at least {min_balls:,} balls under these filters.")
            return
        key_col = {"Runs": "runs", "Strike rate": "sr", "Average": "avg", "Sixes": "sixes",
                   "Boundary share": "bnd_pct"}[sort_by]
        if key_col == "avg" and not HAS_OUT:
            st.warning("No dismissed-player column mapped, so batting average is unavailable; ranking by runs.")
            key_col, sort_by = "runs", "Runs"
        df = df.dropna(subset=[key_col]).sort_values(key_col, ascending=False).head(top_n)
        if df.empty:
            empty("No rows have a value for this ranking metric.")
            return
        if kind == "Bar chart":
            plot = df.sort_values(key_col)
            fig = go.Figure(go.Bar(
                y=plot["batter"], x=plot[key_col], orientation="h", customdata=hover_cols(plot),
                marker=dict(color=plot[key_col], colorscale=SCALE_SEQ),
                text=[f"{v:,.1f}" if key_col in ("sr", "avg", "bnd_pct") else f"{v:,.0f}" for v in plot[key_col]],
                textposition="outside",
                hovertemplate="<b>%{y}</b><br>" + sort_by + ": %{x:,.1f}<br>" + HOVER + "<extra></extra>"))
            fig.update_xaxes(title=sort_by, range=[0, plot[key_col].max() * 1.2])
            lead = plot.iloc[-1]
            style(fig, f"{lead['batter']} ranks first by {sort_by.lower()}",
                  f"n = {len(df)} batters shown · eligibility ≥{min_balls:,} balls", 400)
            show(fig, "ov_table_chart")
            return
        st.markdown(f"**Top {len(df)} batters by {sort_by.lower()}**")
        st.caption(f"Eligibility: ≥{min_balls:,} balls faced · n = {len(df)} rows shown")
        out = pd.DataFrame({
            "Batter": df["batter"], "Balls": df["balls"].astype(int), "Runs": df["runs"].astype(int),
            "SR": df["sr"].round(2), "Avg": df["avg"].round(2), "4s": df["fours"].astype(int),
            "6s": df["sixes"].astype(int), "Boundary %": df["bnd_pct"].round(1), "Dot %": df["dot_pct"].round(1)})
        show_table(out, "ov_table")


# ==========================================================================
# BATTER TAB
# ==========================================================================
def batter_tab() -> None:
    with st.container(border=True):
        c1, c2, c3 = st.columns([2, 1, 2])
        min_balls = c2.slider("Eligibility: min balls faced", 50, 2000, 200, 50, key="bt_minballs")
        t_seasons = c3.multiselect("Season (this tab)", SEASONS, key="bt_seasons", placeholder="All seasons")
        elig_w = make_where(seasons=t_seasons or None)
        elig = agg([("batter", E["batter"])], elig_w, min_balls=min_balls)
        if elig.empty:
            c1.selectbox("Batter", ["(none eligible)"], key="bt_batter", disabled=True)
            st.info(f"No batter has faced at least {min_balls:,} balls in the selected seasons. "
                    "Lower the eligibility threshold or widen the season selection.")
            return
        names = elig.sort_values("runs", ascending=False)["batter"].tolist()
        bat = c1.selectbox("Batter", names, key="bt_batter",
                           help=f"{len(names)} eligible batters (≥{min_balls:,} balls), ordered by runs.")

    w_all = make_where(seasons=t_seasons or None, batter=bat)
    tot = totals(w_all)
    lg = totals(make_where(seasons=t_seasons or None))

    k1, k2, k3 = st.columns(3)
    with k1:
        if tot:
            kpi(f"Runs · {bat}", f"{int(tot['runs']):,}",
                f"{int(tot['balls']):,} balls · {int(tot['innings_n']):,} innings · "
                f"{int(tot['fours']):,} fours · {int(tot['sixes']):,} sixes", "cyan")
        else:
            kpi(f"Runs · {bat}", "—", "No deliveries for this selection", "cyan")
    with k2:
        if tot and lg:
            delta = tot["sr"] - lg["sr"]
            kpi("Strike rate", fmt(tot["sr"], 2),
                f"{delta:+.1f} vs league SR {lg['sr']:.1f} · dot-ball share {fmt(tot['dot_pct'], 1, '%')}", "indigo")
        else:
            kpi("Strike rate", "—", "No deliveries for this selection", "indigo")
    with k3:
        if tot and HAS_OUT:
            kpi("Batting average", fmt(tot["avg"], 2),
                f"{int(tot['outs'] or 0):,} dismissals · boundary share of runs {fmt(tot['bnd_pct'], 1, '%')}", "rose")
        else:
            kpi("Batting average", "Unavailable",
                "No dismissed-player column is mapped, so dismissals cannot be attributed." if not HAS_OUT
                else "—", "rose")

    batter_hero(bat, t_seasons)

    left, right = st.columns(2)
    with left:
        batter_phase_chart(bat, t_seasons)
    with right:
        batter_opponent_chart(bat, t_seasons)
    left, right = st.columns(2)
    with left:
        batter_dismissal_chart(bat, t_seasons)
    with right:
        batter_boundaries_chart(bat, t_seasons)


def batter_hero(bat: str, t_seasons) -> None:
    with chart_box("bt_hero"):
        st.markdown('<div class="tag">Hero · season-by-season</div>', unsafe_allow_html=True)
        cols = st.columns(4)
        kind = ctype(cols[0], "bt_hero_type")
        s = cols[1].multiselect("Season", SEASONS, key="bt_hero_seasons", placeholder="All seasons")
        ph = cols[2].multiselect("Innings phase", PHASES, key="bt_hero_phase", placeholder="All phases")
        inn = cols[3].multiselect("Innings", INNINGS_OPTS, key="bt_hero_innings", placeholder="Both")
        w = make_where(seasons=merge_sel(t_seasons, s), batter=bat, innings=inn or None, phases=ph or None)
        df = agg([("season", E["season"])], w)
        if df.empty or df["balls"].fillna(0).sum() == 0:
            empty(f"{bat} has no deliveries for these filters.")
            return
        df = df.dropna(subset=["season"]).sort_values("season", key=lambda c: c.map(season_key))
        career = df["runs"].sum() / df["balls"].sum() * 100
        top = df.loc[df["runs"].idxmax()]
        cd = hover_cols(df)
        ht = "<b>%{x}</b><br>Runs: %{y:,.0f}<br>" + HOVER + "<extra></extra>"
        fig = go.Figure()
        if kind == "Lines":
            fig.add_scatter(x=df["season"], y=df["runs"], name="Runs", mode="lines+markers",
                            line=dict(color=INDIGO, width=3), marker=dict(size=8), customdata=cd, hovertemplate=ht)
        elif kind == "Area + SR line":
            fig.add_scatter(x=df["season"], y=df["runs"], name="Runs", mode="lines+markers", fill="tozeroy",
                            fillcolor="rgba(129,140,248,0.20)", line=dict(color=INDIGO, width=3),
                            marker=dict(size=7), customdata=cd, hovertemplate=ht)
        else:  # Bars + SR line
            fig.add_bar(x=df["season"], y=df["runs"], name="Runs", marker_color=INDIGO, customdata=cd,
                        text=df["runs"].astype(int), textposition="outside", hovertemplate=ht)
        fig.add_scatter(x=df["season"], y=df["sr"], name="Strike rate", yaxis="y2", mode="lines+markers",
                        line=dict(color=ROSE, width=3), marker=dict(size=8),
                        hovertemplate="Strike rate: %{y:.1f}<extra></extra>")
        fig.add_hline(y=career, yref="y2", line=dict(color=CYAN, dash="dash"),
                      annotation_text=f"Selection SR {career:.1f}", annotation_position="top left")
        fig.update_layout(yaxis2=dict(title="Strike rate", overlaying="y", side="right", showgrid=False))
        fig.update_yaxes(title="Runs", rangemode="tozero")
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=list(df["season"]), title="Season")
        style(fig, f"{bat}'s best season for runs: {top['season']} ({int(top['runs']):,})",
              f"n = {int(df['balls'].sum()):,} balls, {int(df['innings_n'].sum()):,} innings · "
              "dashed line = strike rate across the selection", 520)
        show(fig, "bt_hero_chart")


def batter_phase_chart(bat: str, t_seasons) -> None:
    with chart_box("bt_phase"):
        cols = st.columns(3)
        kind = ctype(cols[0], "bt_ph_type")
        s = cols[1].multiselect("Season", SEASONS, key="bt_ph_seasons", placeholder="All seasons")
        inn = cols[2].multiselect("Innings", INNINGS_OPTS, key="bt_ph_innings", placeholder="Both")
        se = merge_sel(t_seasons, s)
        mine = agg([("phase", E["phase"])], make_where(seasons=se, batter=bat, innings=inn or None))
        league = agg([("phase", E["phase"])], make_where(seasons=se, innings=inn or None))
        if mine.empty or mine["balls"].fillna(0).sum() == 0:
            empty(f"{bat} has no deliveries for these filters.")
            return
        mine = mine.set_index("phase").reindex(PHASES)
        league = league.set_index("phase").reindex(PHASES)
        mine_cd = mine[["balls", "runs", "fours", "sixes", "bnd_pct", "dot_pct"]].fillna(0).to_numpy()
        labels = [("—" if pd.isna(v) else f"{v:.1f}<br>n={int(n):,}") for v, n in
                  zip(mine["sr"], mine["balls"].fillna(0))]
        ymax = max(mine["sr"].max(), league["sr"].max())
        fig = go.Figure()
        if kind == "Radar":
            theta = PHASES + [PHASES[0]]
            fig.add_scatterpolar(r=list(mine["sr"].fillna(0)) + [mine["sr"].fillna(0).iloc[0]], theta=theta,
                                 name=bat, fill="toself", fillcolor="rgba(34,211,238,0.20)",
                                 line=dict(color=CYAN, width=3),
                                 hovertemplate="<b>%{theta}</b><br>" + bat + " SR: %{r:.1f}<extra></extra>")
            fig.add_scatterpolar(r=list(league["sr"].fillna(0)) + [league["sr"].fillna(0).iloc[0]], theta=theta,
                                 name="League", fill="toself", fillcolor="rgba(251,113,133,0.12)",
                                 line=dict(color=ROSE, width=2, dash="dot"),
                                 hovertemplate="<b>%{theta}</b><br>League SR: %{r:.1f}<extra></extra>")
            fig.update_layout(polar=dict(bgcolor="rgba(0,0,0,0)",
                                         radialaxis=dict(gridcolor=GRID, range=[0, ymax * 1.15]),
                                         angularaxis=dict(gridcolor=GRID)))
        elif kind == "Line":
            fig.add_scatter(x=PHASES, y=mine["sr"], name=bat, mode="lines+markers+text", text=labels,
                            textposition="top center", line=dict(color=CYAN, width=3), customdata=mine_cd,
                            hovertemplate="<b>%{x}</b><br>Strike rate: %{y:.2f}<br>" + HOVER + "<extra></extra>")
            fig.add_scatter(x=PHASES, y=league["sr"], name="League", mode="lines+markers",
                            line=dict(color=ROSE, width=2, dash="dot"),
                            hovertemplate="<b>%{x}</b><br>League strike rate: %{y:.2f}<extra></extra>")
            fig.update_yaxes(title="Strike rate", range=[0, ymax * 1.3])
        else:  # Grouped bar
            fig.add_bar(x=PHASES, y=mine["sr"], name=bat, marker_color=CYAN, text=labels, textposition="outside",
                        customdata=mine_cd,
                        hovertemplate="<b>%{x}</b><br>Strike rate: %{y:.2f}<br>" + HOVER + "<extra></extra>")
            fig.add_bar(x=PHASES, y=league["sr"], name="League", marker_color="rgba(251,113,133,0.75)",
                        hovertemplate="<b>%{x}</b><br>League strike rate: %{y:.2f}<extra></extra>")
            fig.update_layout(barmode="group")
            fig.update_yaxes(title="Strike rate", range=[0, ymax * 1.25])
        valid = mine["sr"].dropna()
        best = valid.idxmax() if not valid.empty else None
        title = (f"{bat} scores fastest in the {best} phase ({valid.max():.1f} SR)" if best else
                 f"{bat}: no phase data")
        style(fig, title, f"n = {int(mine['balls'].sum()):,} balls · League = all batters, same filters", 400)
        show(fig, "bt_phase_chart")


def batter_opponent_chart(bat: str, t_seasons) -> None:
    min_opp = 30
    with chart_box("bt_opp"):
        cols = st.columns(3)
        kind = ctype(cols[0], "bt_op_type")
        s = cols[1].multiselect("Season", SEASONS, key="bt_op_seasons", placeholder="All seasons")
        top_n = cols[2].slider("Opponents shown", 3, 15, 10, key="bt_op_top")
        w = make_where(seasons=merge_sel(t_seasons, s), batter=bat)
        df = agg([("opp", E["opp_team"])], w, min_balls=min_opp)
        if df.empty:
            empty(f"No opponent with ≥{min_opp} balls faced by {bat} under these filters.")
            return
        df = df.sort_values("runs", ascending=False).head(top_n).sort_values("runs")
        cd = hover_cols(df)
        text = [f"{int(r):,} · SR {s_:.0f}" for r, s_ in zip(df["runs"], df["sr"])]
        marker = dict(color=df["sr"], colorscale=SCALE_SEQ, showscale=True, colorbar=dict(title="SR"))
        fig = go.Figure()
        if kind == "Column bar":
            d2 = df.sort_values("runs", ascending=False)
            fig.add_bar(x=d2["opp"], y=d2["runs"], customdata=hover_cols(d2),
                        marker=dict(color=d2["sr"], colorscale=SCALE_SEQ, showscale=True, colorbar=dict(title="SR")),
                        text=[f"{int(r):,}" for r in d2["runs"]], textposition="outside",
                        hovertemplate="<b>%{x}</b><br>Runs: %{y:,.0f}<br>" + HOVER + "<extra></extra>")
            fig.update_yaxes(title="Runs scored", range=[0, d2["runs"].max() * 1.2])
        elif kind == "Bubble scatter":
            fig.add_scatter(x=df["runs"], y=df["sr"], mode="markers+text", text=df["opp"], textposition="top center",
                            marker=dict(size=df["balls"], sizemode="area",
                                        sizeref=2.0 * df["balls"].max() / (40.0 ** 2), sizemin=8,
                                        color=df["sr"], colorscale=SCALE_SEQ, opacity=0.85,
                                        line=dict(width=0.5, color="#0b1226")),
                            customdata=cd,
                            hovertemplate="<b>%{text}</b><br>Runs: %{x:,.0f} · SR: %{y:.1f}<br>" + HOVER + "<extra></extra>")
            fig.update_xaxes(title="Runs scored")
            fig.update_yaxes(title="Strike rate")
        else:  # Horizontal bar
            fig.add_bar(y=df["opp"], x=df["runs"], orientation="h", customdata=cd, marker=marker, text=text,
                        textposition="outside",
                        hovertemplate="<b>%{y}</b><br>Runs: %{x:,.0f}<br>" + HOVER + "<extra></extra>")
            fig.update_xaxes(title="Runs scored", range=[0, df["runs"].max() * 1.3])
        lead = df.iloc[-1]
        style(fig, f"{bat} has scored most against {lead['opp']} ({int(lead['runs']):,} runs)",
              f"n = {int(df['balls'].sum()):,} balls · opponents with ≥{min_opp} balls · colour/size = strike rate/balls",
              400)
        show(fig, "bt_opp_chart")


def batter_dismissal_chart(bat: str, t_seasons) -> None:
    with chart_box("bt_dismissal"):
        cols = st.columns(2)
        kind = ctype(cols[0], "bt_dm_type")
        s = cols[1].multiselect("Season", SEASONS, key="bt_dm_seasons", placeholder="All seasons")
        se = merge_sel(t_seasons, s)
        w = make_where(seasons=se, batter=bat)
        if w is None:
            empty()
            return
        if HAS_OUT and HAS_KIND:
            w.parts.append(f"{E['out']} = 1")
            df = q(f"SELECT {d('wicket_kind')} AS kind, COUNT(*) AS n FROM {FROM_SQL} WHERE {w.sql} "
                   f"GROUP BY kind ORDER BY n DESC", w.params)
            if df.empty:
                empty(f"No recorded dismissals for {bat} under these filters.")
                return
            df["kind"] = df["kind"].fillna("unknown")
            top = df.iloc[0]
            fig = go.Figure()
            if kind == "Bar":
                d2 = df.sort_values("n")
                fig.add_bar(y=d2["kind"], x=d2["n"], orientation="h", marker_color=CYAN, text=d2["n"],
                            textposition="outside",
                            hovertemplate="<b>%{y}</b><br>%{x} dismissals<extra></extra>")
                fig.update_xaxes(title="Dismissals", range=[0, d2["n"].max() * 1.2])
            else:
                fig.add_pie(labels=df["kind"], values=df["n"], hole=0.55 if kind == "Donut" else 0, sort=False,
                            marker=dict(colors=ACCENTS + [MUTED]), textinfo="label+percent",
                            hovertemplate="<b>%{label}</b><br>%{value} dismissals (%{percent})<extra></extra>")
            style(fig, f"{bat} is most often out {top['kind']} ({int(top['n'])} of {int(df['n'].sum())})",
                  f"n = {int(df['n'].sum())} dismissals", 400)
            show(fig, "bt_dismissal_chart")
            return
        # Dismissal data unavailable: show a valid alternative (distribution of runs per innings).
        st.caption("Dismissal types need dismissed-player and dismissal-kind columns, which are not mapped. "
                   "Showing runs per innings instead.")
        df = q(f"SELECT {E['season']} AS season, {d('match_id')} AS mid, {d('innings')} AS inn, "
               f"SUM({E['runs']}) AS runs FROM {FROM_SQL} WHERE {w.sql} GROUP BY season, mid, inn", w.params)
        if df.empty:
            empty()
            return
        df = df.sort_values("season", key=lambda c: c.map(season_key))
        fig = go.Figure()
        if kind == "Violin":
            fig.add_violin(x=df["season"], y=df["runs"], line_color=CYAN, box_visible=True, meanline_visible=True,
                           points=False, hovertemplate="Runs: %{y}<extra></extra>")
        elif kind == "Median bar":
            med = df.groupby("season", sort=False)["runs"].median().reset_index()
            fig.add_bar(x=med["season"], y=med["runs"], marker_color=CYAN, text=med["runs"].round(0),
                        textposition="outside", hovertemplate="<b>%{x}</b><br>Median runs: %{y:.0f}<extra></extra>")
        else:
            fig.add_box(x=df["season"], y=df["runs"], marker_color=CYAN, boxmean=True)
        fig.update_xaxes(type="category", title="Season")
        fig.update_yaxes(title="Runs per innings")
        style(fig, f"{bat}: median innings score {df['runs'].median():.0f}", f"n = {len(df)} innings", 400)
        show(fig, "bt_dismissal_chart")


def batter_boundaries_chart(bat: str, t_seasons) -> None:
    with chart_box("bt_boundaries"):
        cols = st.columns(3)
        kind = ctype(cols[0], "bt_bd_type")
        ph = cols[1].multiselect("Innings phase", PHASES, key="bt_bd_phase", placeholder="All phases")
        inn = cols[2].multiselect("Innings", INNINGS_OPTS, key="bt_bd_innings", placeholder="Both")
        w = make_where(seasons=t_seasons or None, batter=bat, phases=ph or None, innings=inn or None)
        df = agg([("season", E["season"])], w)
        if df.empty or df["balls"].fillna(0).sum() == 0:
            empty(f"{bat} has no deliveries for these filters.")
            return
        df = df.dropna(subset=["season"]).sort_values("season", key=lambda c: c.map(season_key))
        cd = hover_cols(df)
        h4 = "<b>%{x}</b><br>Fours: %{y:,.0f}<br>" + HOVER + "<extra></extra>"
        h6 = "<b>%{x}</b><br>Sixes: %{y:,.0f}<extra></extra>"
        fig = go.Figure()
        if kind == "Line":
            fig.add_scatter(x=df["season"], y=df["fours"], name="Fours", mode="lines+markers",
                            line=dict(color=INDIGO, width=3), customdata=cd, hovertemplate=h4)
            fig.add_scatter(x=df["season"], y=df["sixes"], name="Sixes", mode="lines+markers",
                            line=dict(color=ROSE, width=3), hovertemplate=h6)
        elif kind == "Area":
            fig.add_scatter(x=df["season"], y=df["fours"], name="Fours", mode="lines", stackgroup="one",
                            line=dict(color=INDIGO, width=2), fillcolor="rgba(129,140,248,0.35)",
                            customdata=cd, hovertemplate=h4)
            fig.add_scatter(x=df["season"], y=df["sixes"], name="Sixes", mode="lines", stackgroup="one",
                            line=dict(color=ROSE, width=2), fillcolor="rgba(251,113,133,0.35)", hovertemplate=h6)
        else:  # Grouped / Stacked bar
            fig.add_bar(x=df["season"], y=df["fours"], name="Fours", marker_color=INDIGO, customdata=cd,
                        hovertemplate=h4)
            fig.add_bar(x=df["season"], y=df["sixes"], name="Sixes", marker_color=ROSE, hovertemplate=h6)
            fig.update_layout(barmode="stack" if kind == "Stacked bar" else "group")
        fig.update_xaxes(type="category", title="Season")
        fig.update_yaxes(title="Count", rangemode="tozero")
        total4, total6 = int(df["fours"].sum()), int(df["sixes"].sum())
        style(fig, f"{bat}: {total4:,} fours and {total6:,} sixes",
              f"n = {int(df['balls'].sum()):,} balls · boundary = 4 or 6 runs off the bat", 400)
        show(fig, "bt_boundaries_chart")


# ==========================================================================
# Main
# ==========================================================================
tab_overview, tab_batter = st.tabs(["Overview", "Batter"])
with tab_overview:
    overview_tab()
with tab_batter:
    batter_tab()
