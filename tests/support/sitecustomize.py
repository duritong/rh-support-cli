"""Enable the inherited guard only for explicitly sandboxed test children."""

import os

if os.environ.get("RH_TEST_SANDBOX_ACTIVE") == "1":
    if __package__:
        from .network_guard import network_guard
    else:
        from network_guard import network_guard

    _guard = network_guard()
    _guard.__enter__()
