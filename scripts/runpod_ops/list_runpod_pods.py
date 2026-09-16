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

# RunPod REST API to list pods
url = "https://api.runpod.ai/graphql"
headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
# GraphQL query for pods
query = """
query {
  myself {
    pods {
      id
      name
      runtime {
        status
      }
      machineId
    }
  }
}
"""
response = requests.post(url, json={"query": query}, headers=headers)

if response.status_code == 200:
    data = response.json()
    pods = data.get('data', {}).get('myself', {}).get('pods', [])
    print(f"Total pods: {len(pods)}")
    for pod in pods:
        print(f"Pod ID: {pod.get('id')}, Name: {pod.get('name')}, Status: {pod.get('runtime', {}).get('status')}, Machine ID: {pod.get('machineId')}")
else:
    print(f"Error: {response.status_code}")
    print(response.text)
