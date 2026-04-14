from infra.k8s_client import K8sClient
from infra.helm_manager import HelmManager
from infra.prometheus_client import PrometheusClient
from infra.grafana_client import GrafanaClient

print("--- K8s Client ---")
k = K8sClient()
print("Ping:", k.ping())
try:
    print("Pods in free5gc:", len(k.get_pods("free5gc")))
except Exception as e:
    print("Error:", e)

print("\n--- Prometheus Client ---")
p = PrometheusClient()
print("Ping:", p.ping())
try:
    print("Metrics sample:", p.get_cpu_usage(namespace="free5gc", duration="1m"))
except Exception as e:
    print("Error:", e)

print("\n--- Grafana Client ---")
g = GrafanaClient()
print("Ping:", g.ping())

print("\n--- Helm Manager ---")
h = HelmManager()
try:
    print("Releases:", len(h.list_releases("free5gc")))
except Exception as e:
    print("Error:", e)
