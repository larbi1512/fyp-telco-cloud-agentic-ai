import yaml
import time
from infra.k8s_client import K8sClient
from kubernetes.client.rest import ApiException

def main():
    print("Testing config deployment to remote cluster...")
    client = K8sClient()
    
    # Simulate an agent-generated manifest (a simple ConfigMap)
    manifest_yaml = """
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: agent-test-config
      namespace: free5gc
      labels:
        managed-by: oss-gpt
    data:
      test_key: "Deploying configs from agent successfully!"
      generated_at: "2026-04-08"
    """
    manifest_dict = yaml.safe_load(manifest_yaml)
    
    # 1. Apply Dry Run
    print("\n--- 1. Dry Run Application ---")
    result_dry = client.apply_manifest(manifest=manifest_dict, namespace="free5gc", dry_run=True)
    print(f"Dry Run Result: {result_dry}")
    
    # 2. Live Apply
    print("\n--- 2. Live Application ---")
    result_live = client.apply_manifest(manifest=manifest_dict, namespace="free5gc", dry_run=False)
    print(f"Live Apply Result: {result_live}")
    
    # 3. Verify it exists
    print("\n--- 3. Verifying ConfigMap exists on cluster ---")
    try:
        cm = client._core.read_namespaced_config_map(name="agent-test-config", namespace="free5gc")
        print(f"Successfully retrieved ConfigMap 'agent-test-config'.")
        print(f"Data: {cm.data}")
    except ApiException as e:
        print(f"Failed to read ConfigMap: {e}")
        
    # 4. Clean up
    print("\n--- 4. Cleaning up ---")
    try:
        client._core.delete_namespaced_config_map(name="agent-test-config", namespace="free5gc")
        print("Successfully deleted test ConfigMap.")
    except ApiException as e:
        print(f"Failed to delete ConfigMap: {e}")

if __name__ == "__main__":
    main()
