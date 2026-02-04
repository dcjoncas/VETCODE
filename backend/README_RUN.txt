DevReady Vetting v2.3.0 — Technical-only flow

New in v2.3.0:
- Company field for Job Descriptions
- View Profile as HTML (shareable) + Download as DOCX
- View Job Description as HTML + Download as DOCX
- Tabs: Dashboard / All Profiles / All Job Descriptions
- Load past JDs back into editor for testing and re-save to make latest

Run:
  cd C:\VETCODE\backend
  python -m venv .venv
  .venv\Scripts\activate
  pip install -r ../requirements.txt
  uvicorn main:app --reload

Open:
  http://127.0.0.1:8000/ui/index.html
