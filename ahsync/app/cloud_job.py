"""The cloud market's poll job: a newer published summary, then the re-plan and the addon files (SPEC §2.4, BZ17).

Every `[cloud] poll_minutes` the scheduler calls `poll_cloud`. For each cloud market it asks for a newer summary
(collect.cloud_intel.poll); new documents are staged and published by one re-plan (app.refresh.replan_all), and
only then remembered, so a re-plan that fails fetches them again at the next poll. The addon files are rewritten
after a new document, so hourly prices reach the game at the next /reload.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from ahsync.app.refresh import replan_all
from ahsync.clock import Clock
from ahsync.collect import cloud_intel
from ahsync.collect.client import Transport, UrllibTransport, no_redirect_opener
from ahsync.config import Config, is_cloud_market
from ahsync.db.sqlite import Database
from ahsync.export.addon_files import export_market, export_plan

log = logging.getLogger("ahsync.cloud")


def cloud_transport(transport: Transport) -> Transport:
    """The transport the summary is read through: bounded and never following a redirect (SEC16).

    Args:
        transport: The app's transport (a UrllibTransport in production, a fake in tests).

    Returns:
        A no-redirect UrllibTransport bounded at cloud_intel.MAX_BYTES when given the real one; the fake as is.
    """
    if isinstance(transport, UrllibTransport):
        return UrllibTransport(opener=no_redirect_opener(), max_bytes=cloud_intel.MAX_BYTES)
    return transport


def poll_cloud(db: Database, transport: Transport, clock: Clock, config: Config) -> list[str]:
    """One poll of every cloud market; new documents re-plan and rewrite the addon files (SPEC §2.4, BZ17).

    Args:
        db: Database.
        transport: The summary's transport (cloud_transport).
        clock: Time source.
        config: The running settings.

    Returns:
        The markets that got a new document and were re-planned with it; [] when there was none, or the re-plan
        failed (then nothing is remembered, and the next poll fetches them again).
    """
    now = clock.now_ms()
    found = {m: cloud_intel.poll(db, transport, config.cloud.intel_url, m, now)
             for m in config.markets if is_cloud_market(config, m)}
    bodies = {m: r.body for m, r in found.items() if r.state == "new" and r.body is not None}
    new = {m: replace(r, body=None) for m, r in found.items() if r.state == "new"}
    del found                                        # replan_all drops each gzip once stored (BZ43)
    if not new:
        return []
    replanned = replan_all(db, now, config, clock.local_offset_ms(now), documents={m: r.intel for m, r in new.items()},
                           bodies=bodies)
    done = [m for m in sorted(new) if m in replanned]
    for market in done:
        cloud_intel.remember(db, market, new[market].etag, new[market].sha256, now)
    if done:
        log.info("market summary published for %s; re-planned", ", ".join(done))
        export_market(db, config, now, clock.sleep)
        export_plan(db, config, now, clock.sleep)
    return done
