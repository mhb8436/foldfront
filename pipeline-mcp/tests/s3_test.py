import sys
import os
from pathlib import Path

# Add src to sys.path
current_dir = Path(__file__).resolve().parent
src_dir = current_dir.parent / "src"
sys.path.append(str(src_dir))

# Try to find .env in various locations
env_locations = [
    current_dir.parent / ".env",  # pipeline-mcp/.env
    Path(".env"),
]

env_path = None
for loc in env_locations:
    if loc.exists():
        env_path = loc
        break

if env_path:
    print(f"Loading environment from: {env_path}")
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value
else:
    print("Warning: .env file not found.")

from pipeline_mcp.s3 import ncp_storage

def test_connection():
    print(f"Testing S3 Connection to bucket: {ncp_storage.bucket}")
    if not ncp_storage.client:
        print("❌ Client not initialized. Check your .env file and credentials.")
        return
    
    try:
        # Use ncp_storage.client which is now a property
        ncp_storage.client.put_object(Bucket=ncp_storage.bucket, Key="tests/health_check.txt", Body="ok")
        print("✅ S3 Write Success!")
        ncp_storage.client.delete_object(Bucket=ncp_storage.bucket, Key="tests/health_check.txt")
    except Exception as e:
        print(f"❌ S3 Write Failed: {e}")

if __name__ == "__main__":
    test_connection()
