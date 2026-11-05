"""One real-browser window for many captcha widgets.

A release often has dozens of links on one host, each needing its OWN token
(Turnstile tokens work once and expire after about five minutes). Starting an
Edge per link would fight over the same devtools port, so every widget solve
goes through here:

* links that need a widget token are collected per (host, kind, sitekey) for a
  short window (``window`` seconds);
* ONE page then renders as many widgets as links are waiting
  (``browser_solver.solve_widgets_in_browser``) and every token goes to exactly
  one waiting link as it arrives;
* only one browser runs at a time (``browser_lock``); links that start waiting
  while a batch runs join the next batch, and a batch holds at most
  ``max_batch`` links;
* the "Solve in browser" button and the clearance solver take the same lock.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("opengrab.captcha.batcher")

Key = Tuple[str, str, str]


class _Waiter:
    __slots__ = ("event", "token")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.token = ""


class _Group:
    def __init__(self, page_url: str) -> None:
        self.page_url = page_url
        self.waiting: List[_Waiter] = []


class WidgetBatcher:
    """solve_batch(page_url, kind, sitekey, count, on_token, hidden) renders the
    widgets and calls on_token(token) once per token; it may return early."""

    def __init__(self, solve_batch: Callable[..., object], window: float = 1.5,
                 max_batch: int = 40, fail_cooldown: float = 30.0,
                 wait_cap: float = 1800.0, clock=time.monotonic) -> None:
        self.solve_batch = solve_batch
        self.window = window
        self.max_batch = max(1, int(max_batch))
        self.fail_cooldown = fail_cooldown
        self.wait_cap = wait_cap
        self._clock = clock
        self.browser_lock = threading.Lock()      # one browser at a time
        self._mx = threading.Lock()               # guards the groups
        self._groups: Dict[Key, _Group] = {}
        self._failed_at: Dict[Key, float] = {}
        self.batches: List[Tuple[Key, int, int]] = []   # (key, links, tokens): history for diagnostics
        self.running = 0                          # batches running right now

    # -- one link asks for a token -------------------------------------------
    def request(self, host: str, kind: str, sitekey: str, page_url: str) -> str:
        """Block until this link has its own token; "" when none could be made."""
        key: Key = (host, kind, sitekey)
        w = _Waiter()
        with self._mx:
            failed = self._failed_at.get(key)
            if failed is not None and self._clock() - failed < self.fail_cooldown:
                return ""            # the last batch got nothing: park for a human instead of retrying at once
            g = self._groups.get(key)
            start = g is None
            if start:
                g = self._groups[key] = _Group(page_url)
            g.waiting.append(w)
        if start:
            threading.Thread(target=self._collect, args=(key,), daemon=True,
                             name="captcha-batch").start()
        w.event.wait(self.wait_cap)
        return w.token

    def waiting(self) -> int:
        with self._mx:
            return sum(len(g.waiting) for g in self._groups.values())

    # -- the collector: one thread per key while it has waiters -------------------
    def _collect(self, key: Key) -> None:
        try:
            while True:
                time.sleep(self.window)
                with self.browser_lock:
                    with self._mx:
                        g = self._groups[key]
                        batch = g.waiting[:self.max_batch]
                        del g.waiting[:len(batch)]
                        if not batch:
                            del self._groups[key]
                            return
                    self._run(key, g.page_url, batch)
        except BaseException:   # noqa: BLE001 - never leave a link waiting forever
            log.exception("captcha batch collector failed")
            with self._mx:
                g = self._groups.pop(key, None)
            for w in (g.waiting if g else []):
                w.event.set()
            raise

    def _run(self, key: Key, page_url: str, batch: List[_Waiter]) -> None:
        pending = list(batch)
        given = [0]

        def on_token(token: str) -> None:
            with self._mx:
                if not pending:
                    return           # more tokens than links: the extra one is dropped, never reused
                w = pending.pop(0)
                given[0] += 1
            w.token = token
            w.event.set()

        self.running += 1
        try:
            self.solve_batch(page_url, key[1], key[2], len(batch), on_token, True)
        except Exception as exc:  # noqa: BLE001 - BrowserSolverUnavailable, bad sitekey, ...
            log.info("captcha batch for %s failed: %s", key[0], exc)
        finally:
            self.running -= 1
            with self._mx:
                self.batches.append((key, len(batch), given[0]))
                del self.batches[:-50]
                if given[0] == 0:
                    self._failed_at[key] = self._clock()
                else:
                    self._failed_at.pop(key, None)
                left = list(pending)
            for w in left:           # these links get no token and fall back to a human
                w.event.set()

    # -- the user asked: show the window and solve for known links ----------------
    def run_now(self, page_url: str, kind: str, sitekey: str, count: int,
                on_token: Callable[[str], None], hidden: bool = False) -> int:
        """Run one batch of `count` widgets at once (waits for the browser lock).
        Returns how many tokens were handed to on_token."""
        got = [0]

        def counted(token: str) -> None:
            got[0] += 1
            on_token(token)

        with self.browser_lock:
            self._failed_at.pop((urlhost(page_url), kind, sitekey), None)
            self.solve_batch(page_url, kind, sitekey, count, counted, hidden)
        return got[0]


def urlhost(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or ""
