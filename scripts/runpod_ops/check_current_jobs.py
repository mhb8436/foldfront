import os
import sys
import requests
import json
from pathlib import Path

project_root = Path("/opt/protein_pipeline")
sys.path.append(str(project_root / "pipeline-mcp/src"))

from dotenv import load_dotenv
load_dotenv(str(project_root / "pipeline-mcp/.env"), override=True)

api_key = os.environ.get("RUNPOD_API_KEY")
endpoint_id = "u533d5016gsvil"

# Read the current jobs
jobs_path = Path("outputs/admin_20260421_021144_3534ea86/tiers/50/af2/runpod_jobs.json")
if jobs_path.exists():
    data = json.loads(jobs_path.read_text())
    jobs = data.get("jobs", {})
    print("Checking active jobs...")
    for seq_id, job_id in list(jobs.items())[:4]: # Check the first 4 since they were resubmitted
        url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            status_data = response.json()
            status = status_data.get('status')
            print(f"[{seq_id}] Job {job_id}: {status}")
            if status == "IN_PROGRESS" or status == "RUNNING":
                # Print execution time if available in the API response
                print(f"  Details: {status_data}")
        else:
            print(f"[{seq_id}] Job {job_id}: HTTP {response.status_code}")
else:
    print("No jobs file found.")
