"""Seed CLEARLY-FICTIONAL demo data into the canonical store so the live Gateway
endpoints (search/fetch/recent + REST) return real results for testing.

The persona is a fictional maker, "Robin Vale" - none of this is Tory's data.
All projects are tagged `demo-*`; notes/time use source='demo'; interpretive
rows carry a 'demo' marker. Run `--purge` to drop everything this created
(do that before migrating the real corpus).

Usage:
  DB_URL="mssql+pymssql://USER:PWD@SERVER.database.windows.net:1433/cortex" \
      python scripts/seed_demo_data.py            # seed
  DB_URL=... python scripts/seed_demo_data.py --purge   # drop the seeded tables

It also works against the local SQLite dev DB (set CORTEX_DB_PATH instead).
Creates the needed tables (portable DDL) if missing; safe to re-run (purges
demo rows first).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import sqlalchemy as sa

# Reuse the Gateway's engine resolution so DB_URL / CORTEX_DB_PATH work the same.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex_gateway import db  # noqa: E402

md = sa.MetaData()
NOW = sa.text("CURRENT_TIMESTAMP")


def _t(name, *cols):
    return sa.Table(name, md, *cols)


projects = _t("projects",
    sa.Column("tag", sa.String(80), primary_key=True),
    sa.Column("name", sa.String(200)), sa.Column("status", sa.String(40)),
    sa.Column("priority", sa.Integer), sa.Column("description", sa.Text),
    sa.Column("category", sa.String(40)), sa.Column("org_tag", sa.String(80)),
    sa.Column("github_url", sa.String(300)), sa.Column("total_hours", sa.Float),
    sa.Column("collaborators", sa.String(400)),
    sa.Column("last_touched", sa.DateTime), sa.Column("created_at", sa.DateTime))

notes = _t("notes",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("content", sa.Text), sa.Column("tags", sa.String(300)),
    sa.Column("project", sa.String(80)), sa.Column("note_type", sa.String(40)),
    sa.Column("source", sa.String(40)), sa.Column("session_id", sa.String(80)),
    sa.Column("created_at", sa.DateTime))

people = _t("people",
    sa.Column("id", sa.String(80), primary_key=True), sa.Column("name", sa.String(200)),
    sa.Column("role", sa.String(120)), sa.Column("email", sa.String(200)),
    sa.Column("projects", sa.String(400)), sa.Column("notes", sa.Text),
    sa.Column("created_at", sa.DateTime))

time_entries = _t("time_entries",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("project_tag", sa.String(80)), sa.Column("org_tag", sa.String(80)),
    sa.Column("activity_type", sa.String(60)), sa.Column("description", sa.Text),
    sa.Column("started_at", sa.DateTime), sa.Column("duration_minutes", sa.Integer),
    sa.Column("source", sa.String(40)), sa.Column("created_at", sa.DateTime))

summaries_gist = _t("summaries_gist",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("period_label", sa.String(120)), sa.Column("period_start", sa.DateTime),
    sa.Column("period_end", sa.DateTime), sa.Column("body", sa.Text),
    sa.Column("confidence", sa.String(20)), sa.Column("created_at", sa.DateTime))

summaries_theme = _t("summaries_theme",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("title", sa.String(300)), sa.Column("body", sa.Text),
    sa.Column("confidence", sa.String(20)), sa.Column("created_at", sa.DateTime))

open_questions = _t("open_questions",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("question", sa.Text), sa.Column("body", sa.Text),
    sa.Column("confidence", sa.String(20)), sa.Column("status", sa.String(40)),
    sa.Column("created_at", sa.DateTime))

patterns = _t("patterns",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(200)), sa.Column("body", sa.Text),
    sa.Column("confidence", sa.String(20)), sa.Column("created_at", sa.DateTime))

temporal_narratives = _t("temporal_narratives",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("kind", sa.String(20)), sa.Column("period_label", sa.String(60)),
    sa.Column("period_start", sa.DateTime), sa.Column("period_end", sa.DateTime),
    sa.Column("narrative", sa.Text), sa.Column("created_at", sa.DateTime))

overseer_journal = _t("overseer_journal",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("body", sa.Text), sa.Column("created_at", sa.DateTime))

human_journal_entries = _t("human_journal_entries",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("text", sa.Text), sa.Column("entry_type", sa.String(40)),
    sa.Column("created_at", sa.DateTime))

ALL_TABLES = [projects, notes, people, time_entries, summaries_gist, summaries_theme,
              open_questions, patterns, temporal_narratives, overseer_journal,
              human_journal_entries]

# ── Fictional dataset ─────────────────────────────────────────────────

BASE = datetime.utcnow() - timedelta(days=90)
def D(days, h=9): return BASE + timedelta(days=days, hours=h)

PROJECTS = [
    ("demo-tidegauge", "TideGauge", "active", 1, "Open-source coastal water-level sensor: ESP32 + ultrasonic rangefinder logging tide height to a public dashboard.", "hardware", "demo-openshore", "https://example.com/robin/tidegauge", 41.5, "Mara Quinn, Dev Osei"),
    ("demo-mycelium-panels", "Mycelium Acoustic Panels", "active", 2, "Growing sound-dampening wall panels from mycelium and hemp hurd; measuring absorption coefficients across frequencies.", "research", "demo-quietlab", "", 28.0, "Lena Park"),
    ("demo-loomlang", "LoomLang", "paused", 3, "A tiny DSL that compiles weaving drafts into loom instructions; pattern preview renderer in the browser.", "software", "", "https://example.com/robin/loomlang", 16.5, ""),
    ("demo-solar-kiln", "Solar Kiln", "active", 2, "Passive solar wood-drying kiln with a firmware-controlled vent and moisture sensor array for the workshop.", "hardware", "demo-openshore", "", 33.0, "Dev Osei"),
    ("demo-harbor-notes", "Harbor Notes", "idea", 4, "A local-first note app for field research: offline capture, tags, and sync when back on wifi.", "software", "", "", 6.0, "Mara Quinn"),
]

PEOPLE = [
    ("demo-mara-quinn", "Mara Quinn", "collaborator", "mara@example.com", "demo-tidegauge,demo-harbor-notes", "Firmware + dashboard work on TideGauge; design sounding board."),
    ("demo-dev-osei", "Dev Osei", "collaborator", "dev@example.com", "demo-tidegauge,demo-solar-kiln", "Mechanical + enclosure design; runs the workshop."),
    ("demo-lena-park", "Lena Park", "mentor", "lena@example.com", "demo-mycelium-panels", "Materials scientist advising on mycelium substrate + acoustics testing."),
    ("demo-toby-reed", "Toby Reed", "client", "toby@example.com", "demo-solar-kiln", "Furniture maker who wants the kiln for air-drying hardwood."),
    ("demo-iris-fenn", "Iris Fenn", "friend", "iris@example.com", "", "Runs the local makerspace; helps with grant connections."),
]

# Gist fragments - each becomes one summaries_gist row, keyword-rich for search.
GISTS = [
    "Calibrated the TideGauge ultrasonic sensor against a tape-measured staff gauge; readings within 8mm after offset correction.",
    "Flashed new TideGauge firmware that median-filters the ultrasonic sensor to reject wave-chop noise before logging.",
    "Wired the TideGauge dashboard to push tide height every 5 minutes; added a 24-hour sparkline and a low-tide alert.",
    "TideGauge enclosure leaked at the cable gland during the first storm; redesigning with a drip loop and potted connector.",
    "Drafted a small-grant application for TideGauge to the coastal-resilience fund; need two letters of support by Friday.",
    "Mara fixed the dashboard timezone bug - tide timestamps were UTC but rendering as local without the offset.",
    "Measured acoustic absorption of the first mycelium panel batch; strong dampening above 1kHz, weak in the low end.",
    "Second mycelium substrate (hemp hurd + oyster mycelium) grew denser; sound absorption improved but dry time doubled.",
    "Lena suggested pressing the mycelium panels before the final colonization to raise density and absorption.",
    "Logged a failed mycelium batch - contamination from an unsterilized mold; switching to pressure-cooker sterilization.",
    "Solar Kiln vent servo now opens on a humidity threshold; firmware reads the moisture sensor array every minute.",
    "Solar Kiln hit 52C internal on a clear day; oak sample dropped from 28% to 19% moisture over a week.",
    "Added a second moisture sensor to the Solar Kiln to catch uneven drying between the top and bottom racks.",
    "Toby dropped off walnut slabs for the Solar Kiln; need to log starting moisture and stack with spacers.",
    "LoomLang now parses a twill draft and renders the pattern preview in the browser; export to loom instructions still rough.",
    "Refactored the LoomLang compiler to separate the draft parser from the renderer; tests pass for plain and twill weaves.",
    "Paused LoomLang to focus on TideGauge before the grant deadline; left notes on the threading-order bug.",
    "Sketched Harbor Notes data model: offline-first capture with tags and a sync queue that flushes on wifi.",
    "Harbor Notes prototype stores notes in local storage; need a conflict strategy before adding real sync.",
    "Workshop day with Dev: cut and potted the new TideGauge enclosure; the drip loop fix held under a hose test.",
    "Spent the morning on TideGauge sensor calibration math; documented the offset + temperature-compensation curve.",
    "Iris connected me with the makerspace grant committee; they want a one-page TideGauge summary and a budget.",
    "Tuned the Solar Kiln firmware PID-ish vent logic; less oscillation, holds humidity setpoint within a few percent.",
    "Mycelium panel absorption test rig built from a small reverb box and a calibrated mic; repeatable now.",
    "Wrote up the TideGauge bill of materials; ESP32, ultrasonic rangefinder, solar + LiPo, weatherproof box ~ $46.",
]

THEMES = [
    ("Building open hardware in public", "Robin consistently ships sensor projects (TideGauge, Solar Kiln) as open hardware with public dashboards and BOMs, inviting collaborators in early."),
    ("Calibration before features", "A recurring move: nail sensor calibration and noise rejection first (ultrasonic, moisture) before building dashboards or alerts."),
    ("Materials experiments with patient timelines", "The mycelium acoustic work trades fast iteration for slow biological timelines; success is measured in absorption curves, not commits."),
    ("Grants as forcing functions", "Deadlines (the coastal-resilience grant) reprioritize the whole project list - LoomLang got paused to push TideGauge."),
]

QUESTIONS = [
    ("How low can mycelium panels dampen before needing a denser substrate?", "Absorption is strong above 1kHz but weak in the low end across two batches; pressing helps density. Open question whether biology alone can reach low-frequency targets."),
    ("What's the right sync-conflict strategy for Harbor Notes?", "Offline-first capture is easy; the unresolved question is conflict resolution when the same note is edited on two devices before sync."),
    ("Is the TideGauge enclosure reliable enough for a winter deployment?", "First storm caused a cable-gland leak. The drip-loop + potted connector fix passed a hose test, but a real multi-week storm season is untested."),
]

PATTERNS = [
    ("Ships a calibration writeup with every sensor", "Each sensor project (TideGauge, Solar Kiln) gets a documented calibration/offset curve before moving on - a durable habit."),
    ("Pauses the soft project under deadline", "When a hard deadline appears, the 'fun' software project (LoomLang, Harbor Notes) gets paused in favor of the hardware with a real due date."),
    ("Pulls collaborators in at the enclosure/field stage", "Dev and Mara get looped in once a project hits physical build or field deployment, not during early prototyping."),
]

NARRATIVES = [
    ("weekly", "demo-W1", -84, -78, "[WORK]\nA TideGauge week: ultrasonic sensor calibration against a staff gauge, a firmware median-filter to kill wave-chop noise, and the first dashboard push every five minutes. The enclosure leaked in a storm - a drip-loop redesign is underway. [PERSONAL] Quiet otherwise."),
    ("weekly", "demo-W2", -77, -71, "[WORK]\nSplit between the mycelium acoustic panels (first absorption tests - strong above 1kHz, weak low end) and Solar Kiln firmware (humidity-triggered vent). Lena advised pressing the panels for density. [CONNECTIONS] Iris opened a door to the makerspace grant committee."),
    ("weekly", "demo-W3", -70, -64, "[WORK]\nGrant-deadline week. LoomLang got paused so TideGauge could ship a one-page summary, BOM (~$46), and budget for the coastal-resilience fund. Dev and Robin potted the new enclosure; the drip-loop fix held a hose test."),
    ("monthly", "demo-M1", -90, -60, "The month centered on TideGauge maturing from a noisy prototype to a calibrated, dashboarded coastal sensor, while the mycelium acoustic research advanced slowly on its biological clock. A grant deadline acted as a forcing function, pausing LoomLang. The Solar Kiln quietly hit its first successful oak-drying run (28% to 19% moisture)."),
]

JOURNAL = [
    "Refined the gist prompt after noticing TideGauge summaries were burying the calibration details under dashboard chatter.",
    "The mycelium project resists my usual fast-iteration instinct; logging it as a pattern - patience as a materials skill.",
    "Watched the grant deadline reorder everything. Worth surfacing to Robin that LoomLang keeps getting deprioritized.",
    "Calibration-writeup habit is now strong enough to call a pattern; filed it with TideGauge + Solar Kiln as evidence.",
    "Connected the enclosure-leak question to the winter-deployment risk; it's the open question most likely to bite.",
]

HUMAN_JOURNAL = [
    ("Storm tonight - anxious the TideGauge box leaks again. The drip loop should hold but I'll feel better after I see the logs.", "reflection"),
    ("Pressed the third mycelium batch today. Smells like the forest floor in the best way. Fingers crossed on density.", "reflection"),
    ("Grant submitted. One page, clean budget, two letters. Whatever happens, TideGauge is in better shape for it.", "reflection"),
]


def _purge(engine):
    insp = sa.inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as c:
        for t in reversed(ALL_TABLES):
            if t.name in existing:
                c.execute(sa.text(f"DROP TABLE {t.name}"))
    print(f"purged {len([t for t in ALL_TABLES if t.name in existing])} demo tables")


def _seed(engine):
    md.create_all(engine)  # create the demo tables if missing
    with engine.begin() as c:
        # clear any prior demo rows so re-runs don't duplicate
        c.execute(sa.delete(projects).where(projects.c.tag.like("demo-%")))
        c.execute(sa.delete(people).where(people.c.id.like("demo-%")))
        c.execute(sa.delete(notes).where(notes.c.source == "demo"))
        c.execute(sa.delete(time_entries).where(time_entries.c.source == "demo"))
        for t in (summaries_gist, summaries_theme, open_questions, patterns,
                  temporal_narratives, overseer_journal, human_journal_entries):
            c.execute(sa.delete(t))  # interpretive tables are demo-only

        for i, (tag, name, status, pri, desc, cat, org, gh, hrs, collab) in enumerate(PROJECTS):
            c.execute(sa.insert(projects).values(
                tag=tag, name=name, status=status, priority=pri, description=desc,
                category=cat, org_tag=org, github_url=gh, total_hours=hrs,
                collaborators=collab, last_touched=D(-3 - i), created_at=D(-85 + i)))
        for pid, nm, role, em, projs, nt in PEOPLE:
            c.execute(sa.insert(people).values(
                id=pid, name=nm, role=role, email=em, projects=projs, notes=nt,
                created_at=D(-80)))
        note_projects = ["demo-tidegauge", "demo-mycelium-panels", "demo-solar-kiln",
                         "demo-loomlang", "demo-harbor-notes"]
        for i, g in enumerate(GISTS):
            c.execute(sa.insert(notes).values(
                content=g, tags="demo", project=note_projects[i % len(note_projects)],
                note_type="note", source="demo", created_at=D(i)))
            c.execute(sa.insert(summaries_gist).values(
                period_label=f"demo:session-{i:03d}", period_start=D(i, 8),
                period_end=D(i, 11), body=g, confidence="med", created_at=D(i, 12)))
        for i in range(15):
            c.execute(sa.insert(time_entries).values(
                project_tag=note_projects[i % len(note_projects)], org_tag="",
                activity_type=["coding", "fabrication", "research", "design", "testing"][i % 5],
                description=f"Demo work session {i + 1}", started_at=D(i * 2, 10),
                duration_minutes=45 + (i % 5) * 30, source="demo", created_at=D(i * 2)))
        for title, body in THEMES:
            c.execute(sa.insert(summaries_theme).values(title=title, body=body, confidence="med", created_at=D(-40)))
        for q, body in QUESTIONS:
            c.execute(sa.insert(open_questions).values(question=q, body=body, confidence="high", status="open", created_at=D(-30)))
        for nm, body in PATTERNS:
            c.execute(sa.insert(patterns).values(name=nm, body=body, confidence="med", created_at=D(-25)))
        for kind, label, s, e, text in NARRATIVES:
            c.execute(sa.insert(temporal_narratives).values(
                kind=kind, period_label=label, period_start=D(s), period_end=D(e),
                narrative=text, created_at=D(e)))
        for i, body in enumerate(JOURNAL):
            c.execute(sa.insert(overseer_journal).values(body=body, created_at=D(-20 + i * 3)))
        for text, et in HUMAN_JOURNAL:
            c.execute(sa.insert(human_journal_entries).values(text=text, entry_type=et, created_at=D(-15)))
    print(f"seeded: {len(PROJECTS)} projects, {len(PEOPLE)} people, {len(GISTS)} notes+gists, "
          f"{len(THEMES)} themes, {len(QUESTIONS)} questions, {len(PATTERNS)} patterns, "
          f"{len(NARRATIVES)} narratives, {len(JOURNAL)} journal, {len(HUMAN_JOURNAL)} human-journal")


def main(argv):
    engine = db.engine()
    print("dialect:", engine.dialect.name)
    if "--purge" in argv:
        _purge(engine)
    else:
        _seed(engine)


if __name__ == "__main__":
    main(sys.argv[1:])
