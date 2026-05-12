import json
import os
from datetime import datetime


DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
ANSWERS_PATH = os.path.join(DATA_DIR, "engineering_survey_answers.json")

SECTIONS = [
    {
        "name": "Logic & Problem-Solving",
        "questions": [
            "Initial Approach: Do you prefer to map out every dependency before starting, or do you find clarity through the act of doing?",
            "Abstract vs. Concrete: Are you more comfortable discussing high-level theory or practical, tangible applications?",
            "Deconstruction: When a system fails, do you look for the specific broken part or the flawed logic that allowed it to break?",
            "Tool Selection: Do you prefer using specialized, niche tools for specific tasks or versatile, multi-purpose frameworks?",
            "Simplicity: Do you believe the best solution is the one with the fewest moving parts, even if it lacks certain features?",
            "Mental Modeling: Do you visualize a solution in its entirety before you begin documenting it?",
            "Constraint Management: Do you find that strict limitations such as budget, time, or regulations actually improve your creative output?",
            "Research Habits: When stuck, is your first instinct to consult external documentation or to experiment until a solution emerges?",
            "Pattern Recognition: Do you often find solutions by applying lessons from a completely unrelated industry or field?",
            "Information Density: Do you prefer concise bulleted data or long-form contextual narratives?",
            "Edge Cases: How much time do you spend preparing for the 1% chance scenario compared to the 99% chance reality?",
            "Linearity: Do you work best following a step-by-step sequence or jumping between different sections of a project?",
            "Automation: If a task takes an hour, would you spend two hours finding a way to make it take ten minutes next time?",
            "Legacy Systems: Do you prefer cleaning up and optimizing an existing project or starting with a blank slate?",
            "Technical Debt: How comfortable are you leaving a good enough solution in place to meet a deadline, knowing you must fix it later?",
        ],
    },
    {
        "name": "Risk, Quality & Standards",
        "questions": [
            "Precedent: Do you feel more secure using a method that has worked for 20 years or a new method backed by recent data?",
            "Validation: Do you trust your own internal review process more than a third-party audit?",
            "Precision: Is close enough ever acceptable in your line of work, or is anything less than 100% accuracy a failure?",
            "Risk Tolerance: Would you rather take a calculated risk for a massive gain or play it safe for a guaranteed modest result?",
            "Standardization: Do you advocate for one way of doing things across your team, or do you value individual methodology?",
            "Documentation: Do you view recording your process as a burden or as a core part of the deliverable?",
            "Ambiguity: Can you make high-stakes decisions when you only have 60% of the necessary information?",
            "Scalability: When solving a problem for one client or project, do you automatically think about how it applies to all future ones?",
            "Skepticism: Do you naturally look for why a new idea will not work before looking for why it will?",
            "Compliance: Do you view regulations and red tape as a hurdle to clear or as a structural guide to follow?",
            "Attention Span: Are you better at the sprint of finishing a quick task or the marathon of a project lasting months?",
            "Error Culture: Do you believe most professional mistakes are caused by individual negligence or poor system design?",
            "Review Style: When reviewing someone else's work, do you focus on minor typos or the overall structural logic?",
            "Stability: Do you prefer a work environment where the rules are fixed, or one where the goalposts move frequently?",
            "Finality: Do you find it difficult to stop tweaking a project even after it has met all requirements?",
        ],
    },
    {
        "name": "Communication & Collaboration",
        "questions": [
            "Authority: Do you prefer to lead by expertise or by coordination?",
            "Mentorship: Do you prefer giving people the answer or giving them the tools to find it themselves?",
            "Conflict: In a professional disagreement, do you rely on data to win the argument or on finding a middle-ground consensus?",
            "Audience: Can you explain your most complex work to someone with zero technical or legal background?",
            "Transparency: Do you prefer to hide your work in progress until it is perfect, or do you share early drafts for feedback?",
            "Interdependence: Do you feel responsible for the success of the whole team, or primarily for your own specific tasks?",
            "Brainstorming: Do you think better out loud in a group or silently at your desk?",
            "Brevity: Is your professional writing style the more detail the better, or less is more?",
            "Delegation: Do you find it difficult to let go of tasks you know you can do faster than someone else?",
            "Feedback Sensitivity: Does a critique of your work feel like a critique of your personal competence?",
            "Social Battery: Does a day of meetings energize you or leave you needing time alone to recover?",
            "Instruction: Do you prefer receiving instructions via a conversation or a written brief?",
            "Presentation: Do you care more about how a report looks or what it says?",
            "Assertiveness: Are you comfortable telling a superior that their proposed plan is logically flawed?",
            "Networking: Do you view professional relationships as a transactional necessity or a genuine interest?",
        ],
    },
    {
        "name": "Drive, Ethics & Growth",
        "questions": [
            "Motivation: Are you driven by the prestige of the project or the difficulty of the work?",
            "Learning Curve: Do you enjoy being the expert whom everyone asks for help, or being the student learning something new?",
            "Ethics: If a strategy is legal but feels unfair, would you still recommend it?",
            "Adaptability: How quickly can you pivot when your primary strategy is suddenly rendered obsolete?",
            "Work-Life Boundary: Do you find it easy to turn off your professional brain at the end of the day?",
            "Ambition: Is your goal to be the best at what you do, or to be the person who manages those who do it?",
            "Curiosity: Do you often find yourself researching topics that have nothing to do with your current project?",
            "Resilience: How long does it take you to recover from a major professional loss or rejected proposal?",
            "Intuition: How much do you rely on a gut feeling versus what the data explicitly tells you?",
            "Ownership: Do you feel a personal sense of craftsmanship over every document or design you produce?",
            "Environment: Do you work better in a high-pressure noisy environment or a quiet controlled one?",
            "Vision: Do you focus more on where the project will be in 5 days or 5 years?",
            "Self-Reflection: Are you aware of your professional blind spots, or do you wait for others to point them out?",
        ],
    },
]


