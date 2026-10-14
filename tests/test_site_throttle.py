"""Abuse-limit tests (SITE-04): the limits that protect password checking
must cost an attacker throughput without ever being turnable against the
person the site exists for."""

import asyncio

import pytest

from lecture_study.site.throttle import (
    MAX_DELAY_SECONDS,
    AttemptDelay,
    PasswordCheckLimiter,
    SuppressingLogGate,
    TooManyAttempts,
    WaitingRoom,
)


class TestAttemptDelay:
    def test_the_first_attempts_wait_for_nothing(self):
        delay = AttemptDelay(free_attempts=3)

        assert delay.seconds_before("aluna@example.com", now=0.0) == 0.0

        for moment in range(3):
            delay.record_failure("aluna@example.com", now=float(moment))

        assert delay.seconds_before("aluna@example.com", now=3.0) == 0.0

    def test_the_wait_grows_with_each_further_failure(self):
        delay = AttemptDelay(free_attempts=3)

        for moment in range(4):
            delay.record_failure("aluna@example.com", now=float(moment))
        first = delay.seconds_before("aluna@example.com", now=4.0)

        delay.record_failure("aluna@example.com", now=5.0)
        second = delay.seconds_before("aluna@example.com", now=5.0)

        assert first > 0.0
        assert second > first

    def test_the_wait_stops_growing_at_the_cap(self):
        """An unbounded wait would be a lockout by another name."""
        delay = AttemptDelay(free_attempts=3)

        for moment in range(40):
            delay.record_failure("aluna@example.com", now=float(moment))

        assert delay.seconds_before("aluna@example.com", now=40.0) == MAX_DELAY_SECONDS

    def test_the_wait_lapses_once_the_window_passes(self):
        delay = AttemptDelay(free_attempts=3, window_seconds=300.0)

        for moment in range(10):
            delay.record_failure("aluna@example.com", now=float(moment))

        assert delay.seconds_before("aluna@example.com", now=400.0) == 0.0

    def test_signing_in_clears_what_the_address_had_earned(self):
        delay = AttemptDelay(free_attempts=3)
        for moment in range(10):
            delay.record_failure("aluna@example.com", now=float(moment))

        delay.forget("aluna@example.com")

        assert delay.seconds_before("aluna@example.com", now=10.0) == 0.0

    def test_one_address_never_delays_another(self):
        delay = AttemptDelay(free_attempts=3)

        for moment in range(10):
            delay.record_failure("aluna@example.com", now=float(moment))

        assert delay.seconds_before("outra@example.com", now=10.0) == 0.0

    def test_asking_for_the_wait_does_not_discard_the_history(self):
        """A question, not an instruction: reading the wait must not throw
        away the evidence it reports on."""
        delay = AttemptDelay(free_attempts=3, window_seconds=300.0)
        for moment in range(10):
            delay.record_failure("aluna@example.com", now=float(moment))

        delay.seconds_before("aluna@example.com", now=400.0)

        assert delay.seconds_before("aluna@example.com", now=10.0) > 0.0

    def test_a_flood_of_new_addresses_cannot_wipe_the_one_under_attack(self):
        """Clearing the table wholesale let an attacker reset the wait he
        had earned by naming a thousand throwaway addresses."""
        delay = AttemptDelay(free_attempts=3, max_tracked_accounts=16)
        for moment in range(10):
            delay.record_failure("aluna@example.com", now=float(moment))
        earned = delay.seconds_before("aluna@example.com", now=10.0)

        for index in range(200):
            delay.record_failure(f"descartavel{index}@example.com", now=11.0)

        assert delay.seconds_before("aluna@example.com", now=11.0) == earned

    def test_the_table_stays_bounded(self):
        delay = AttemptDelay(free_attempts=3, max_tracked_accounts=16)

        for index in range(200):
            delay.record_failure(f"descartavel{index}@example.com", now=1.0)

        assert len(delay._failures) <= 16


