"""Solver registry / chain with failover.

Priority order (lower number = earlier) is configurable per run.  The
default 'auto' chain:  ddddocr -> tesseract (opt-in) -> automatic paid solvers (2captcha, capmonster, capsolver, nopecha)
(only if a key is configured) -> manual (UI).
The engine uses the chain for OCR-able kinds and always falls back to
'manual' so a captcha can never be a dead end.
"""
from __future__ import annotations

import logging

from .base import (
    BaseSolver, CaptchaRequest, CaptchaResult,
    ManualSolver, DdddOCRSolver, TwoCaptchaSolver,
    CapMonsterSolver, CapSolverSolver, NopeCHASolver,
    TesseractSolver,
)
from ..settings import Settings

log = logging.getLogger("opengrab.captcha.chain")


class SolverChain:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client
        self.solvers: list[BaseSolver] = [
            DdddOCRSolver(settings),
            TesseractSolver(settings),
            ManualSolver(settings),
            TwoCaptchaSolver(settings),
            CapMonsterSolver(settings),
            CapSolverSolver(settings),
            NopeCHASolver(settings),
        ]

    # -- selection -----------------------------------------------------------
    def _ordered(self, kind: str) -> list[BaseSolver]:
        pref = (self.settings.captcha_provider or "auto").lower()
        enabled = []
        for s in self.solvers:
            if kind not in s.kinds:
                continue
            try:
                if not s.enabled():
                    continue
            except Exception:
                continue
            enabled.append(s)
        
        # For auto mode, we need to reorganize to put paid solvers first
        # and manual solver always last (even if preferred is 'manual')
        if pref == "auto":
            # Separate solvers into automatic (paid) and manual solver
            auto_solvers = [s for s in enabled if s.id != "manual"]
            manual_solver = [s for s in enabled if s.id == "manual"]
            
            # Sort automatic solvers by priority (lower number = earlier)
            auto_solvers_sorted = sorted(auto_solvers, key=lambda s: s.priority)
            
            # Manual solver always goes last  
            ordered = auto_solvers_sorted + manual_solver
        else:
            # For non-auto mode, keep current behaviour but ensure manual is still last if preferred is not manual
            preferred = [s for s in enabled if s.id == pref]
            others = [s for s in enabled if s.id != pref]
            
            # If preferred isn't manual, put manual at the end
            # If preferred IS manual, then it should be first (current behavior)  
            if pref != "manual":
                ordered = preferred + others
                # Ensure manual is last if present
                manual_solver = [s for s in ordered if s.id == "manual"]
                other_solvers = [s for s in ordered if s.id != "manual"]
                ordered = other_solvers + manual_solver
            else:
                ordered = preferred + others
                
        return ordered

    # -- solving ---------------------------------------------------------------
    def solve(self, req: CaptchaRequest, allowed: list[str] | None = None,
              timeout_s: float | None = None) -> CaptchaResult:
        """Try solvers in order; return first ok result.  If only 'manual'
        remains (or manual is chosen), returns a special __MANUAL__ result
        the engine parks the link for.
        """
        timeout_s = timeout_s or self.settings.captcha_manual_timeout_s
        chain = self._ordered(req.kind)
        if allowed:
            chain = [s for s in chain if s.id in allowed] or chain
        if not chain:
            chain = [ManualSolver(self.settings)]
        last: CaptchaResult | None = None
        for s in chain:
            if s.id == "manual" and not self.settings.captcha_auto_failover:
                continue
            try:
                res = s.solve(req, timeout_s=timeout_s, client=self.client) \
                    if s.id != "manual" else s.solve(req, timeout_s=timeout_s)
            except TypeError:
                res = s.solve(req, timeout_s=timeout_s)
            except Exception as e:
                res = CaptchaResult(ok=False, provider=s.id, error=str(e))
            log.info("captcha solver=%s kind=%s ok=%s err=%s",
                     s.id, req.kind, res.ok, res.error)
            if res.ok:
                return res
            last = res
        # fall through: force manual so the human can always finish it
        m = ManualSolver(self.settings)
        return m.solve(req, timeout_s=timeout_s)

    def status(self) -> list[dict]:
        out = []
        for s in self.solvers:
            try:
                on = s.enabled()
            except Exception:
                on = False
            out.append({"id": s.id, "name": s.name, "enabled": on,
                        "kinds": list(s.kinds), "needs_key": s.needs_key,
                        "priority": s.priority})
        return out
