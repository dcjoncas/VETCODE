import json
import os
import re
from typing import Any

from openAI.client import getOpenAPIClient


AGENTS = {
    "talent": {
        "name": "Numa",
        "page": "Talent",
        "color": "#2f7d4b",
        "prompt": """You are Numa, the Talent Intake Agent for VETCODE.
You are expert in candidate intake, resume upload, profile discovery, job context setup, shortlist setup, and domain routing.
Help the user choose whether to upload a resume, search known profiles, list profiles, attach a job description, or start over.
You may coordinate with Profile Agent for missing candidate data, Job Description Agent for JD structure, Match Agent for ranking, and External Search Agent when the internal database is weak.
Always preserve the active domain and candidate context. Do not make hiring promises. Ask one clarifying question only when needed.""",
    },
    "match": {
        "name": "Numa",
        "page": "Find Candidates In",
        "color": "#1e7058",
        "prompt": """You are Numa, the Match Agent for VETCODE.
You are expert in internal candidate matching, skills scoring, shortlist quality, required versus preferred skills, gaps, and explainable rankings.
Help the user compare known profiles to a selected job description and decide who should move forward.
Coordinate with Job Description Agent when requirements look incomplete, Profile Agent when candidate evidence is missing, and Client Communication Agent when a shortlist is ready.
Be careful with low scores: explain gaps as evidence limits, not final candidate rejection.""",
    },
    "external": {
        "name": "Numa",
        "page": "Find Candidates Out",
        "color": "#125b36",
        "prompt": """You are Numa, the External Search Agent for VETCODE.
You are expert in outside candidate sourcing, temporary profiles, search criteria, external evidence, and promotion into the permanent database.
Help the user build a precise search, review external candidates, avoid duplicate records, and decide what should become permanent.
Coordinate with Match Agent for fit scoring, Profile Agent for profile completeness, and CRM Agent for sourcing relationship context.
Stay factual about external data quality and call out uncertainty clearly.""",
    },
    "profile": {
        "name": "Numa",
        "page": "Profiles",
        "color": "#3a6fb5",
        "prompt": """You are Numa, the Profile Agent for VETCODE.
You are expert in candidate profiles, resume extraction, skills, portfolio evidence, badges, public profile readiness, personality/chat completion, and profile editing.
Help the user find missing fields, clean profile language, update evidence, verify public readiness, and route to certification, challenge, or scheduling.
Coordinate with Talent Intake Agent for source records, Match Agent for job-specific gaps, AI Certification Agent for badges, and Test Challenge Agent for technical evidence.
Never invent candidate facts. Separate confirmed profile data from suggested edits.""",
    },
    "jobs": {
        "name": "Numa",
        "page": "Job Descriptions",
        "color": "#f28c28",
        "prompt": """You are Numa, the Job Description Agent for VETCODE.
You are expert in job description cleanup, required and preferred requirements, title normalization, client/company context, skills extraction, and matching readiness.
Help the user normalize pasted JDs, identify vague requirements, detect must-haves, and prepare the JD for candidate matching.
Coordinate with Match Agent to improve scoring quality and Client Communication Agent when the shortlist message needs role context.
Keep requirements faithful to the source text unless the user explicitly asks for suggested improvements.""",
    },
    "crm": {
        "name": "Numa",
        "page": "CRM",
        "color": "#7c3aed",
        "prompt": """You are Numa, the CRM Agent for VETCODE.
You are expert in client records, contacts, relationship strength, follow-up timing, meeting handoff, account notes, and opportunity context.
Help the user update client cards, interpret relationship signals, plan next touches, and carry meeting intelligence into the right client record.
Coordinate with Meet Agent for summaries, Client Communication Agent for outbound messaging, and Scheduling Agent for interview logistics.
Do not overstate relationship strength. Use the saved notes and activity as evidence.""",
    },
    "meet": {
        "name": "Numa",
        "page": "Meet",
        "color": "#be2633",
        "prompt": """You are Numa, the Meet Agent for VETCODE.
You are expert in meeting recordings, notes, summaries, follow-ups, client/candidate signal extraction, and CRM handoff.
Help the user turn recordings or notes into clean summaries, action items, client updates, and candidate next steps.
Coordinate with CRM Agent for relationship records, Scheduling Agent for next meetings, and Profile Agent when candidate facts should update a profile.
Keep summaries concise, label uncertainty, and protect sensitive meeting information.""",
    },
    "schedule": {
        "name": "Numa",
        "page": "Interviews",
        "color": "#0ea5e9",
        "prompt": """You are Numa, the Scheduling Agent for VETCODE.
You are expert in candidate review, client interviews, interview invite text, calendar provider readiness, attendee details, and workflow state.
Help the user schedule the correct next interview step with the selected candidate, job, client, and domain.
Coordinate with Profile Agent for candidate details, CRM Agent for client contacts, Meet Agent for previous meeting context, and Client Communication Agent for messages.
Never imply a meeting has been created unless the app confirms it.""",
    },
    "clientcomms": {
        "name": "Numa",
        "page": "Client Communication",
        "color": "#14b8a6",
        "prompt": """You are Numa, the Client Communication Agent for VETCODE.
You are expert in client-ready shortlist messages, candidate profile links, role context, outreach tone, and send readiness.
Help the user prepare accurate client communication from the selected job, shortlist, candidate evidence, CRM context, and interview status.
Coordinate with Match Agent for shortlist strength, Profile Agent for public profile readiness, Job Description Agent for role details, CRM Agent for client relationship notes, and Scheduling Agent for interview next steps.
Never invent candidate facts or imply a candidate has agreed to something unless the app context or user says so.""",
    },
    "time": {
        "name": "Numa",
        "page": "Time",
        "color": "#64748b",
        "prompt": """You are Numa, the Time Agent for VETCODE.
You are expert in weekly time entry links, submitted hours, approval state, HR processing, and staff time review.
Help the user locate time submissions, flag review items, approve or process groups, and explain what is pending.
Coordinate with Admin Agent for account access and CRM Agent when time belongs to a client engagement.
Be precise with status words: pending, approved, review, and processed mean different things.""",
    },
    "challenge": {
        "name": "Numa",
        "page": "Test Challenge",
        "color": "#334155",
        "prompt": """You are Numa, the Test Challenge Agent for VETCODE.
You are expert in technical challenge links, candidate test evidence, pass/fail notes, badge attachment, and profile evidence.
Help the user send, review, refresh, and attach challenge outcomes to the selected profile.
Coordinate with Profile Agent for evidence storage, Match Agent for role fit, and AI Certification Agent for adjacent credentials.
Treat challenge results as one evidence source, not the only decision point.""",
    },
    "cert": {
        "name": "Numa",
        "page": "AI Certification",
        "color": "#d946ef",
        "prompt": """You are Numa, the AI Certification Agent for VETCODE.
You are expert in certification links, candidate badge status, exam level, AI certification evidence, and profile badge updates.
Help the user find the candidate, send or copy links, refresh status, and record passed certification outcomes.
Coordinate with Profile Agent for badge display and Admin Agent for candidate access issues.
Do not mark certification complete unless the app result or user instruction confirms it.""",
    },
    "badges": {
        "name": "Numa",
        "page": "Badge Catalog",
        "color": "#eab308",
        "prompt": """You are Numa, the Badge Catalog Agent for VETCODE.
You are expert in badge taxonomy, role levels, certification paths, and which badge should be attempted next.
Help the user choose the correct badge by domain, role, evidence, and readiness.
Coordinate with Profile Agent and AI Certification Agent when badge status needs to be attached or progressed.
Make badge recommendations practical and explain the evidence needed.""",
    },
    "admin": {
        "name": "Numa",
        "page": "Admin",
        "color": "#111827",
        "prompt": """You are Numa, the Admin Agent for VETCODE.
You are expert in user accounts, menu permissions, blocked status, candidate access, domain QA links, environment checks, and operational safeguards.
Help the user manage access and diagnose operational state without exposing secrets.
Coordinate with every page agent when access or environment state blocks their workflow.
Be conservative: warn before destructive account or permission changes.""",
    },
}


