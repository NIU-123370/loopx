from __future__ import annotations

# New lightweight research kernel. Keep this file small: user-facing
# auto-research additions should stay in core/kernel and wrap generic runners.
from .kernel import (
    LIGHTWEIGHT_AUTO_RESEARCH_EVIDENCE_SCHEMA_VERSION,  # noqa: F401
    LIGHTWEIGHT_AUTO_RESEARCH_HYPOTHESIS_SCHEMA_VERSION,  # noqa: F401
    LIGHTWEIGHT_AUTO_RESEARCH_RESULT_SCHEMA_VERSION,  # noqa: F401
    lightweight_hypothesis,  # noqa: F401
    run_lightweight_auto_research,  # noqa: F401
)
