import os
import sys
from pathlib import Path

project_root = Path("/opt/protein_pipeline")
sys.path.append(str(project_root / "pipeline-mcp/src"))

from dotenv import load_dotenv
load_dotenv(str(project_root / "pipeline-mcp/.env"), override=True)

from pipeline_mcp.clients.runpod import RunPodClient

api_key = os.environ.get("RUNPOD_API_KEY")
if not api_key:
    print("RUNPOD_API_KEY not found")
    sys.exit(1)

client = RunPodClient(api_key=api_key)
job_id = "85a4ca32-ed68-4fa7-a4a4-ee0edec4f9b0-e1"
endpoint_id = "u533d5016gsvil"

try:
    # RunPodClient doesn't have a direct get_job_status, but let's see its methods
    print(f"Checking job {job_id} on endpoint {endpoint_id}...")
    # Actually, looking at runpod.py earlier, it has poll methods.
    # Let's try to just use requests to see the status if we can.
    import requests
    url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(url, headers=headers)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