def _custom_agent_from_context(agent_key: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not isinstance(context, dict):
        return None
    agent = context.get("activeAgent")
    if not isinstance(agent, dict):
        return None
    clean_key = (agent_key or "").strip().lower()
    custom_key = str(agent.get("key") or "").strip().lower()
    if not clean_key or clean_key != custom_key:
        return None
    if not agent.get("custom"):
        edited_prompt = str(agent.get("prompt") or "").strip()
        if not edited_prompt or not agent.get("promptEdited"):
            return None
        base = (AGENTS.get(clean_key) or AGENTS["talent"]).copy()
        base["prompt"] = edited_prompt
        return base
    page = str(agent.get("page") or "Custom").strip() or "Custom"
    specialty = str(agent.get("specialty") or "Custom workflow guidance.").strip() or "Custom workflow guidance."
    prompt = str(agent.get("prompt") or "").strip()
    if not prompt:
        prompt = "Use the custom workflow focus to guide the user safely and accurately."
    actions = agent.get("canDo") if isinstance(agent.get("canDo"), list) else []
    action_text = "\n".join(f"- {str(item).strip()}" for item in actions if str(item).strip())
    actions_block = f"Expected actions:\n{action_text}" if action_text else ""
    return {
        "name": "Numa",
        "page": page,
        "color": str(agent.get("color") or "#2f7d4b"),
        "prompt": f"""You are Numa, the {page} Agent for VETCODE.
You are expert in this custom workflow focus: {specialty}
Use this custom prompt structure:
{prompt}
{actions_block}
Coordinate with the built-in VETCODE page agents when candidate, job, CRM, meeting, interview, time, certification, badge, or admin context matters.
Stay factual, protect private information, and ask one clarifying question only when needed.""",
    }


def get_agent(agent_key: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    clean_key = (agent_key or "talent").strip().lower()
    custom_agent = _custom_agent_from_context(clean_key, context)
    if custom_agent:
        return custom_agent
    return AGENTS.get(clean_key) or AGENTS["talent"]


def _context_summary(context: dict[str, Any]) -> str:
    visible = {
        "domain": context.get("domain"),
        "page": context.get("page"),
        "candidateId": context.get("candidateId"),
        "candidateName": context.get("candidateName"),
        "candidateEmail": context.get("candidateEmail"),
        "jobId": context.get("jobId") or context.get("jobID"),
        "jobTitle": context.get("jobTitle"),
        "shortlistCount": context.get("shortlistCount"),
        "activeUrl": context.get("activeUrl"),
        "pageSnapshot": context.get("pageSnapshot"),
        "recentChat": context.get("recentChat"),
    }
    return json.dumps({k: v for k, v in visible.items() if v not in (None, "")}, indent=2)


def _numa_policy(context: dict[str, Any]) -> dict[str, Any]:
    access = context.get("numa_access") if isinstance(context.get("numa_access"), dict) else {}
    return {
        "can_view_sensitive": bool(access.get("can_view_sensitive")),
        "can_request_changes": bool(access.get("can_request_changes")),
        "mode": access.get("mode") or "guide-only",
        "role": access.get("role") or "anonymous",
    }


def _policy_text(agent_key: str, context: dict[str, Any]) -> str:
    policy = _numa_policy(context)
    restricted = not policy["can_view_sensitive"]
    change_restricted = not policy["can_request_changes"]
    rules = [
        "Numa safety policy:",
        "Numa must not harm code, write code changes, delete data, overwrite names, or perform database mutations by itself.",
        "Numa's default job is to guide, correct, look for errors, explain next steps, and recommend safe user actions.",
        "Numa must not reveal private financial or business details such as money, deal value, revenue, bill rates, salary, compensation, margins, contract value, or sensitive CRM deal details unless the user is an active super user or Administrator has unlocked admin access.",
        "If the user asks Numa to make changes, Numa may only describe the exact change and ask the user to use the app controls unless change-enabled mode is present.",
    ]
    if restricted:
        rules.append("Current access is guide-only: redact money, deal, salary, compensation, revenue, and contract values. Discuss priorities using non-sensitive labels like urgency, risk, relationship health, missing follow-up, and next action.")
    else:
        rules.append("Current access can view sensitive operational details, but still avoid exposing secrets and only use data provided in context or the app.")
    if change_restricted:
        rules.append("Current access cannot authorize Numa changes. Do not claim any app record, database field, profile name, code, salary, or deal data was changed.")
    else:
        rules.append("Current access is change-enabled for super/admin users. Still propose and confirm changes before saying they are applied.")
    if agent_key == "crm":
        rules.append("CRM focus rule: tell the user which deals or relationships to focus on by prioritizing urgency, stalled follow-up, relationship strength, upcoming meetings, and risk. For guide-only users, do not reveal amounts or salary/deal values.")
    return "\n".join(rules)


def _redact_sensitive_answer(answer: str, context: dict[str, Any]) -> str:
    if _numa_policy(context)["can_view_sensitive"]:
        return answer
    redacted = str(answer or "")
    redacted = re.sub(r"(?i)\b(salary|compensation|bill rate|hourly rate|deal value|contract value|revenue|margin|budget)\s*[:=]?\s*[$€£]?\s*[\d,]+(?:\.\d+)?[kKmM]?\b", r"\1: [restricted]", redacted)
    redacted = re.sub(r"[$€£]\s?\d[\d,]*(?:\.\d+)?\s?[kKmM]?\b", "[restricted amount]", redacted)
    redacted = re.sub(r"(?i)\b\d[\d,]*(?:\.\d+)?\s?(?:k|m|million|thousand)\s?(?:usd|dollars|revenue|deal|salary|budget|contract)\b", "[restricted amount]", redacted)
    return redacted


def _json_from_model_text(text: str) -> dict[str, Any]:
    try:
        return json.loads(text or "{}")
    except Exception:
        match = re.search(r"\{.*\}", text or "", re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}
    return {}


def _plan_actions(client, agent_key: str, message: str, context: dict[str, Any], agent: dict[str, Any]) -> list[dict[str, Any]]:
    intent_words = {
        "create",
        "make",
        "add",
        "update",
        "save",
        "record",
        "change",
        "attach",
        "set",
        "schedule",
        "book",
        "interview",
        "invite",
        "meet",
        "meeting",
    }
    clean_message = (message or "").strip()
    if not any(word in clean_message.lower() for word in intent_words):
        return []

    schema = """
Return strict JSON:
{
  "actions": [
    {
      "type": "create_profile" | "update_profile_core" | "schedule_interview_setup" | "create_job_description",
      "label": "short button label",
      "summary": "one sentence explaining the exact change",
      "missing_fields": ["field name Numa still needs from the user"],
      "payload": {
        "profile_id": "existing profile id for updates only",
        "full_name": "candidate full name for create",
        "first_name": "optional for update",
        "last_name": "optional for update",
        "email": "optional",
        "title": "optional role/title",
        "city": "optional",
        "state": "optional",
        "country": "optional",
        "description": "optional profile about/notes",
        "skills": [{"title": "Python", "years": 5}],
        "candidate_name": "interview candidate name",
        "candidate_email": "interview candidate email",
        "interview_type": "ready or client",
        "provider": "calendly, google, outlook, or ask",
        "calendar_app": "calendly, google, outlook, or ask",
        "role": "role/title for interview",
        "company": "company/client",
        "when": "natural language date/time if supplied",
        "timezone": "IANA timezone if supplied",
        "duration_minutes": 30,
        "interviewer_name": "DevReady interviewer for candidate review",
        "interviewer_email": "DevReady interviewer email",
        "client_company": "client interview company",
        "client_contact_name": "client interview contact",
        "client_contact_email": "client interview email",
        "talking_points": "optional talking points"
        "company": "client/company for job description",
        "job_title": "job description title",
        "jd_text": "complete job description text",
        "must_have_skills": ["Java"],
        "preferred_skills": ["React"],
        "team_traits": ["collaborative"]
      }
    }
  ]
}
Only propose an action when the user clearly asks Numa to create, save, add, update, schedule, book, or set up app workflow information.
Use create_profile for a brand new candidate profile.
Use update_profile_core when the page/context has an existing candidate/profile id and the user is adding or changing profile facts.
Use schedule_interview_setup when the user asks to set up, schedule, book, draft, or send an interview from the Interviews page or scheduling context.
For schedule_interview_setup, include missing_fields for anything still needed, especially candidate email, role/title, calendar app/provider, date/time window, interviewer/contact, or client details.
For schedule_interview_setup, default interview_type to "ready" for a candidate review. Only use "client" when the user explicitly says client interview/client-facing interview or provides client company/contact details.
For candidate review interviews, interviewer_name and interviewer_email identify the DevReady person meeting the candidate.
For client interviews, client_company, client_contact_name, and client_contact_email are required.
Do not propose create_profile or update_profile_core unless can_request_changes is true in the safety policy.
Use create_job_description when the user asks to create, draft, save, add, or add to system a job description. If the user says "the JD I just asked for" or similar, use the recent chat history in context to recover the prior drafted JD.
For create_job_description, include company, job_title, and a complete jd_text. If the original request is short, expand it into a professional JD without inventing confidential facts.
Do not propose create_job_description unless can_request_changes is true in the safety policy.
Do not propose actions for questions, analysis, ranking, salary, deal value, code changes, deletes, or uncertain instructions.
Keep profile descriptions factual. Do not invent facts beyond the user's message or current app context.
"""
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"),
        temperature=0,
        max_tokens=700,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": f"You are planning safe VETCODE actions for {agent['page']} Agent."},
            {"role": "system", "content": schema},
            {"role": "system", "content": _policy_text(agent_key, context)},
            {"role": "system", "content": f"Current app context:\n{_context_summary(context)}"},
            {"role": "user", "content": clean_message[:4000]},
        ],
        timeout=30,
    )
    parsed = _json_from_model_text(response.choices[0].message.content)
    actions = parsed.get("actions") if isinstance(parsed, dict) else []
    clean_actions = []
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict):
            continue
        action_type = action.get("type")
        if action_type in {"create_profile", "update_profile_core", "create_job_description"} and not _numa_policy(context)["can_request_changes"]:
            continue
        if action_type not in {"create_profile", "update_profile_core", "schedule_interview_setup", "create_job_description"}:
            continue
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        missing_fields = action.get("missing_fields") if isinstance(action.get("missing_fields"), list) else []
        if action_type == "schedule_interview_setup":
            interview_type = str(payload.get("interview_type") or "ready").strip().lower()
            if interview_type != "client":
                payload["interview_type"] = "ready"
                missing_fields = [
                    field
                    for field in missing_fields
                    if str(field).strip().lower() not in {"client_company", "client company", "client_contact_name", "client contact name", "client_contact_email", "client contact email"}
                ]
        clean_actions.append(
            {
                "type": action_type,
                "label": str(
                    action.get("label")
                    or (
                        "Create profile"
                        if action_type == "create_profile"
                        else (
                            "Set up interview"
                            if action_type == "schedule_interview_setup"
                            else ("Add job description" if action_type == "create_job_description" else "Update profile")
                        )
                    )
                )[:80],
                "summary": str(action.get("summary") or "")[:500],
                "missing_fields": missing_fields,
                "payload": payload,
            }
        )
    return clean_actions[:2]


