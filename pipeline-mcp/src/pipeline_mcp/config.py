from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class RunPodConfig:
    api_key: str
    mmseqs_endpoint_id: str
    proteinmpnn_endpoint_id: str | None
    colabfold_endpoint_id: str | None
    alphafold2_endpoint_id: str | None
    bioemu_endpoint_id: str | None
    diffdock_endpoint_id: str | None
    rfd3_endpoint_id: str | None
    relax_endpoint_id: str | None
    ca_bundle: str | None
    skip_verify: bool


@dataclass(frozen=True)
class ProteinMPNNConfig:
    provider: str
    gpu_url: str | None
    gpu_token: str | None
    gpu_timeout_s: float


@dataclass(frozen=True)
class ServiceConfig:
    soluprot_url: str | None
    af2_url: str | None


@dataclass(frozen=True)
class RosettaConfig:
    docker_image: str | None
    docker_bin: str | None
    relax_binary: str | None
    score_binary: str | None
    database_path: str | None
    timeout_s: float


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str | None
    model_name: str


@dataclass(frozen=True)
class AppConfig:
    runpod: RunPodConfig
    proteinmpnn: ProteinMPNNConfig
    services: ServiceConfig
    rosetta: RosettaConfig
    gemini: GeminiConfig
    output_root: str


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _proteinmpnn_provider() -> str:
    value = os.environ.get("PROTEINMPNN_PROVIDER", "runpod").strip().lower() or "runpod"
    if value in {"gpu", "http", "gpu-http"}:
        return "gpu_http"
    if value not in {"runpod", "gpu_http"}:
        raise RuntimeError("PROTEINMPNN_PROVIDER must be one of: runpod, gpu_http")
    return value


def load_config() -> AppConfig:
    api_key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY is required")

    mmseqs_endpoint_id = os.environ.get("MMSEQS_ENDPOINT_ID", "").strip()
    if not mmseqs_endpoint_id:
        raise RuntimeError("MMSEQS_ENDPOINT_ID is required")

    proteinmpnn_provider = _proteinmpnn_provider()
    proteinmpnn_endpoint_id = os.environ.get("PROTEINMPNN_ENDPOINT_ID", "").strip() or None
    proteinmpnn_gpu_url = os.environ.get("PROTEINMPNN_GPU_URL", "").strip() or None
    proteinmpnn_gpu_token = os.environ.get("PROTEINMPNN_GPU_TOKEN", "").strip() or None
    proteinmpnn_gpu_timeout_s = float(
        os.environ.get("PROTEINMPNN_GPU_TIMEOUT_S", "21600").strip() or "21600"
    )
    if proteinmpnn_provider == "runpod" and not proteinmpnn_endpoint_id:
        raise RuntimeError("PROTEINMPNN_ENDPOINT_ID is required")
    if proteinmpnn_provider == "gpu_http" and not proteinmpnn_gpu_url:
        raise RuntimeError("PROTEINMPNN_GPU_URL is required when PROTEINMPNN_PROVIDER=gpu_http")

    colabfold_endpoint_id = (
        os.environ.get("COLABFOLD_ENDPOINT_ID", "").strip()
        or os.environ.get("COLABFOLD_RUNPOD_ENDPOINT_ID", "").strip()
        or None
    )

    alphafold2_endpoint_id = (
        os.environ.get("ALPHAFOLD2_ENDPOINT_ID", "").strip()
        or os.environ.get("AF2_ENDPOINT_ID", "").strip()
        or os.environ.get("ALPHAFOLD2_RUNPOD_ENDPOINT_ID", "").strip()
        or None
    )
    bioemu_endpoint_id = os.environ.get("BIOEMU_ENDPOINT_ID", "").strip() or None
    diffdock_endpoint_id = os.environ.get("DIFFDOCK_ENDPOINT_ID", "").strip() or None
    rfd3_endpoint_id = os.environ.get("RFD3_ENDPOINT_ID", "").strip() or None
    relax_endpoint_id = os.environ.get("RUNPOD_RELAX_ENDPOINT_ID", "").strip() or None

    ca_bundle = os.environ.get("RUNPOD_CA_BUNDLE", "").strip() or None
    skip_verify = _env_true("RUNPOD_SKIP_VERIFY") or _env_true("RUNPOD_INSECURE")

    soluprot_url = os.environ.get("SOLUPROT_URL", "").strip() or None
    af2_url = os.environ.get("AF2_URL", "").strip() or None
    rosetta_docker_image = os.environ.get("ROSETTA_DOCKER_IMAGE", "").strip() or None
    rosetta_docker_bin = os.environ.get("ROSETTA_DOCKER_BIN", "").strip() or None
    rosetta_relax_binary = os.environ.get("ROSETTA_RELAX_BIN", "").strip() or None
    rosetta_score_binary = os.environ.get("ROSETTA_SCORE_BIN", "").strip() or None
    rosetta_database_path = os.environ.get("ROSETTA_DATABASE", "").strip() or None
    rosetta_timeout_s = float(os.environ.get("ROSETTA_TIMEOUT_S", "3600").strip() or "3600")

    output_root = os.environ.get("PIPELINE_OUTPUT_ROOT", "outputs").strip() or "outputs"

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip() or None
    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"


    return AppConfig(
        runpod=RunPodConfig(
            api_key=api_key,
            mmseqs_endpoint_id=mmseqs_endpoint_id,
            proteinmpnn_endpoint_id=proteinmpnn_endpoint_id,
            colabfold_endpoint_id=colabfold_endpoint_id,
            alphafold2_endpoint_id=alphafold2_endpoint_id,
            bioemu_endpoint_id=bioemu_endpoint_id,
            diffdock_endpoint_id=diffdock_endpoint_id,
            rfd3_endpoint_id=rfd3_endpoint_id,
            relax_endpoint_id=relax_endpoint_id,
            ca_bundle=ca_bundle,
            skip_verify=bool(skip_verify),
        ),
        proteinmpnn=ProteinMPNNConfig(
            provider=proteinmpnn_provider,
            gpu_url=proteinmpnn_gpu_url,
            gpu_token=proteinmpnn_gpu_token,
            gpu_timeout_s=proteinmpnn_gpu_timeout_s,
        ),
        services=ServiceConfig(soluprot_url=soluprot_url, af2_url=af2_url),
        rosetta=RosettaConfig(
            docker_image=rosetta_docker_image,
            docker_bin=rosetta_docker_bin,
            relax_binary=rosetta_relax_binary,
            score_binary=rosetta_score_binary,
            database_path=rosetta_database_path,
            timeout_s=rosetta_timeout_s,
        ),
        gemini=GeminiConfig(
            api_key=gemini_api_key,
            model_name=gemini_model,
        ),
        output_root=output_root,
    )
