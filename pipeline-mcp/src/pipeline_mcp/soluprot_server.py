from __future__ import annotations

import argparse
import json
import math
import re
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any


_SEQ_RE = re.compile(r"[^A-Za-z]+")
_HYDROPHOBIC = set("AVLIMFWYC")
_CHARGED = set("DEKRH")
_POLAR = set("STNQ")
_AROMATIC = set("FWY")


def _clean_sequence(seq: str) -> str:
    return _SEQ_RE.sub("", (seq or "")).upper()


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def soluprot_score(sequence: str) -> float:
    seq = _clean_sequence(sequence)
    n = len(seq)
    if n <= 0:
        return 0.0

    hyd = sum(1 for aa in seq if aa in _HYDROPHOBIC) / n
    chg = sum(1 for aa in seq if aa in _CHARGED) / n
    pol = sum(1 for aa in seq if aa in _POLAR) / n
    aro = sum(1 for aa in seq if aa in _AROMATIC) / n

    z = (chg - hyd) * 6.0 + (pol - 0.2) * 1.2 - (aro - 0.06) * 1.0 + 1.5
    score = float(_sigmoid(z))

    if n > 1200:
        score *= 1200.0 / float(n)

    return max(0.0, min(1.0, score))


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(raw.decode("utf-8", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/healthz":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path.rstrip("/") != "/score":
                self._json(404, {"error": "not found"})
                return

            body = self._read_json()
            sequences = body.get("sequences")
            if not isinstance(sequences, list):
                raise ValueError("Expected {sequences: [{id, sequence}, ...]}")

            results: list[dict[str, Any]] = []
            for item in sequences:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("id") or "")
                seq = str(item.get("sequence") or "")
                if not sid:
                    continue
                results.append({"id": sid, "score": soluprot_score(seq)})

            self._json(200, {"results": results})
        except Exception as exc:
            self._json(400, {"error": str(exc)})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args(argv)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"listening: http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

