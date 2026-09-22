"""
Run celery beat on exactly one instance of the fleet.

Every instance starts a beat container, but only the one holding the Redis
lease actually schedules. The others wait and take over if the holder dies.
Two beats would dispatch every scheduled task twice — the alert engine's
deduplication would absorb most of it, but SMS reminders are money and
patience, and a safety net is not a licence to lean on it.

The lease is a plain SET NX with a TTL, refreshed at a third of its length.
If a refresh finds the lease belongs to someone else (a network partition
healed the wrong way), beat is stopped immediately: a missed schedule beat
is recoverable, a double one is not.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

import redis

LEASE_KEY = "mmdsa:beat:lease"
LEASE_TTL_SECONDS = 90
REFRESH_SECONDS = 30

HOLDER = f"{socket.gethostname()}:{os.getpid()}"


def main() -> int:
    client = redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"), socket_timeout=5
    )

    beat: subprocess.Popen | None = None
    try:
        while True:
            if beat is None:
                if client.set(LEASE_KEY, HOLDER, nx=True, ex=LEASE_TTL_SECONDS):
                    print(f"Beat lease acquired by {HOLDER}.", flush=True)
                    beat = subprocess.Popen(
                        ["celery", "-A", "config", "beat", "--loglevel=INFO"]
                    )
            else:
                holder = client.get(LEASE_KEY)
                if holder is None or holder.decode() != HOLDER:
                    print("Beat lease lost. Stopping the scheduler.", flush=True)
                    beat.terminate()
                    beat.wait(timeout=30)
                    return 1
                client.expire(LEASE_KEY, LEASE_TTL_SECONDS)
                if beat.poll() is not None:
                    # The scheduler died on its own; release so another
                    # instance can take over without waiting out the TTL.
                    client.delete(LEASE_KEY)
                    return beat.returncode or 1
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        return 0
    finally:
        if beat is not None and beat.poll() is None:
            beat.send_signal(signal.SIGTERM)
            beat.wait(timeout=30)
        try:
            if client.get(LEASE_KEY) == HOLDER.encode():
                client.delete(LEASE_KEY)
        except redis.RedisError:
            pass


if __name__ == "__main__":
    sys.exit(main())
