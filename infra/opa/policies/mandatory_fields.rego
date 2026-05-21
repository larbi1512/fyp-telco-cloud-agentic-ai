# Required Helm value fields for every VNF: nfimage (with repository),
# exposedPorts, and the per-NF start flag block.
# Severity FAIL — these are needed to render a valid chart.

package fyp

import rego.v1

violations contains v if {
	some cfg in input.config_artifacts
	not cfg.helm_values.nfimage
	v := {
		"rule": "mandatory_fields",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: missing nfimage", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.nfimage
	not cfg.helm_values.nfimage.repository
	v := {
		"rule": "mandatory_fields",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: nfimage.repository is empty", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	not cfg.helm_values.exposedPorts
	v := {
		"rule": "mandatory_fields",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: missing exposedPorts", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	not cfg.helm_values.start
	v := {
		"rule": "mandatory_fields",
		"severity": "FAIL",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: missing start flags", [cfg.vnf_name]),
	}
}
