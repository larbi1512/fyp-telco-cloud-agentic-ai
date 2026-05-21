# Each VNF must declare a readiness or liveness probe so Kubernetes can
# restart hung pods. Severity WARNING — informational only.

package fyp

import rego.v1

violations contains v if {
	some cfg in input.config_artifacts
	not has_probe(cfg.helm_values)
	v := {
		"rule": "health_probes",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: no readiness or liveness probes", [cfg.vnf_name]),
	}
}

has_probe(hv) if hv.readinessProbe == true

has_probe(hv) if hv.livenessProbe == true

has_probe(hv) if is_object(hv.readinessProbe)

has_probe(hv) if is_object(hv.livenessProbe)
