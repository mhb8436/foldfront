import json
import urllib.request

login_data = json.dumps({"username": "admin", "password": "mimikyuiscute"}).encode("utf-8")
login_req = urllib.request.Request("http://127.0.0.1:18080/auth/login", data=login_data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(login_req) as response:
    token = json.loads(response.read().decode())["token"]

with open("./outputs/admin_20260409_013302_4b230da5/request.json") as f:
    req = json.load(f)

req["continue_same_run"] = True
req["run_id"] = "admin_20260409_013302_4b230da5"

call_data = json.dumps({"name": "pipeline.run", "arguments": req}).encode("utf-8")
call_req = urllib.request.Request("http://127.0.0.1:18080/tools/call", data=call_data, headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})

with urllib.request.urlopen(call_req) as response:
    print(response.read().decode())
