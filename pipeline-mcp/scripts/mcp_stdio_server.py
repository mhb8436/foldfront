from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ[key] = value


def main() -> None:
    pipeline_mcp_dir = Path(__file__).resolve().parents[1]
    src_dir = pipeline_mcp_dir / "src"

    sys.path.insert(0, str(src_dir))
    _load_dotenv(pipeline_mcp_dir / ".env")

    from pipeline_mcp.mcp_stdio_server import main as _server_main

    _server_main()


if __name__ == "__main__":
    main()
