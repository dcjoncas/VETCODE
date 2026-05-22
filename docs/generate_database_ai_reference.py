from __future__ import annotations

import html
import importlib.util
import os
import re
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUT = ROOT / "docs" / "VETCODE_DATABASE_AI_REFERENCE.html"

sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env")
except Exception:
    pass


RELATIONSHIPS = [
    ("person", "professional", "person.id", "professional.personid", "Candidate identity to professional record"),
    ("professional", "professionalprofile", "professional.id", "professionalprofile.professionalid", "Professional record to profile rows"),
    ("professionalprofile", "professionalskill", "professionalprofile.id", "professionalskill.profileid", "Profile to manually/AI extracted skills"),
    ("professionalprofile", "resumeskill", "professionalprofile.id", "resumeskill.profileid", "Profile to resume-extracted skills"),
    ("skill", "professionalskill", "skill.id", "professionalskill.skillid", "Shared skill catalog"),
    ("skill", "resumeskill", "skill.id", "resumeskill.skillid", "Resume skill catalog links"),
    ("professionalprofile", "professionalexperience", "professionalprofile.id", "professionalexperience.profileid", "Profile portfolio/work history rows"),
    ("professionalexperience", "portfolioskill", "professionalexperience.id", "portfolioskill.professionalexperienceid", "Portfolio row to role-specific skills"),
    ("jobdescription", "jobskills", "jobdescription.id", "jobskills.jobid", "Job description to required/preferred skills"),
    ("skill", "jobskills", "skill.id", "jobskills.skillid", "Skill catalog to JD skills"),
    ("jobdescription", "jobpersonalities", "jobdescription.id", "jobpersonalities.jobid", "JD to AI-scored personality fit"),
    ("person", "aichatlogs", "person.id", "aichatlogs.personid", "Candidate external chat link and transcript"),
    ("professionalprofile", "professionalsurvey", "professionalprofile.id", "professionalsurvey.profileid", "Profile to personality survey"),
    ("professionalsurvey", "professionalsurveyquestion", "professionalsurvey.id", "professionalsurveyquestion.professionalsurveyid", "Survey to question answers"),
    ("question", "personality", "question.personalityid", "personality.id", "Question to personality dimension"),
]

JSON_STORES = [
    ("backend/data/onboarding_records.json", "Onboarding packets and public onboarding links", "Created from completed profiles; used by HR onboarding and accounting resource linkage."),
    ("backend/data/time_entries.json", "Weekly candidate/staff time entries", "Feeds Time Admin, approved time, payroll/accounting, and invoices."),
    ("backend/data/accounting_records.json", "Resource financials, invoices, expenses", "Stores bill rate, cost rate, invoice status, line items, P&L inputs, and balance sheet inputs."),
    ("backend/data/crm_records.json", "CRM customer/team/deal records", "Source for customer dropdowns, team cards, invoice customers, news scan, and sales portal."),
    ("backend/data/meeting_records.json", "Saved meeting outputs", "Meet page archive, transcript summaries, Ask This Meeting, and CRM handoff."),
    ("backend/data/workflow_events.json", "Process-flow activity", "Tracks candidate movement through talent, match, review, shortlist, interview, status."),
    ("backend/data/profile_badges.json", "Badges and certifications", "AI certification and test challenge evidence attached to profiles."),
    ("backend/data/interview_archive.json", "Interview records", "Candidate/client interview scheduling records and status."),
    ("backend/data/engineering_survey_answers.json", "Engineering-domain personality answers", "Engineer survey fallback store used by BuildReady profiles."),
]

