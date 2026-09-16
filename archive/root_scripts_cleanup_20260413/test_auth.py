import json
import urllib.request
login_data = json.dumps({"username": "admin", "password": "mimikyuiscute"}).encode("utf-8")
login_req = urllib.request.Request("http://127.0.0.1:18080/auth/login", data=login_data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(login_req) as response:
    print(response.read().decode())
