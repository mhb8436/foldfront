import requests
import json
import time

SERVER_URL = "http://127.0.0.1:18080/tools/call"
def call_tool(name, args):
    resp = requests.post(SERVER_URL, json={"name": name, "arguments": args})
    return resp.json()["result"]

# Wait, adding a new UI mode might be very hard without breaking everything since it expects pipeline.run.
# Instead of a whole new UI mode right now, let's inject a boolean to PipelineRequest: `evolution_mode`.
# When true, the backend orchestrates the loop!