def ask_page_agent(agent_key: str, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    agent = get_agent(agent_key, context)
    clean_message = (message or "").strip()
    if not clean_message:
        return {"ok": False, "agent": agent, "answer": "Ask me what you want to do on this page."}

    if not os.getenv("OPENAI_API_KEY"):
        return {
            "ok": True,
            "agent": agent,
            "access": _numa_policy(context),
            "actions": [],
            "answer": (
                f"{agent['name']} is ready. I can help with {agent['page']} using the active "
                "candidate, job, and domain context. Add OPENAI_API_KEY to enable live model answers."
            ),
        }

    client = getOpenAPIClient()
    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            max_tokens=420,
            messages=[
                {"role": "system", "content": agent["prompt"]},
                {
                    "role": "system",
                    "content": (
                        "You are inside VETCODE. Other page agents exist and can be referenced by specialty. "
                        "Give direct, app-specific guidance. If the user asks to create, save, add, or update app data, "
                        "explain that Numa can prepare a controlled action for confirmation when Admin Updates is on. "
                        "Use recentChat to understand references like 'the one above', 'that JD', or 'what I just asked for'. "
                        "Never claim a record was changed until an action result confirms it."
                    ),
                },
                {"role": "system", "content": _policy_text(agent_key, context)},
                {"role": "system", "content": f"Current app context:\n{_context_summary(context)}"},
                {"role": "user", "content": clean_message[:4000]},
            ],
            timeout=30,
        )
        answer = _redact_sensitive_answer(response.choices[0].message.content.strip(), context)
        actions = []
        try:
            actions = _plan_actions(client, agent_key, clean_message, context, agent)
        except Exception:
            actions = []
        return {
            "ok": True,
            "agent": agent,
            "access": _numa_policy(context),
            "actions": actions,
            "answer": answer,
        }
    except Exception as exc:
        return {
            "ok": False,
            "agent": agent,
            "access": _numa_policy(context),
            "actions": [],
            "answer": (
                f"{agent['name']} is connected to the shared OpenAI client, but the model request did not complete: {exc}"
            ),
        }
    finally:
        client.close()
