import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def _load_worker_module():
    path = Path(__file__).resolve().parents[1] / "deploy" / "gpu" / "proteinmpnn_http_worker.py"
    spec = importlib.util.spec_from_file_location("proteinmpnn_http_worker_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProteinMPNNHttpWorkerTest(unittest.TestCase):
    def test_load_handler_ignores_runpod_serverless_start(self):
        module = _load_worker_module()
        module._HANDLER = None

        runpod_module = types.ModuleType("runpod")
        serverless_module = types.ModuleType("runpod.serverless")

        def fail_start(_payload):
            raise RuntimeError("serverless start should be patched by worker")

        serverless_module.start = fail_start
        runpod_module.serverless = serverless_module

        with tempfile.TemporaryDirectory() as tmp:
            handler_path = Path(tmp) / "handler.py"
            handler_path.write_text(
                "import runpod\n"
                "def handler(event):\n"
                "    return {'native': {'sequence': 'AAA'}, 'samples': []}\n"
                "runpod.serverless.start({'handler': handler})\n",
                encoding="utf-8",
            )
            with patch.dict(sys.modules, {"runpod": runpod_module, "runpod.serverless": serverless_module}):
                with patch.dict(os.environ, {"PROTEINMPNN_HANDLER_PATH": str(handler_path)}):
                    handler = module._load_handler()

        self.assertEqual(handler({"input": {}})["native"]["sequence"], "AAA")


if __name__ == "__main__":
    unittest.main()
