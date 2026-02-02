
import json
from openai_client import run_prompt

def normalize_resume(raw, domain):
    prompt = open("prompts/resume_to_profile.txt").read()
    content = run_prompt(prompt, raw)
    return json.loads(content)

def normalize_job(raw, domain):
    prompt = open("prompts/job_to_profile.txt").read()
    content = run_prompt(prompt, raw)
    return json.loads(content)
