from __future__ import annotations

import os


# Unit tests exercise both the original local API contracts and anonymous
# behavior explicitly. Keep the test process local unless a test opts in.
os.environ.setdefault("SMP_DEPLOYMENT_MODE", "local")
