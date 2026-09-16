import socket
import json


def send_pymol_command(code: str, host: str = "localhost", port: int = 9880) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    try:
        sock.connect((host, port))
        message = json.dumps({"code": code})
        sock.sendall(message.encode("utf-8"))
        response = sock.recv(65536)
        return json.loads(response.decode("utf-8"))
    finally:
        sock.close()


commands = """
cmd.load('1LVM_no_neg.pdb', 'structure')
atom_count = cmd.count_atoms('structure')
residue_count = cmd.count_atoms('structure and name CA')
chains = cmd.get_chains('structure')
cmd.color('cyan', 'structure')
cmd.show_as('cartoon', 'structure')
cmd.png('1LVM_rendered.png', width=800, height=600, dpi=100, ray=0)
_result = {
    'atom_count': atom_count,
    'residue_count': residue_count,
    'chains': chains
}
"""

res = send_pymol_command(commands)
print("Analysis Result:")
print(res)
