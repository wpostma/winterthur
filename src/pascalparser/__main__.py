"""``python -m pascalparser`` shim.

Lets the package run as a module without going through the installed
entry-point script. Useful in dev when ``uv sync`` / ``pip install``
hasn't been run yet.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
