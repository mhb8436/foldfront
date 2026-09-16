"""Settings.

Secrets arrive through the environment and are never written to a document or
a log. The variable names are the ones the original uses - RUNPOD_API_KEY,
PIPELINE_OUTPUT_ROOT, *_ENDPOINT_ID - so an existing deployment does not have
to be reconfigured to run this.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    #  MongoDB is the store; this is not pluggable
    mongo_uri: str = Field(default="mongodb://127.0.0.1:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="foldfront", alias="MONGO_DB")

    #  Where artifact files land, under the name the original uses
    output_root: str = Field(default="./data/runs", alias="PIPELINE_OUTPUT_ROOT")

    #  Remote GPUs. The key comes from the environment only
    runpod_api_key: str | None = Field(default=None, alias="RUNPOD_API_KEY")

    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=18090, alias="API_PORT")

    #  How long a worker may hold a job, in seconds
    job_lease_seconds: int = Field(default=900, alias="JOB_LEASE_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
