"""설정.

시크릿·키·민감정보는 환경변수로만 받고 문서·로그에 값을 남기지 않는다.
현행 RAPID 가 쓰던 환경변수 이름(RUNPOD_API_KEY · PIPELINE_OUTPUT_ROOT · *_ENDPOINT_ID)을
그대로 승계한다 — 운영 환경 설정을 다시 작성하지 않아도 된다.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    #  DB 는 MongoDB 로 고정한다
    mongo_uri: str = Field(default="mongodb://127.0.0.1:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="foldfront", alias="MONGO_DB")

    #  아티팩트 실체가 놓이는 곳. 현행 환경변수 이름을 승계한다
    output_root: str = Field(default="./data/runs", alias="PIPELINE_OUTPUT_ROOT")

    #  외부 GPU. 값은 환경변수로만 받는다
    runpod_api_key: str | None = Field(default=None, alias="RUNPOD_API_KEY")

    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=18090, alias="API_PORT")

    #  작업 큐 lease 시간(초)
    job_lease_seconds: int = Field(default=900, alias="JOB_LEASE_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
