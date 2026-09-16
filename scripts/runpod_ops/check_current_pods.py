import os
import sys
from pathlib import Path
import requests

project_root = Path("/opt/protein_pipeline")
sys.path.append(str(project_root / "pipeline-mcp/src"))

from dotenv import load_dotenv
load_dotenv(str(project_root / "pipeline-mcp/.env"), override=True)

api_key = os.environ.get("RUNPOD_API_KEY")
endpoint_id = "u533d5016gsvil"

url = f"https://api.runpod.ai/v2/{endpoint_id}/health"
headers = {"Authorization": f"Bearer {api_key}"}
response = requests.get(url, headers=headers)
print(f"ColabFold Endpoint Health: {response.json()}")

