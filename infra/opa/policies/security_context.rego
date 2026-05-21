# Flag containers running as root (runAsUser=0) or with privileged: true.
# Severity WARNING — OAI charts run as root by design, so this is informational.
# Uses the legacy rule name "root_containers" to keep ValidationReport keys stable.

package fyp

import rego.v1

violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.podSecurityContext.runAsUser == 0
	v := {
		"rule": "root_containers",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: runs as root (runAsUser=0)", [cfg.vnf_name]),
	}
}

violations contains v if {
	some cfg in input.config_artifacts
	cfg.helm_values.securityContext.privileged == true
	v := {
		"rule": "root_containers",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: privileged container", [cfg.vnf_name]),
	}
}