AI_MODULES = [
    ("backend/openAI/pageAgents.py", "ask_page_agent(agent_key, message, context)", "POST /api/agents/ask; frontend widget in backend/ui/pages/JS/pageAgents.js", "OPENAI_AGENT_MODEL or gpt-4o-mini", "Page-aware Numa guidance and controlled action planning.", "Selects active prompt, summarizes page context, applies safety policy, calls OpenAI, redacts sensitive values, and optionally plans controlled actions."),
    ("backend/openAI/pageAgents.py", "_plan_actions(client, agent_key, message, context, agent)", "Called inside ask_page_agent", "OPENAI_AGENT_MODEL or gpt-4o-mini, JSON response", "Converts clear user intent into structured actions.", "Only runs for create/save/update/schedule intent. Filters by access policy. Returns max two safe actions with missing fields and payloads."),
    ("backend/resume_ai_profile.py", "normalize_ai_resume_profile(raw_text, domain)", "/api/azure/resume/upload; profile creation from resume", "OPENAI_MODEL or gpt-4o-mini, JSON response", "Extracts contact, headline, summary, skills, culture, and portfolio/work history from resume text.", "Requires OPENAI_API_KEY. Sends strict JSON shape. Cleans markdown JSON, clamps years, deduplicates skills, and preserves separate jobs as portfolio rows."),
    ("backend/openAI/candidateChat.py", "askQuestion(), saveProgress(), openEndedQuestion()", "/api/chat routes; external candidate chat page", "gpt-3.5-turbo for open-ended chat; deterministic survey flow", "Runs candidate personality/profile-completion chat and saves 1-5 survey answers.", "Loads domain questions, parses answer 1-5, saves to Postgres or engineering JSON fallback, and deterministically asks the next question."),
    ("backend/openAI/engineeringSurvey.py", "get_questions(), save_answer(), profile_personality()", "Engineering/BuildReady candidate chat and profile rendering", "No live model call", "Engineering-specific personality survey.", "Saves answers into backend/data/engineering_survey_answers.json and converts section averages to percentage personality rows."),
    ("backend/openAI/jobProcessing.py", "processPersonalities(jobId, jobDescription, azureCursor)", "Job normalization/upload path", "gpt-3.5-turbo", "Scores personality traits against a job description.", "Loops every personality row, asks for a 1-5 importance score, clamps malformed values, inserts into jobpersonalities."),
    ("backend/openAI/externalPeopleSearch.py", "getPeopleSkills(), getPeopleCity(), getPeopleState(), getPeopleCountry()", "External candidate search prep", "gpt-3.5-turbo", "Extracts search criteria from JDs for outside sourcing.", "Asks for comma-separated required skills or one location value, then strips no-city/no-state/no-country fallbacks."),
    ("backend/openAI/candidateProcessing.py", "processGeneral(), processSkillYears(), candidateDescription(), candidateCulturalExperience()", "Legacy resume enrichment helpers", "gpt-3.5-turbo", "Extracts targeted resume facts and hiring-manager summaries.", "Single-purpose prompts return raw data only, then values are clamped and converted into app shape."),
    ("backend/openAI/emailProcessing.py", "shortlistClientEmail(jobId, candidates)", "POST /api/ai/clientEmail/shortlist", "gpt-5.4-mini", "Drafts a client-facing shortlist email.", "Loads job by ID, includes candidate names and scores, and returns only the email body."),
    ("backend/calendar_router.py", "AI interview draft prompt block", "Calendar/interview scheduling endpoints", "OPENAI_MODEL or gpt-5 via Responses API JSON schema", "Drafts interview title, subject, body, attendees, duration, and logistics.", "Builds prompt from candidate, role, company, interview type, talking points, and chat/profile context, then validates strict JSON schema output."),
    ("backend/main.py", "_summarize_customer_news(customer, location, items)", "POST /api/crm/news-scan", "OPENAI_AGENT_MODEL or gpt-4o-mini", "Summarizes customer news and recommends CRM follow-up.", "Pulls Google News RSS, passes snippets to model, expects JSON headline/signals/recommended_action/risk_level, and falls back when unavailable."),
]

