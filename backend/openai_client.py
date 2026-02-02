
import os, time
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def run_prompt(prompt, text):
    start = time.time()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text[:12000]}
        ],
        timeout=30
    )
    return resp.choices[0].message.content
