
VETCODE v3.0.0
==============

Implemented:
- Sidebar navigation works (Vetting / Profiles / Job Descriptions)
- Profiles list with checkbox + hard delete (confirm required)
- Job Descriptions list with checkbox + hard delete (confirm required)
- Selecting ranked candidate auto-loads full profile + recommendation pack
- No JSON rendered in UI
- Bulk upload moved to Profiles tab
- Hard delete only (no soft delete)

Run:
  cd C:\VETCODE\backend
  python -m venv .venv
  .venv\Scripts\activate
  pip install -r ../requirements.txt
  uvicorn main:app --reload

Open:
  http://127.0.0.1:8000/ui/index.html
