"""Settings.

Secrets arrive through the environment and are never written to a document or
a log. The variable names are the ones the original uses - RUNPOD_API_KEY,
PIPELINE_OUTPUT_ROOT, *_ENDPOINT_ID - so an existing deployment does not have
to be reconfigured to run this.
"""

from __future__ import annotations

from functools import lru_cache

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    #  MongoDB is the store; this is not pluggable
    mongo_uri: str = Field(default="mongodb://127.0.0.1:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="foldfront", alias="MONGO_DB")

    #  Where artifact files land, under the name the original uses
    output_root: str = Field(default="./data/runs", alias="PIPELINE_OUTPUT_ROOT")

    @field_validator("output_root")
    @classmethod
    def _absolute_root(cls, v: str) -> str:
        #  A relative root is anchored at the platform directory, not the
        #  cwd. The API and the worker are started from wherever, and the
        #  root confinement in payloads.py has to name the same place for both
        #  - or the worker refuses every file the API stored.
        path = Path(v).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        return str(path.resolve())

    #  Remote GPUs. The key comes from the environment only
    runpod_api_key: str | None = Field(default=None, alias="RUNPOD_API_KEY")

    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=18090, alias="API_PORT")

    #  How long a worker may hold a job, in seconds
    #  Role the development identity carries while OIDC is off. Lets the
    #  console be seen as a viewer or a researcher without an identity provider.
    dev_role: str = Field(default="admin", alias="DEV_ROLE")

    job_lease_seconds: int = Field(default=900, alias="JOB_LEASE_SECONDS")

    #  Uploaded inputs. One person may hold this much at once, and a file no
    #  run has read is removed after this many days. A file a run read stays
    #  as long as the run does - it is that run's provenance.
    #  The design copilot's model: any OpenAI-compatible chat endpoint. The
    #  default is ollama on this machine; nothing leaves the box.
    local_llm_url: str = Field(default="http://127.0.0.1:11434/v1", alias="LOCAL_LLM_URL")
    local_llm_model: str = Field(default="exaone3.5:7.8b", alias="LOCAL_LLM_MODEL")
    local_llm_timeout_s: float = Field(default=120.0, alias="LOCAL_LLM_TIMEOUT_S")

    input_quota_mb: int = Field(default=512, alias="INPUT_QUOTA_MB")
    input_retention_days: int = Field(default=30, alias="INPUT_RETENTION_DAYS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
