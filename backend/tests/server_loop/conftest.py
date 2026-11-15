"""Tests here run on uvloop, the event loop the server runs on (uvicorn uses it when installed).

The rest of the suite runs on the default asyncio loop. Code that starts child processes behaves differently on
uvloop (it refuses some Popen arguments), so its lifecycle tests run here too.
"""

import uvloop


def pytest_asyncio_loop_factories(config, item):
    return {"uvloop": uvloop.new_event_loop}
