import os
import sys
from pathlib import Path
import requests

project_root = Path("/opt/protein_pipeline")
sys.path.append(str(project_root / "pipeline-mcp/src"))

from dotenv import load_dotenv
load_dotenv(str(project_root / "pipeline-mcp/.env"), override=True)

api_key = os.environ.get("RUNPOD_API_KEY")
if not api_key:
    print("RUNPOD_API_KEY not found")
    sys.exit(1)

endpoint_id = "u533d5016gsvil"
url = f"https://api.runpod.ai/v2/{endpoint_id}/health"
headers = {"Authorization": f"Bearer {api_key}"}
response = requests.get(url, headers=headers)

if response.status_code == 200:
    print(f"Health: {response.json()}")
else:
    print(f"Error: {response.status_code}")
    print(response.text)

# Also try the REST API for endpoint detail
url = f"https://api.runpod.io/v1/{api_key}/endpoints/{endpoint_id}"
# Wait, I don't know the exact URL for REST. 
# Let's use the one from runpod.py: _RUNPOD_REST_API_BASE + /endpoints/{endpoint_id}
rest_url = f"https://rest.runpod.io/v1/endpoints/{endpoint_id}"
headers = {"Authorization": f"Bearer {api_key}"}
response = requests.get(rest_url, headers=headers)
if response.status_code == 200:
    print(f"Endpoint Detail: {response.json()}")
else:
    print(f"REST Error: {response.status_code}")
    print(response.text)
