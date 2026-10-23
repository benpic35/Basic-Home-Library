"""Basic-Home-Library - launcher.

Double-click this file (or the .exe built from it) to start the app.
"""

import os
import sys

# Make the bundled package importable no matter where the app is launched from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from library_app.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