class TestPasswordCheckLimiter:
    def test_reserving_within_the_budget_succeeds(self):
        limiter = PasswordCheckLimiter(max_concurrent=1, max_queued=2)

        limiter.reserve()

        # No exception: the reservation held.

    def test_a_burst_past_the_budget_is_refused_synchronously(self):
        """The check that matters: reservation happens before anything is
        awaited, so a concurrent burst cannot all pass it before any of
        them claims a place. Checking only after an await let a flood of
        24 admitted attempts all slip through together, each paying its
        target's delay budget for a check that was never going to run."""
        limiter = PasswordCheckLimiter(max_concurrent=1, max_queued=2)

        limiter.reserve()
        limiter.reserve()

        with pytest.raises(TooManyAttempts):
            limiter.reserve()

    def test_releasing_frees_the_place_it_held(self):
        limiter = PasswordCheckLimiter(max_concurrent=1, max_queued=1)
        limiter.reserve()

        limiter.release()

        limiter.reserve()  # does not raise: a place was freed

    def test_running_bounds_true_concurrency_to_max_concurrent(self):
        async def scenario() -> int:
            limiter = PasswordCheckLimiter(max_concurrent=2, max_queued=8)
            concurrent = 0
            highest = 0

            async def check() -> None:
                nonlocal concurrent, highest
                limiter.reserve()
                try:
                    async with limiter.run():
                        concurrent += 1
                        highest = max(highest, concurrent)
                        await asyncio.sleep(0)
                        concurrent -= 1
                finally:
                    limiter.release()

            await asyncio.gather(*(check() for _ in range(6)))
            return highest

        assert asyncio.run(scenario()) == 2


class TestWaitingRoom:
    def test_it_admits_up_to_its_size(self):
        async def scenario() -> int:
            room = WaitingRoom(max_waiting=3)
            released = asyncio.Event()
            admitted = 0

            async def attempt() -> None:
                nonlocal admitted
                async with room.room_for_one():
                    admitted += 1
                    await released.wait()

            running = [asyncio.create_task(attempt()) for _ in range(3)]
            await asyncio.sleep(0)
            released.set()
            await asyncio.gather(*running)
            return admitted

        assert asyncio.run(scenario()) == 3

    def test_a_burst_beyond_the_room_is_refused_at_once(self):
        """Sleeping attempts hold a connection, so the waiting itself must
        not become the attack."""

        async def scenario() -> int:
            room = WaitingRoom(max_waiting=3)
            released = asyncio.Event()
            refused = 0

            async def attempt() -> None:
                nonlocal refused
                try:
                    async with room.room_for_one():
                        await released.wait()
                except TooManyAttempts:
                    refused += 1

            running = [asyncio.create_task(attempt()) for _ in range(10)]
            await asyncio.sleep(0)
            released.set()
            await asyncio.gather(*running)
            return refused

        assert asyncio.run(scenario()) == 7

    def test_a_place_is_released_when_an_attempt_fails(self):
        async def scenario() -> int:
            room = WaitingRoom(max_waiting=3)
            for _ in range(5):
                try:
                    async with room.room_for_one():
                        raise RuntimeError("verificação falhou")
                except RuntimeError:
                    pass
            return room.waiting

        assert asyncio.run(scenario()) == 0

    def test_a_cancelled_attempt_gives_its_place_back(self):
        async def scenario() -> int:
            room = WaitingRoom(max_waiting=3)

            async def attempt() -> None:
                async with room.room_for_one():
                    await asyncio.sleep(10)

            running = asyncio.create_task(attempt())
            await asyncio.sleep(0)
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
            return room.waiting

        assert asyncio.run(scenario()) == 0


class TestClaimingAWait:
    def test_attempts_made_side_by_side_each_pay_their_own_wait(self):
        """Charging only on failure let a burst read the same wait at the
        same moment and pay one between them -- measured at 27.7 guesses a
        second against a single address."""
        delay = AttemptDelay(free_attempts=1)

        claimed = [delay.claim("aluna@example.com", now=0.0) for _ in range(5)]

        assert claimed == sorted(claimed)
        assert claimed[0] == 0.0
        assert claimed[-1] > claimed[1]

    def test_claiming_counts_even_when_the_attempt_succeeds(self):
        delay = AttemptDelay(free_attempts=1)

        delay.claim("aluna@example.com", now=0.0)
        delay.claim("aluna@example.com", now=0.0)

        assert delay.seconds_before("aluna@example.com", now=0.0) > 0.0


class TestSuppressingLogGate:
    def test_the_first_occurrence_logs(self):
        gate = SuppressingLogGate(window_seconds=5.0)

        should_log, swallowed = gate.should_log(now=0.0)

        assert should_log is True
        assert swallowed == 0

    def test_occurrences_inside_the_window_are_swallowed_and_counted(self):
        gate = SuppressingLogGate(window_seconds=5.0)
        gate.should_log(now=0.0)

        for moment in (1.0, 2.0, 3.0):
            should_log, _ = gate.should_log(now=moment)
            assert should_log is False

        should_log, swallowed = gate.should_log(now=6.0)
        assert should_log is True
        assert swallowed == 3
