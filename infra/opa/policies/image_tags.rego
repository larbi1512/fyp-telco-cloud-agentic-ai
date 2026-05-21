# Discourage non-reproducible image tags. Pinned versions only.
# Severity WARNING — won't block GO, but flags supply-chain risk.

package fyp

import rego.v1

bad_tags := {"latest", "develop", ""}

violations contains v if {
	some cfg in input.config_artifacts
	tag := object.get(cfg.helm_values.nfimage, "version", "")
	tag in bad_tags
	v := {
		"rule": "image_tags",
		"severity": "WARNING",
		"vnf": cfg.vnf_name,
		"message": sprintf("%s: using non-pinned image tag '%s'", [cfg.vnf_name, tag]),
	}
}
