import json, urllib.request, sys
with open("./outputs/admin_20260409_013302_4b230da5/request.json") as f: req = json.load(f)
req["continue_same_run"] = True
req["run_id"] = "admin_20260409_013302_4b230da5"
try:
    login_req = urllib.request.Request("http://127.0.0.1:18080/auth/login", data=json.dumps({"username":"admin","password":"mimikyuiscute"}).encode(), headers={"Content-Type":"application/json"})
    token = json.loads(urllib.request.urlopen(login_req).read().decode())["token"]
    call_req = urllib.request.Request("http://127.0.0.1:18080/tools/call", data=json.dumps({"name":"pipeline.run","arguments":req}).encode(), headers={"Content-Type":"application/json", "Authorization":f"Bearer {token}"})
    print(urllib.request.urlopen(call_req).read().decode())
except Exception as e:
    print(e.read().decode() if hasattr(e, 'read') else e)