PROMPT_SNIPPETS = [
    ("Resume extraction system prompt", "backend/resume_ai_profile.py", "Strict JSON profile shape with contact, headline, summary, skills, culture, portfolio, and confidence. Rules preserve separate jobs and avoid overstating skill years."),
    ("Numa answer wrapper", "backend/openAI/pageAgents.py", "Direct app-specific guidance from page context. Never claim a record was changed until an action result confirms it."),
    ("Numa safety policy", "backend/openAI/pageAgents.py", "Do not harm code, write code changes, delete data, overwrite names, or mutate the database by itself. Redact sensitive finance/deal/rate data unless admin access is unlocked."),
    ("Numa action schema", "backend/openAI/pageAgents.py", "Strict JSON actions: create_profile, update_profile_core, schedule_interview_setup, or create_job_description with label, summary, missing_fields, and payload."),
    ("Candidate survey prompt", "backend/openAI/candidateChat.py", "Question N asks for a 1-5 agreement score with optional explanation."),
    ("JD personality scoring", "backend/openAI/jobProcessing.py", "Return only a number 1-5 for how beneficial a personality trait is to the JD."),
    ("Client shortlist email", "backend/openAI/emailProcessing.py", "Generate an email body for hiring managers and make all candidates shine without extra wrapper text."),
    ("Calendar/interview draft", "backend/calendar_router.py", "Return strict JSON for title, interview type, role, company, talking points, attendees, duration, location, subject, and body."),
    ("CRM news scan", "backend/main.py", "Read recent customer news results and return JSON headline, signals, recommended action, and risk level."),
]

