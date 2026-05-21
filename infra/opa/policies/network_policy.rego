# Networking guardrails: flag hostNetwork on non-UPF VNFs (UPF needs it for
# GTP) and Multus configurations with missing default gateway.
# Severity WARNING — informational; the real K8s dry-run catches port clashes.

package fyp

import rego.v1

# hostNetwork mode is a security risk unless this is the UPF.
violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.hostNetwork == true
	not contains(lower(cfg.vnf_name), "upf")
	v := {
		"rule": "network_policy",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: hostNetwork enabled on non-UPF VNF", [cfg.vnf_name]),
	}
}

# Multus enabled but defaultGateway missing or empty — packets will drop.
violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.multus
	object.get(cfg.helm_values.multus, "defaultGateway", "") == ""
	v := {
		"rule": "network_policy",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: multus enabled but defaultGateway is missing or empty", [cfg.vnf_name]),
	}
}
