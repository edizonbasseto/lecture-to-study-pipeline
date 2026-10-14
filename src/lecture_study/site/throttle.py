"""Abuse limits for password checking (SITE-04).

Every password check costs one PBKDF2 pass per allowlisted person, and
both doors that trigger it -- the login form and Basic Auth -- are open
to anyone. Two distinct limits guard two distinct failures, and neither
may be turnable against the person the site exists for.

`AttemptDelay` slows repeated attempts on one address down instead of
locking that address out. A lockout is aimed as easily at the rightful
owner as at an attacker: wrong guesses anyone can make would keep her out
of her own library forever, and there is no password reset to recover
through. A growing wait costs an attacker throughput while the person who
simply mistyped waits seconds and gets in. Waits are counted per address
attempted, never per network address, because behind the TLS proxy every
visitor shares one peer address and an IP-keyed counter would punish the
whole audience.

The wait an attempt pays is claimed when the attempt starts, not when it
fails. Charging on failure alone let a burst all read the same wait at
the same moment, sleep through it together and check in parallel, paying
one wait between them instead of one each -- measured at 27.7 guesses a
second against a single address. Claiming up front makes the nth attempt
in a burst pay the nth wait, without any per-address gate an attacker
could hold shut against its owner.

`PasswordCheckLimiter` caps how many checks run at once and refuses once
the queue behind them is longer than the wait is worth. That is what
keeps a flood from saturating the worker, and unlike a lockout it cannot
be aimed at one person: it sheds load for the moment of the flood only.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

FREE_ATTEMPTS = 3
WINDOW_SECONDS = 300.0
FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
MAX_CONCURRENT_CHECKS = 2
MAX_QUEUED_CHECKS = 8
MAX_TRACKED_ACCOUNTS = 1024
MAX_WAITING_ATTEMPTS = 24


class AttemptDelay:
    """How long one address must wait before its next password check."""

    def __init__(
        self,
        free_attempts: int | None = None,
        window_seconds: float | None = None,
        first_delay_seconds: float | None = None,
        max_delay_seconds: float | None = None,
        max_tracked_accounts: int | None = None,
    ) -> None:
        # Resolved here rather than as argument defaults, which bind once at
        # import and so cannot be adjusted for a test that must not really
        # sleep for half a minute.
        self._free_attempts = FREE_ATTEMPTS if free_attempts is None else free_attempts
        self._window_seconds = (
            WINDOW_SECONDS if window_seconds is None else window_seconds
        )
        self._first_delay_seconds = (
            FIRST_DELAY_SECONDS if first_delay_seconds is None else first_delay_seconds
        )
        self._max_delay_seconds = (
            MAX_DELAY_SECONDS if max_delay_seconds is None else max_delay_seconds
        )
        self._max_tracked_accounts = (
            MAX_TRACKED_ACCOUNTS
            if max_tracked_accounts is None
            else max_tracked_accounts
        )
        self._failures: dict[str, deque[float]] = {}

    def seconds_before(self, account: str, now: float | None = None) -> float:
        """The wait this address has earned: none until the free attempts
        are spent, then doubling up to the cap.

        A question, not an instruction: asking must not discard the very
        history it reports on, so nothing is pruned here.
        """
        history = self._failures.get(account)
        if history is None:
            return 0.0
        horizon = (time.time() if now is None else now) - self._window_seconds
        recent = sum(1 for failure in history if failure >= horizon)
        wasted = recent - self._free_attempts
        if wasted <= 0:
            return 0.0
        return min(
            self._first_delay_seconds * (2.0 ** (wasted - 1)), self._max_delay_seconds
        )

    def claim(self, account: str, now: float | None = None) -> float:
        """Charge this attempt its wait and return it, counting the attempt
        immediately so that attempts running side by side each pay their
        own place in the queue rather than sharing one."""
        seconds = self.seconds_before(account, now)
        self.record_failure(account, now)
        return seconds

    def record_failure(self, account: str, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        history = self._failures.get(account)
        if history is None:
            self._evict_until_there_is_room(moment)
            history = deque()
            self._failures[account] = history
        self._prune(history, moment).append(moment)

    def forget(self, account: str) -> None:
        """Drop an address's history, so a successful sign-in starts clean."""
        self._failures.pop(account, None)

    def _prune(self, history: deque[float], now: float | None) -> deque[float]:
        horizon = (time.time() if now is None else now) - self._window_seconds
        while history and history[0] < horizon:
            history.popleft()
        return history

    def _evict_until_there_is_room(self, now: float) -> None:
        """Keep the table bounded by dropping the address with the least
        evidence against it, never by clearing the table wholesale.

        Clearing would let an attacker name a thousand throwaway addresses
        to wipe the history of the one address actually under attack, and
        so reset the wait he had earned. Dropping by recency alone would
        too: he would simply stop guessing for a moment and flood. The
        address with the most failures is the one worth remembering, so
        buying back a reset costs as many failures as were already spent.
        """
        if len(self._failures) < self._max_tracked_accounts:
            return
        horizon = now - self._window_seconds
        weakest = min(
            self._failures,
            key=lambda account: sum(
                1 for failure in self._failures[account] if failure >= horizon
            ),
        )
        del self._failures[weakest]


