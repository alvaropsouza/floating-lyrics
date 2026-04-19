"""
Compatibility entrypoint for the canonical headless backend server.

The Qt-based server path was duplicating the same backend responsibilities as
``main_server_headless.py``. Keep this file as a stable entrypoint, but route
execution to the headless server so there is only one maintained server
implementation.
"""

from main_server_headless import main


if __name__ == "__main__":
    raise SystemExit(main())
