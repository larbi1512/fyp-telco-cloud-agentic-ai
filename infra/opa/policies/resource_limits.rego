# Ensures every VNF declares CPU and memory requests and limits.
# Severity FAIL — missing limits can crash a node under load.

package fyp

import rego.v1

# resources.define must be true. Covers missing `resources`, missing `define`,
# or `define: false`.
violations contains v if {
	some cfg in input.config_artifacts
	not cfg.helm_values.resources.define
	v := {
		"rule": "resource_limits",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: resources.define is False — no resource limits will be applied", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.resources.define == true
	not cfg.helm_values.resources.requests.nf.cpu
	v := {
		"rule": "resource_limits",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: missing resource requests (cpu)", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.resources.define == true
	not cfg.helm_values.resources.requests.nf.memory
	v := {
		"rule": "resource_limits",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: missing resource requests (memory)", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.resources.define == true
	not cfg.helm_values.resources.limits.nf.cpu
	v := {
		"rule": "resource_limits",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: missing resource limits (cpu)", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.resources.define == true
	not cfg.helm_values.resources.limits.nf.memory
	v := {
		"rule": "resource_limits",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: missing resource limits (memory)", [cfg.vnf_name]),
	}
}
