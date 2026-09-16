import os
import sys
import requests
from pathlib import Path
import json

project_root = Path("/opt/protein_pipeline")
sys.path.append(str(project_root / "pipeline-mcp/src"))

from dotenv import load_dotenv
load_dotenv(str(project_root / "pipeline-mcp/.env"), override=True)

api_key = os.environ.get("RUNPOD_API_KEY")
endpoint_id = "u533d5016gsvil"

jobs_path = Path("outputs/admin_20260421_021144_3534ea86/tiers/50/af2/runpod_jobs.json")
data = json.loads(jobs_path.read_text())
jobs = data.get("jobs", {})

for seq_id, job_id in jobs.items():
    url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        print(f"[{seq_id}] {job_id} -> {response.json().get('status')}")
    else:
        print(f"[{seq_id}] {job_id} -> HTTP {response.status_code}")
