"""Backup and restore child lifecycles on the server's event loop: the same tests as on the default loop.

Starting, timing out, cancelling, monitoring (including a failing disk probe), stopping an unstoppable child,
private files, and workspace cleanup and retention, for children alone and through backup and restore.
Synchronous tests do not touch the loop and are left to their own modules.
"""

import inspect

from tests.test_backup_resources import *  # noqa: F401,F403
from tests.test_backups import *  # noqa: F401,F403
from tests.test_restore_admission import *  # noqa: F401,F403
from tests.test_restore_preflight import *  # noqa: F401,F403

for _name, _test in list(globals().items()):
    if _name.startswith("test_") and not inspect.iscoroutinefunction(_test):
        del globals()[_name]