FRONTEND_AGENT_NOTES = [
    ("backend/ui/pages/JS/pageAgents.js", "Frontend agent registry, active page mapping, context collection, widget rendering, action cards, and calls to /api/agents/ask and /api/agents/action."),
    ("backend/ui/pages/agents.html", "Admin/password-protected agent management page where page agents can be enabled/disabled and prompts can be edited."),
    ("backend/main.py", "Backend receives the page-agent request, checks admin unlock/change mode, calls pageAgents.ask_page_agent, and executes controlled actions."),
]


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def read_file(path: str) -> str:
    try:
        return (ROOT / path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def try_load_agents() -> dict:
    try:
        spec = importlib.util.spec_from_file_location("pageAgents", BACKEND / "openAI" / "pageAgents.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module.AGENTS
    except Exception:
        return {}


def try_query_schema():
    try:
        import psycopg

        host = os.getenv("AZURE_DATABASE_HOST")
        db = os.getenv("AZURE_DATABASE_NAME")
        user = os.getenv("AZURE_DATABASE_USER")
        password = os.getenv("AZURE_DATABASE_PASSWORD")
        port = int(os.getenv("AZURE_DATABASE_PORT", "5432"))
        if not all([host, db, user, password]):
            return None, [], [], "Azure database env vars were not complete when this doc was generated."
        with psycopg.connect(host=host, dbname=db, user=user, password=password, port=port, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name, column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                    """
                )
                rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT tc.table_name, kcu.column_name, ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
                    ORDER BY tc.table_name, kcu.column_name
                    """
                )
                fks = cur.fetchall()
                cur.execute(
                    """
                    SELECT tc.table_name, kcu.column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public'
                    ORDER BY tc.table_name, kcu.ordinal_position
                    """
                )
                pks = cur.fetchall()
        tables: dict[str, list[dict]] = {}
        for table, col, dtype, nullable, default in rows:
            tables.setdefault(table, []).append({"name": col, "type": dtype, "nullable": nullable, "default": bool(default)})
        return tables, fks, pks, "Schema was read from the configured Azure PostgreSQL development database."
    except Exception as exc:
        return None, [], [], f"Schema query failed; using documented core schema groups. Error: {exc}"


def schema_table_html(tables, pks) -> str:
    if not tables:
        return "<p>Live schema was unavailable. See the relationship map and local SQLite schema sections.</p>"
    pk_map: dict[str, set[str]] = {}
    for table, col in pks:
        pk_map.setdefault(table, set()).add(col)
    sections = []
    for table in sorted(tables):
        col_rows = []
        for col in tables[table]:
            flags = []
            if col["name"] in pk_map.get(table, set()):
                flags.append("PK")
            if col["nullable"] == "NO":
                flags.append("required")
            if col["default"]:
                flags.append("default")
            col_rows.append(f"<tr><td><code>{esc(col['name'])}</code></td><td>{esc(col['type'])}</td><td>{esc(', '.join(flags))}</td></tr>")
        sections.append(
            f"<details class='schema-detail'><summary><strong>{esc(table)}</strong><span>{len(tables[table])} columns</span></summary>"
            f"<table><thead><tr><th>Column</th><th>Type</th><th>Flags</th></tr></thead><tbody>{''.join(col_rows)}</tbody></table></details>"
        )
    return "".join(sections)


def fk_table_html(fks) -> str:
    if fks:
        rows = "".join(
            f"<tr><td>{esc(table)}</td><td><code>{esc(col)}</code></td><td>{esc(ref_table)}</td><td><code>{esc(ref_col)}</code></td></tr>"
            for table, col, ref_table, ref_col in fks
        )
        return f"<table><thead><tr><th>Table</th><th>Column</th><th>References</th><th>Reference column</th></tr></thead><tbody>{rows}</tbody></table>"
    rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td><td>{esc(d)}</td><td>{esc(e)}</td></tr>" for a, b, c, d, e in RELATIONSHIPS)
    return f"<table><thead><tr><th>From table</th><th>To table</th><th>From key</th><th>To key</th><th>Purpose</th></tr></thead><tbody>{rows}</tbody></table>"


def local_sqlite_schema() -> str:
    text = read_file("backend/storage.py")
    snippets = re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\)", text, flags=re.S | re.I)
    if not snippets:
        return "<p>Local SQLite schema is defined in <code>backend/storage.py</code>.</p>"
    rows = []
    for table, body in snippets:
        clean = re.sub(r"\s+", " ", body).strip()
        rows.append(f"<tr><td><code>{esc(table)}</code></td><td><code>{esc(clean)}</code></td></tr>")
    return f"<table><thead><tr><th>SQLite table</th><th>Definition</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def diagram_html() -> str:
    groups = [
        ("Candidate Identity", ["person", "professional", "professionalprofile"], "profile"),
        ("Evidence", ["professionalexperience", "professionalskill", "resumeskill", "skill"], "evidence"),
        ("Jobs", ["jobdescription", "jobskills", "jobpersonalities"], "jobs"),
        ("Chat & Survey", ["aichatlogs", "professionalsurvey", "professionalsurveyquestion", "personality"], "chat"),
        ("Workflow", ["platformactivity", "platformactivityoverride", "workflow_events.json"], "workflow"),
        ("Operations", ["onboarding_records.json", "time_entries.json", "accounting_records.json"], "ops"),
        ("CRM & Meetings", ["crm_records.json", "meeting_records.json", "interview_archive.json"], "crm"),
    ]
    cards = []
    for title, names, cls in groups:
        chips = "".join(f"<code>{esc(name)}</code>" for name in names)
        cards.append(f"<section class='schema-node {cls}'><h3>{esc(title)}</h3><div>{chips}</div></section>")
    arrows = """
      <div class='flow-arrow'>Candidate profile feeds resume evidence, matching, chat, onboarding, time, accounting, CRM, and reports.</div>
      <div class='flow-arrow'>Job descriptions feed matching, shortlist, candidate review, client communication, interviews, and status.</div>
      <div class='flow-arrow'>Approved time plus resource rates create invoices; CRM customers anchor accounting and reports.</div>
    """
    return f"<div class='schema-map'>{''.join(cards)}</div>{arrows}"


def table(rows, headers) -> str:
    head = "".join(f"<th>{esc(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def ai_module_html() -> str:
    rows = []
    for module, entry, used_by, model, purpose, logic in AI_MODULES:
        rows.append([f"<code>{esc(module)}</code><br><strong>{esc(entry)}</strong>", esc(used_by), esc(model), esc(purpose), esc(logic)])
    return table(rows, ["Code / Entry", "Used By", "Model", "Purpose", "Internal Logic"])


def prompt_snippets_html() -> str:
    rows = [[esc(name), f"<code>{esc(path)}</code>", esc(summary)] for name, path, summary in PROMPT_SNIPPETS]
    return table(rows, ["Prompt", "Location", "Structure / Rule"])


def json_stores_html() -> str:
    rows = [[f"<code>{esc(path)}</code>", esc(purpose), esc(notes)] for path, purpose, notes in JSON_STORES]
    return table(rows, ["Store", "Purpose", "How It Connects"])


def frontend_agent_html() -> str:
    rows = [[f"<code>{esc(path)}</code>", esc(notes)] for path, notes in FRONTEND_AGENT_NOTES]
    return table(rows, ["File", "Role"])


def agent_prompt_html(agents: dict) -> str:
    rows = []
    for key, agent in agents.items():
        prompt = agent.get("prompt", "")
        rows.append(
            "<details class='agent-detail'>"
            f"<summary><strong>{esc(agent.get('name', 'Numa'))}</strong><span>{esc(key)} - {esc(agent.get('page'))}</span></summary>"
            f"<div class='agent-meta'><span>Color: <code>{esc(agent.get('color'))}</code></span><span>Prompt length: {len(prompt)} chars</span></div>"
            f"<pre><code>{esc(prompt)}</code></pre></details>"
        )
    return "".join(rows) or "<p>No backend agent prompts were loaded.</p>"


def extract_routes() -> str:
    text = read_file("backend/main.py")
    rows = []
    for m in re.finditer(r"@app\.(get|post|put|delete)\(\s*['\"]([^'\"]+)", text):
        method, path = m.group(1).upper(), m.group(2)
        if any(token in path for token in ["agents", "crm", "accounting", "invoice", "time", "onboarding", "meet", "profile", "jd", "match", "resume"]):
            rows.append([f"<code>{esc(method)}</code>", f"<code>{esc(path)}</code>"])
    return table(rows[:140], ["Method", "Path"]) if rows else "<p>No routes extracted.</p>"


def build() -> str:
    agents = try_load_agents()
    tables, fks, pks, schema_note = try_query_schema()
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    table_count = len(tables) if tables else "Core"
    css = """
:root{--bg:#f4f7f6;--paper:#fff;--ink:#102018;--muted:#5b6b62;--line:#dbe7df;--green:#2f7d4b;--blue:#145db2;--gold:#b88727;--code:#eef4f0}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,Helvetica,sans-serif;line-height:1.5}.shell{max-width:1320px;margin:0 auto;padding:28px 18px 70px}.hero{background:linear-gradient(135deg,#fff,#eff8f0 55%,#e8f0ff);border:1px solid var(--line);border-radius:14px;padding:28px;box-shadow:0 18px 50px rgba(16,32,24,.08)}h1{margin:0 0 6px;font-size:36px;line-height:1.1}h2{margin:32px 0 12px;padding-top:18px;border-top:1px solid var(--line);font-size:24px}h3{margin:0 0 10px;font-size:17px}p{margin:0 0 13px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}.metric{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px}.metric strong{display:block;font-size:26px;color:var(--green)}.toc{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;margin-top:18px}.toc a{display:block;border:1px solid var(--line);border-radius:999px;background:#fff;padding:9px 12px;text-decoration:none;color:var(--green);font-weight:800;font-size:13px}.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:18px;margin-top:14px;box-shadow:0 10px 28px rgba(16,32,24,.05)}table{width:100%;border-collapse:collapse;margin:12px 0 18px;font-size:13px;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top}th{background:#edf7ef;color:#183c24}code{font-family:Consolas,'Courier New',monospace;background:var(--code);border:1px solid #d8e4dc;border-radius:5px;padding:1px 5px;font-size:.92em}pre{background:#0f1d16;color:#f3fff6;border-radius:9px;padding:14px;overflow:auto;font-size:12px;line-height:1.45}pre code{background:transparent;border:0;color:inherit;padding:0}.schema-map{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}.schema-node{min-height:155px;border:1px solid var(--line);border-radius:13px;padding:14px;background:#fff}.schema-node.profile{border-color:#b7dfc0;background:linear-gradient(180deg,#fff,#effaf1)}.schema-node.evidence{border-color:#c8d9f5;background:linear-gradient(180deg,#fff,#eef5ff)}.schema-node.jobs{border-color:#f5d6aa;background:linear-gradient(180deg,#fff,#fff6e9)}.schema-node.chat{border-color:#dfc7f8;background:linear-gradient(180deg,#fff,#f7efff)}.schema-node.workflow{border-color:#bdd8ef;background:linear-gradient(180deg,#fff,#eef8ff)}.schema-node.ops{border-color:#ded7bd;background:linear-gradient(180deg,#fff,#faf7ea)}.schema-node.crm{border-color:#d8c2ee;background:linear-gradient(180deg,#fff,#f5f0ff)}.schema-node code{display:inline-block;margin:3px}.flow-arrow{margin-top:8px;border-left:4px solid var(--green);background:#fff;border-radius:8px;padding:10px 12px}.schema-detail,.agent-detail{border:1px solid var(--line);border-radius:10px;background:#fff;margin:9px 0;overflow:hidden}.schema-detail summary,.agent-detail summary{display:flex;justify-content:space-between;gap:12px;cursor:pointer;padding:11px 13px;background:#f8fbf8}.schema-detail summary span,.agent-detail summary span{color:var(--muted);font-size:12px}.agent-meta{display:flex;gap:12px;flex-wrap:wrap;padding:10px 13px;color:var(--muted);font-size:12px}.callout{border-left:5px solid var(--blue);background:#edf5ff;border-radius:8px;padding:12px;margin:12px 0}.warn{border-left-color:var(--gold);background:#fff8e8}.agent-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.persona{padding:16px;border-radius:14px;border:1px solid var(--line);background:#fff}.persona.numa{box-shadow:inset 0 0 0 2px rgba(47,125,75,.08)}.persona.egeria{background:linear-gradient(135deg,#fff,#f8f4e8 50%,#edf3ff);box-shadow:inset 0 0 0 2px rgba(184,135,39,.12)}.badge{display:inline-block;border-radius:999px;padding:4px 9px;font-weight:800;font-size:12px;background:#edf7ef;color:var(--green);border:1px solid #cde4d1}.badge.blue{background:#edf5ff;color:var(--blue);border-color:#c8d9f5}.badge.gold{background:#fff7e3;color:#8a620e;border-color:#efd48d}@media(max-width:850px){.grid,.agent-pair{grid-template-columns:1fr}.hero{padding:20px}h1{font-size:30px}}
"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VETCODE Database Schema and AI Reference</title>
  <style>{css}</style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <span class="badge">VETCODE Technical Reference</span>
      <h1>Database Schema and AI Reference</h1>
      <p class="muted">Generated {esc(updated)}. This document maps the development database, JSON operational stores, AI modules, prompt structures, and page-agent behavior for Numa and Egeria.</p>
      <div class="grid">
        <div class="metric"><strong>{esc(table_count)}</strong><span>PostgreSQL tables documented</span></div>
        <div class="metric"><strong>{len(AI_MODULES)}</strong><span>AI code paths documented</span></div>
        <div class="metric"><strong>{len(agents)}</strong><span>Backend Numa page prompts loaded</span></div>
      </div>
      <nav class="toc">
        <a href="#schema-diagram">Schema Diagram</a>
        <a href="#schema-tables">Schema Tables</a>
        <a href="#json-stores">JSON Stores</a>
        <a href="#ai-inventory">AI Code Inventory</a>
        <a href="#numa-egeria">Numa and Egeria</a>
        <a href="#agent-prompts">Agent Prompts</a>
        <a href="#routes">Important API Routes</a>
      </nav>
    </section>
    <section id="schema-diagram" class="card"><h2>Database Schema Diagram</h2><p>{esc(schema_note)}</p>{diagram_html()}</section>
    <section class="card"><h2>Relationship Map</h2><p>Key joins used when moving from candidate intake to profile, job matching, chat, onboarding, time, accounting, CRM, and reports.</p>{fk_table_html(fks)}</section>
    <section id="schema-tables" class="card"><h2>PostgreSQL Schema Tables</h2><p>The relational source of truth for profiles, jobs, skills, surveys, public profiles, platform activity, and reference data lives in Azure PostgreSQL through <code>backend/azureUtils/storage/client.py</code>.</p>{schema_table_html(tables, pks)}</section>
    <section class="card"><h2>Local SQLite Fallback Schema</h2><p>The local fallback database is used for simpler profile/JD/match flows when Azure is unavailable or for local-only data paths.</p>{local_sqlite_schema()}</section>
    <section id="json-stores" class="card"><h2>JSON Operational Stores</h2><p>Newer workflow areas are currently stored in JSON files under <code>backend/data</code>. These files are domain-filtered in app logic and often seed demo data when empty.</p>{json_stores_html()}</section>
    <section id="ai-inventory" class="card"><h2>AI Code Inventory</h2><p>Specific AI modules, how they are reached, which models they use, and the internal logic applied before or after model calls.</p>{ai_module_html()}</section>
    <section class="card"><h2>Prompt Structures</h2><p>Prompting is split by task: resume extraction uses strict JSON, page agents use role prompt plus safety policy plus page context, and action planning uses a separate strict JSON action schema.</p>{prompt_snippets_html()}<div class="callout warn"><strong>Secret safety:</strong> This document names environment variables but does not include secret values, API keys, passwords, OAuth secrets, or database credentials.</div></section>
    <section id="numa-egeria" class="card">
      <h2>Numa and Egeria</h2>
      <div class="agent-pair">
        <article class="persona numa"><span class="badge blue">Implemented</span><h3>Numa</h3><p>Numa is the main page-aware assistant framework. In the code, every built-in page agent is currently named Numa or Sales Numa. Numa changes focus based on the active page: Talent, Find Candidates In, Find Candidates Out, Profiles, Job Descriptions, CRM, Meet, Interviews, Client Communication, Time, Accounting, Invoices, Test Challenge, AI Certification, Badge Catalog, and Admin.</p><p><strong>Frontend:</strong> <code>backend/ui/pages/JS/pageAgents.js</code> mounts the floating Ask Numa button, gathers page context, stores recent chat memory, and sends requests to the backend.</p><p><strong>Backend:</strong> <code>backend/openAI/pageAgents.py</code> stores page-specific prompts, safety policy, redaction logic, and controlled action planner.</p><p><strong>Policy:</strong> Numa guides first. It cannot claim data changed until an app action confirms it. Sensitive financial/deal/rate data is redacted unless admin/super-user access is unlocked.</p></article>
        <article class="persona egeria"><span class="badge gold">Implemented for FastBoard</span><h3>Egeria</h3><p>Egeria is the guided process helper. The FastBoard Candidate Launch in <code>backend/ui/pages/components/processFlow.html</code> presents Egeria as the assistant that asks, drafts, confirms, creates, ranks, selects, and prepares scheduling.</p><p><strong>Current split:</strong> Egeria has her own frontend registry entry, backend agent key, and system prompt. The FastBoard workflow calls <code>agent_key=egeria</code> for JD drafting/action planning, then uses deterministic app steps to save the JD, rank candidates, seed shortlist/status, and offer rollback.</p><p><strong>Next implementation target:</strong> expand Egeria beyond FastBoard into a full cross-page process governor with a gold/blue/silver visual identity, durable server-side workflow runs, and database-level transaction logging.</p></article>
      </div>
      <h3>Frontend Agent Files</h3>{frontend_agent_html()}
    </section>
    <section id="agent-prompts" class="card"><h2>Backend Numa Agent Prompt Registry</h2><p>These prompts are loaded from <code>backend/openAI/pageAgents.py</code>. Prompt edits from the Agents page can override active frontend prompt context sent to the backend.</p>{agent_prompt_html(agents)}</section>
    <section id="routes" class="card"><h2>Important API Routes</h2><p>Selected routes related to profiles, jobs, matching, agents, CRM, onboarding, time, accounting, invoices, and meetings.</p>{extract_routes()}</section>
    <section class="card"><h2>Developer Notes</h2><ul><li>Domain isolation depends on preserving <code>domain</code> through URLs, session storage, API calls, SQL filters, and JSON store filters.</li><li>Profile/job data is mostly PostgreSQL. CRM, accounting, time, onboarding, meetings, workflow, and badges are mostly JSON operational stores.</li><li>Main AI risk areas are resume extraction completeness, action planner overreach, external sourcing uncertainty, and sensitive financial/deal data exposure.</li><li>When adding AI features, keep deterministic fallback paths and clear next-step messaging when the model or API key is unavailable.</li><li>Egeria must stay registered in both frontend and backend agent registries so UI label, prompt, icon, and backend behavior match.</li></ul></section>
  </main>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(OUT)