QUESTIONS = [question for section in SECTIONS for question in section["questions"]]


def is_engineer_domain(domain: str = "") -> bool:
    return (domain or "").strip().lower() in {"engineer", "engineering"}


def get_questions() -> list[str]:
    return QUESTIONS


def get_question(index: int) -> str:
    if index < 1 or index > len(QUESTIONS):
        raise IndexError("Engineering survey question index out of range")
    return QUESTIONS[index - 1]


def question_section(index: int) -> str:
    running = 0
    for section in SECTIONS:
        running += len(section["questions"])
        if index <= running:
            return section["name"]
    return "Engineering"


def _read_answers() -> dict:
    try:
        if os.path.exists(ANSWERS_PATH):
            with open(ANSWERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _write_answers(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = ANSWERS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, ANSWERS_PATH)


def save_answer(person_id: int | str, question_number: int, answer: int) -> None:
    if not person_id:
        return
    data = _read_answers()
    key = str(person_id)
    entry = data.setdefault(key, {"domain": "engineer", "answers": {}, "updated_at": ""})
    entry["answers"][str(question_number)] = {
        "answer": int(answer),
        "question": get_question(question_number),
        "section": question_section(question_number),
    }
    entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_answers(data)


def profile_personality(person_id: int | str) -> list[dict]:
    entry = _read_answers().get(str(person_id), {})
    answers = entry.get("answers", {}) if isinstance(entry, dict) else {}
    if not answers:
        return []

    buckets = {section["name"]: [] for section in SECTIONS}
    for raw in answers.values():
        section = raw.get("section")
        answer = raw.get("answer")
        if section in buckets and isinstance(answer, int):
            buckets[section].append(answer)

    rows = []
    for index, (section, values) in enumerate(buckets.items(), start=9001):
        if not values:
            continue
        rows.append({
            "title": section,
            "id": index,
            "score": round((sum(values) / len(values)) / 5 * 100),
            "domain": "engineer",
        })
    return rows
