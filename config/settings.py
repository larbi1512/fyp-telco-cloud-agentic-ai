import os
from pathlib import Path
from dotenv import load_dotenv

# Project paths 
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# LLM Backend Selection (ollama or vllm)
LLM_BACKEND: str = os.getenv("LLM_BACKEND", "vllm")

# Ollama 
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gpt-oss:120b")
OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
OLLAMA_MAX_TOKENS: int = int(os.getenv("OLLAMA_MAX_TOKENS", "4096"))

# vLLM (OpenAI-compatible)
VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
VLLM_MODEL: str = os.getenv("VLLM_MODEL", "gpt-oss:120b")
VLLM_API_KEY: str = os.getenv("VLLM_API_KEY", "EMPTY")  

# Remote Kubernetes 
KUBECONFIG_PATH: str = os.getenv("KUBECONFIG_PATH", "~/.kube/config")
K8S_NAMESPACE: str = os.getenv("K8S_NAMESPACE", "oai-5g")

# Remote Prometheus / Grafana 
PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
GRAFANA_URL: str = os.getenv("GRAFANA_URL", "http://localhost:3000")
GRAFANA_API_KEY: str = os.getenv("GRAFANA_API_KEY", "")

# Logging
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR: Path = Path(os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs")))

# Config file paths
CONFIG_DIR: Path = PROJECT_ROOT / "config"
VNF_PROFILES_PATH: Path = CONFIG_DIR / "vnf_resource_profiles.yaml"
SLA_THRESHOLDS_PATH: Path = CONFIG_DIR / "sla_thresholds.yaml"
PROMPTS_DIR: Path = PROJECT_ROOT / "core" / "prompts"
