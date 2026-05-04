"""Importing this package registers all baseline runners.

Each module side-effect-registers its runner via experiments.common.runners.register
so that experiments.common.runners.RUNNERS holds every system after this import.
"""

from experiments.baselines import b1_manual      # noqa: F401
from experiments.baselines import b2_osm_stub    # noqa: F401
from experiments.baselines import b3_static_hpa  # noqa: F401
from experiments.baselines import b4_single_llm  # noqa: F401
