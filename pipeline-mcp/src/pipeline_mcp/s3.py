import os
import boto3
from pathlib import Path
from botocore.client import Config

class NCPStorage:
    def __init__(self):
        self._client = None
        self._initialized = False
        self.bucket_name = None

    def _ensure_initialized(self):
        if self._initialized:
            return
        
        self.endpoint_url = os.getenv("NCP_S3_ENDPOINT", "https://kr.object.ncloudstorage.com")
        self.access_key = os.getenv("NCP_S3_ACCESS_KEY")
        self.secret_key = os.getenv("NCP_S3_SECRET_KEY")
        self.bucket_name = os.getenv("NCP_S3_BUCKET", "protein-pipeline-outputs")
        self.region_name = os.getenv("NCP_S3_REGION", "kr-standard")
        
        if not self.access_key or not self.secret_key:
            self._client = None
        else:
            try:
                self._client = boto3.client(
                    's3',
                    endpoint_url=self.endpoint_url,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    region_name=self.region_name,
                    config=Config(signature_version='s3v4')
                )
            except Exception:
                self._client = None
        self._initialized = True

    @property
    def client(self):
        self._ensure_initialized()
        return self._client

    @property
    def bucket(self):
        self._ensure_initialized()
        return self.bucket_name

    def upload_file(self, local_path, remote_path=None):
        self._ensure_initialized()
        if not self._client: return False
        if remote_path is None:
            remote_path = local_path
        
        try:
            self._client.upload_file(str(local_path), self.bucket_name, str(remote_path))
            return True
        except Exception as e:
            print(f"S3 Upload Failed: {e}")
            return False

    def sync_outputs(self, run_id, local_root="outputs"):
        """Syncs an entire run directory to S3"""
        self._ensure_initialized()
        if not self._client: return
        local_dir = Path(local_root) / run_id
        if not local_dir.exists(): return
        
        print(f"Syncing run {run_id} to NCP S3...")
        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(Path(local_root).parent)
                self.upload_file(file_path, str(relative_path))

    def download_model(self, model_name, local_dest="pipeline-mcp/models"):
        """Downloads latest model weights from S3"""
        self._ensure_initialized()
        if not self._client: return False
        dest_path = Path(local_dest) / model_name
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            self._client.download_file(self.bucket_name, f"models/{model_name}", str(dest_path))
            return True
        except Exception:
            return False

ncp_storage = NCPStorage()
