"""Run a scenario with a bound that does not depend on cancellation.

`asyncio.run` waits for its main coroutine without limit, and on the way out
it cancels every task left and waits for them, also without limit. A task
that swallows its cancellation hangs both: a suite worker then sits idle in
the event loop until someone kills it, and the faulthandler dump shows only
`asyncio.run`, not the task that is stuck.

`run_bounded` gives the scenario `timeout` seconds. If it is not done, the
test fails with every task's stack, and the loop is abandoned after a short,
bounded attempt to cancel what is left instead of waiting on it forever.
"""

from __future__ import annotations

import asyncio
import io

import pytest

CLEANUP_SECONDS = 10.0


def _stacks(loop) -> str:
    out = io.StringIO()
    for task in asyncio.all_tasks(loop):
        out.write(f"\n--- {task!r}\n")
        task.print_stack(limit=30, file=out)
    return out.getvalue()


async def _finish(tasks, seconds):
    if tasks:
        await asyncio.wait(tasks, timeout=seconds)


def run_bounded(main, *, timeout: float = 120.0):
    """`asyncio.run(main)`, failing the test after `timeout` seconds."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        task = loop.create_task(main)
        loop.run_until_complete(_finish({task}, timeout))
        stuck = None if task.done() else _stacks(loop)
        # As asyncio.run does, cancel what is left -- but give it a bound.
        left = {t for t in asyncio.all_tasks(loop) if not t.done()}
        for t in left:
            t.cancel()
        loop.run_until_complete(_finish(left, CLEANUP_SECONDS))
        unfinished = {t for t in left if not t.done()}
        if stuck is not None:
            pytest.fail(f"The scenario did not finish within {timeout:g} s; {len(unfinished)} task(s) did not "
                        f"end when cancelled. Tasks when the bound ran out:\n{stuck}", pytrace=False)
        if unfinished:
            # asyncio.run would hang here, waiting on these.
            pytest.fail(f"{len(unfinished)} task(s) left by the scenario did not end within "
                        f"{CLEANUP_SECONDS:g} s of being cancelled:\n{_stacks(loop)}", pytrace=False)
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(_finish({loop.create_task(loop.shutdown_default_executor())}, CLEANUP_SECONDS))
        return task.result()
    finally:
        asyncio.set_event_loop(None)
        loop.close()
