"""A small ASGI app for the real-uvicorn shutdown test (tests/test_shutdown_process.py).

It uses the backend's task owner, request binding, and the lifespan's own shutdown step
(``app.main._stop_owned_work``), without a database. Budgets come from the environment so the
test runs in seconds. Never imported by application code.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app import runtime, startup
from app.middleware.task_owner import TaskOwnerMiddleware

startup.DRAIN_SECONDS = float(os.environ.get("SHUTDOWN_DRAIN", "1"))
startup.CANCEL_SECONDS = float(os.environ.get("SHUTDOWN_CANCEL", "1"))
MARKERS = Path(os.environ["SHUTDOWN_MARKERS"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.main import _stop_owned_work

    owner = runtime.TaskOwner()
    app.state.runtime = owner
    yield
    await _stop_owned_work(owner)
    (MARKERS / "lifespan-finished").write_text("clean" if owner.report and owner.report.clean else "unclean")


app = FastAPI(lifespan=lifespan)
app.add_middleware(TaskOwnerMiddleware)


@app.get("/ready")
async def ready():
    return {"ok": True}


async def _finishing():
    await asyncio.sleep(0.5)
    (MARKERS / "finishing-done").write_text("done")


async def _cooperative():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        (MARKERS / "cooperative-cancelled").write_text("cancelled")
        raise


async def _stubborn():
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            continue


JOBS = {"finishing": _finishing, "cooperative": _cooperative, "stubborn": _stubborn}


@app.post("/jobs/{kind}")
async def start(kind: str):
    runtime.spawn(JOBS[kind](), role=kind)
    return {"started": kind}
