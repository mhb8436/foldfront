import os
import sys
import requests
from pathlib import Path

project_root = Path("/opt/protein_pipeline")
from dotenv import load_dotenv
load_dotenv(str(project_root / "pipeline-mcp/.env"), override=True)

api_key = os.environ.get("RUNPOD_API_KEY")
endpoint_id = "u533d5016gsvil"
job_ids = [
    "0837cdfe-5a74-4fda-b23f-496382724afd-e2",
    "2e87a6fb-b78b-4adf-a55f-179cfa30426e-e2",
    "06758d83-e5d0-4133-8816-516207fdab2b-e1",
    "afb815cf-450f-44e9-9569-d454af97c099-e1",
    "b727236c-eaf0-47cd-b2eb-fa6fe99b1f3a-e1",
    "3ee988f0-7516-4114-b0b0-b019874c79d7-e2",
    "0b8af8ea-9556-4ff7-83ab-5298937904b9-e2",
    "40153930-6458-41a4-85e2-7a3a39d0d6b4-e2",
    "85a4ca32-ed68-4fa7-a4a4-ee0edec4f9b0-e1"
]

for job_id in job_ids:
    url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(url, headers=headers)
    print(f"Job {job_id}: {response.status_code} - {response.json().get('status')}")
