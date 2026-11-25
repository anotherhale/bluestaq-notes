"""Make the ``tools/`` coordination package importable from the test suite.

``nexus_coord`` is an operator tool, not part of the shipped service, so it lives
outside ``src/notes_service`` and is not installed. The tests import it by path.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
