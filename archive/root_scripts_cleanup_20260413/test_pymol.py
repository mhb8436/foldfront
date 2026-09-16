import socket
import json
import time


def send_pymol_command(code: str, host: str = "localhost", port: int = 9880) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((host, port))
        message = json.dumps({"code": code})
        sock.sendall(message.encode("utf-8"))
        response = sock.recv(65536)
        return json.loads(response.decode("utf-8"))
    finally:
        sock.close()


try:
    print("Testing claudemol connection...")
    result = send_pymol_command("cmd.get_version()")
    print("Success! PyMOL version:", result)
except Exception as e:
    print("Failed to connect:", e)
