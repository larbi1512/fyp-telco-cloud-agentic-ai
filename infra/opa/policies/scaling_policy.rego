# Replica-count guardrails. Keeps deployments within a sensible range so an
# LLM-generated topology can't request 50 NRF pods.
# Severity WARNING — won't block GO, but flags drift from defaults.

package fyp

import rego.v1

violations contains v if {
	some cfg in input.config_artifacts
	rc := cfg.helm_values.replicaCount
	rc < 1
	v := {
		"rule": "scaling_policy",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: replicaCount=%d below minimum 1", [cfg.vnf_name, rc]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	rc := cfg.helm_values.replicaCount
	rc > 10
	v := {
		"rule": "scaling_policy",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: replicaCount=%d above maximum 10", [cfg.vnf_name, rc]),
	}
}