class WaitingRoom:
    """Caps how many attempts may be waiting out their delay at once.

    A sleeping request holds a connection, so the waits themselves must be
    bounded or they become the attack. The bound is deliberately **global
    and never per address**: anything an attacker can exhaust by naming
    somebody else's address is a way to lock that person out, which is the
    one outcome this whole mechanism exists to avoid. Filling a global room
    costs an attacker sustained connections and degrades everyone equally
    for as long as the flood lasts, rather than singling out the one person
    who must get in.
    """

    def __init__(self, max_waiting: int | None = None) -> None:
        self._max_waiting = MAX_WAITING_ATTEMPTS if max_waiting is None else max_waiting
        self._waiting = 0

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def room_for_one(self) -> AsyncIterator[None]:
        """Take a place in the room, or raise TooManyAttempts.

        The place is taken before the first await, so a burst arriving
        together cannot all pass a check none of them has paid for yet.
        """
        if self._waiting >= self._max_waiting:
            raise TooManyAttempts
        self._waiting += 1
        try:
            yield
        finally:
            self._waiting -= 1


class PasswordCheckLimiter:
    """Bounds how many password checks may be admitted -- running or
    queued -- and how many of those run at once.

    Admission is a synchronous reservation, taken before anything is
    awaited. A concurrent flood cannot outrun a check that only becomes
    true once earlier attempts reach an await: every coroutine in a burst
    would see the count as it was before any of them suspended, and all of
    them would slip through together. Reserving synchronously means the
    Nth attempt in a burst sees exactly what the N-1 before it reserved.
    """

    def __init__(
        self, max_concurrent: int | None = None, max_queued: int | None = None
    ) -> None:
        # Resolved here, not as argument defaults: a default value binds
        # once at class definition and a test that must shrink it cannot
        # reach it through the module constant afterwards.
        resolved_concurrent = (
            MAX_CONCURRENT_CHECKS if max_concurrent is None else max_concurrent
        )
        self._semaphore = asyncio.Semaphore(resolved_concurrent)
        self._max_admitted = MAX_QUEUED_CHECKS if max_queued is None else max_queued
        self._admitted = 0

    def reserve(self) -> None:
        """Claim a place among those running or queued, or raise
        TooManyAttempts. Synchronous and unconditional: nothing here may
        wait, or a burst could all pass the check before any of them
        claims a place."""
        if self._admitted >= self._max_admitted:
            raise TooManyAttempts
        self._admitted += 1

    def release(self) -> None:
        self._admitted -= 1

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Actually run, once a place has been reserved: bounded to
        `max_concurrent` at a time, queueing behind the semaphore for the
        rest of the reserved admission."""
        async with self._semaphore:
            yield


class TooManyAttempts(RuntimeError):
    """Raised when password checking is saturated and must shed load."""


LOG_SUPPRESSION_SECONDS = 5.0


class SuppressingLogGate:
    """Lets one flavour of event through to the log at most once per
    window, counting what it swallowed.

    Shedding a flood costs the caller nothing -- no PBKDF2, no sleep --
    so nothing upstream limits how often it can be logged, and a flood
    logging once per rejection can bury the sign-ins and real refusals
    the log exists to show.
    """

    def __init__(self, window_seconds: float = LOG_SUPPRESSION_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._last_logged: float | None = None
        self._suppressed = 0

    def should_log(self, now: float | None = None) -> tuple[bool, int]:
        """Whether this occurrence should be logged, and how many were
        swallowed since the last one that was."""
        moment = time.time() if now is None else now
        if self._last_logged is not None and moment - self._last_logged < (
            self._window_seconds
        ):
            self._suppressed += 1
            return False, 0
        swallowed = self._suppressed
        self._last_logged = moment
        self._suppressed = 0
        return True, swallowed
