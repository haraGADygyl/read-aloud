#!/usr/bin/env python3
"""Tests for the daemon's queue — who waits, who gets cut off.

    python3 -m unittest discover tests

kokorod runs under the venv, so numpy is stubbed out: nothing here touches the
model, the socket or the speakers. The worker thread is replaced by one that
does nothing, and `current` is set by hand to stand for a reply in flight.
"""

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.modules.setdefault("numpy", types.ModuleType("numpy"))

import kokorod  # noqa: E402


def job(session, same="queue", label="", text="hello"):
    return kokorod.Job({"session": session, "same": same,
                        "label": label, "text": text})


class Quiet(kokorod.Speaker):
    """A Speaker whose worker never runs, so submit() can be read directly."""

    def __init__(self):
        kokorod.Speaker.__init__(self, kokoro=None)

    def _worker(self):
        pass

    def speaking(self, session, label=""):
        self.current = job(session, label=label)
        if label:
            self.seen_labels.add(label)
        return self


class SameSession(unittest.TestCase):
    """A run of subagents reports back as several replies from one session.
    Each one used to cut off the last, so only the final one was ever heard."""

    def test_queue_lets_the_first_reply_finish(self):
        sp = Quiet().speaking("s1")
        self.assertEqual(sp.submit(job("s1", same="queue")), "queued")
        self.assertFalse(sp.cancel, "cut off a reply it was told to queue")
        self.assertEqual(len(sp.queue), 1)

    def test_queue_keeps_every_reply_in_order(self):
        sp = Quiet().speaking("s1")
        for n in ("second", "third"):
            sp.submit(job("s1", same="queue", text=n))
        self.assertEqual([j.text for j in sp.queue], ["second", "third"])

    def test_interrupt_is_still_available(self):
        sp = Quiet().speaking("s1")
        sp.submit(job("s1", same="interrupt", text="second"))
        self.assertTrue(sp.cancel)
        self.assertEqual([j.text for j in sp.queue], ["second"])

    def test_interrupt_drops_this_session_s_waiting_replies(self):
        sp = Quiet().speaking("s1")
        sp.submit(job("s2", same="interrupt", text="other"))
        sp.submit(job("s1", same="interrupt", text="newest"))
        self.assertEqual([j.text for j in sp.queue], ["other", "newest"])

    def test_the_default_for_a_reply_that_does_not_say_is_interrupt(self):
        # `read-aloud read` and `again` send no --same; asking twice has
        # always restarted rather than queued, and still does.
        sp = Quiet().speaking("cli")
        sp.submit(kokorod.Job({"session": "cli", "text": "again"}))
        self.assertTrue(sp.cancel)


class OtherSessions(unittest.TestCase):
    """mode governs other terminals only — it must not reach past a session's
    own choice, or a run of subagents is talked over by mode=interrupt."""

    def test_queue_mode_still_queues_another_terminal(self):
        sp = Quiet().speaking("s1", label="api")
        self.assertEqual(sp.submit(job("s2", label="web"), "queue"), "queued")
        self.assertFalse(sp.cancel)

    def test_interrupt_mode_still_cuts_off_another_terminal(self):
        sp = Quiet().speaking("s1")
        sp.submit(job("s2", text="mine"), "interrupt")
        self.assertTrue(sp.cancel)
        self.assertEqual([j.text for j in sp.queue], ["mine"])

    def test_interrupt_mode_does_not_cut_off_a_session_that_queues(self):
        sp = Quiet().speaking("s1")
        sp.submit(job("s1", same="queue", text="second"), "interrupt")
        self.assertFalse(sp.cancel, "mode reached past sameSession")
        self.assertEqual([j.text for j in sp.queue], ["second"])

    def test_drop_mode_still_skips_another_terminal(self):
        sp = Quiet().speaking("s1")
        self.assertEqual(sp.submit(job("s2"), "drop"), "dropped")

    def test_drop_mode_does_not_skip_a_session_s_own_reply(self):
        sp = Quiet().speaking("s1")
        self.assertEqual(sp.submit(job("s1", same="queue")), "queued")

    def test_nothing_speaking_means_nothing_to_drop(self):
        sp = Quiet()
        self.assertEqual(sp.submit(job("s2"), "drop"), "queued")


class Cap(unittest.TestCase):
    def test_the_oldest_waiting_reply_is_the_one_dropped(self):
        sp = Quiet().speaking("s1")
        over = 2
        for n in range(kokorod.MAX_QUEUE + over):
            sp.submit(job("s1", same="queue", text=str(n)))
        self.assertEqual([j.text for j in sp.queue],
                         [str(n) for n in range(over, kokorod.MAX_QUEUE + over)])

    def test_a_run_of_subagents_fits(self):
        # Six agents reporting back while the first is still being read: the
        # cap is what decides whether the earliest of them survives.
        sp = Quiet().speaking("s1")
        for n in range(6):
            sp.submit(job("s1", same="queue", text=str(n)))
        self.assertEqual(len(sp.queue), 6)


if __name__ == "__main__":
    unittest.main()
