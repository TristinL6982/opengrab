"""CAPTCHA solving framework.

Design goals (vs. JDownloader 2's paywalled captcha service):
  * NO built-in service lock-in — bring your own key for 2Captcha /
    CapMonster / CapSolver, or use free local OCR (ddddocr), or solve
    manually from the UI (the image is pushed to the front-end).
  * A SolverChain tries solvers in a configured priority order and
    fails over automatically (e.g. ddddocr -> manual -> 2captcha).
  * Each solver is a small class; adding a new one is a 20-line file.

Supported kinds:
  text      — classical distorted-text image captcha  (OCR-able)
  image     — generic image captcha                    (OCR attempt + manual)
  recaptcha / hcaptcha / turnstile — token-based        (provider-only or manual)
"""
from __future__ import annotations

import base64
import logging
import time
import urllib.parse
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("opengrab.captcha")


class CaptchaError(Exception):
    pass


@dataclass
class CaptchaRequest:
    """Everything a solver needs to try to solve a captcha."""
    image_b64: str = ""            # PNG/JPEG base64 (no data: prefix)
    image_url: str = ""            # alternative: url the solver may fetch
    kind: str = "text"             # text|image|recaptcha|hcaptcha|turnstile
    site_url: str = ""             # where the captcha was encountered (referer/domain)
    recaptcha_sitekey: str = ""    # for token kinds
    recaptcha_action: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class CaptchaResult:
    ok: bool
    text: str = ""                 # solved text / token
    provider: str = ""
    cost: float = 0.0              # USD-ish, informational
    raw: dict = field(default_factory=dict)
    error: str = ""


class BaseSolver:
    id: str = "base"
    name: str = "Base"
    kinds: tuple[str, ...] = ("text", "image")
    priority: int = 100            # lower = tried first
    needs_key: bool = False

    def __init__(self, settings):
        self.settings = settings

    def enabled(self) -> bool:
        return True

    def solve(self, req: CaptchaRequest, timeout_s: float = 90.0) -> CaptchaResult:
        raise NotImplementedError

    # helpers ---------------------------------------------------------------
    @staticmethod
    def fetch_image_b64(req: CaptchaRequest, client) -> str:
        if req.image_b64:
            return req.image_b64
        if req.image_url and client is not None:
            r = client.get(req.image_url, max_bytes=4_000_000)
            if r.ok and r.content:
                return base64.b64encode(r.content).decode()
        return ""


class ManualSolver(BaseSolver):
    """'Solve' = mark the link as awaiting the human in the UI.

    The engine detects this special provider and parks the link in
    status CAPTCHA with the image pushed to the UI; when the user submits
    text via /api/captcha the link is re-resolved with that text.
    """
    id = "manual"
    name = "Manual (in UI)"
    kinds = ("text", "image", "recaptcha", "hcaptcha", "turnstile")
    priority = 50
    needs_key = False

    def solve(self, req: CaptchaRequest, timeout_s: float = 90.0) -> CaptchaResult:
        return CaptchaResult(ok=True, text="__MANUAL__", provider="manual")


class DdddOCRSolver(BaseSolver):
    """Local OCR (ddddocr). Free, offline, CPU-only, for classic text captchas.

    Spec section 2: off by default and only usable after the user installs
    the "ocr" add-on and turns on ``captcha_ocr_enabled``. Skipped on weak
    hardware unless ``captcha_ocr_ignore_hardware`` is set, and disabled for
    the session when a solve is slower than hardware.SLOW_INFERENCE_MS. The
    model loads on the first captcha and is dropped after IDLE_UNLOAD_S.
    """
    id = "ddddocr"
    name = "Local OCR (ddddocr)"
    kinds = ("text", "image")
    priority = 10
    needs_key = False
    IDLE_UNLOAD_S = 600.0
    _ocr = None
    _last_used = 0.0
    _too_slow = False
    _lock = __import__("threading").Lock()

    def unavailable_reason(self) -> str:
        if not getattr(self.settings, "captcha_ocr_enabled", False):
            return "turned off (opt in under Settings, Captcha)"
        if DdddOCRSolver._too_slow:
            return "disabled for this session: OCR was too slow on this PC"
        if not getattr(self.settings, "captcha_ocr_ignore_hardware", False):
            from .. import hardware
            prof = hardware.profile()
            if prof["weak"]:
                return "skipped on weak hardware (" + ", ".join(prof["reasons"]) + ")"
        from .. import addons
        addons.activate_installed()
        if not addons.importable("ddddocr"):
            return "the Local OCR add-on is not installed"
        return ""

    def enabled(self) -> bool:
        try:
            return not self.unavailable_reason()
        except Exception:
            return False

    def _get_ocr(self):
        with self._lock:
            if DdddOCRSolver._ocr is None:
                import ddddocr  # lazy: heavy import, only after opt-in
                DdddOCRSolver._ocr = ddddocr.DdddOcr(show_ad=False)
                self._schedule_unload()
            DdddOCRSolver._last_used = time.time()
            return DdddOCRSolver._ocr

    @classmethod
    def _schedule_unload(cls) -> None:
        import threading

        def check():
            while True:
                time.sleep(min(60.0, cls.IDLE_UNLOAD_S))
                with cls._lock:
                    if cls._ocr is None:
                        return
                    if time.time() - cls._last_used >= cls.IDLE_UNLOAD_S:
                        cls._ocr = None
                        import gc
                        gc.collect()
                        return

        threading.Thread(target=check, name="ocr-idle-unload", daemon=True).start()

    def solve(self, req: CaptchaRequest, timeout_s: float = 90.0) -> CaptchaResult:
        img_b64 = req.image_b64 or (req.extra.get("image_b64") or "")
        if not img_b64:
            return CaptchaResult(ok=False, provider=self.id,
                                 error="no image to OCR")
        try:
            ocr = self._get_ocr()
            img = base64.b64decode(img_b64)
            t0 = time.time()
            text = ocr.classification(img)
            ms = int((time.time() - t0) * 1000)
            from .. import hardware
            if ms > hardware.SLOW_INFERENCE_MS:
                DdddOCRSolver._too_slow = True
            text = (text or "").strip()
            if not text:
                return CaptchaResult(ok=False, provider=self.id,
                                     error="OCR returned empty")
            return CaptchaResult(ok=True, text=text, provider=self.id,
                                 raw={"ms": ms})
        except Exception as e:
            return CaptchaResult(ok=False, provider=self.id, error=str(e))


class _TwoCaptchaMixin:
    """Shared logic for 2captcha & CapMonster (same REST protocol)."""
    api_base = "https://2captcha.com"

    def _api(self, client, action: str, params: dict) -> dict:
        r = client.get(self.api_base + "/in.php",
                       data={k: str(v) for k, v in params.items()})
        try:
            body = r.text.strip()
            if body.startswith("{"):
                return r.json()
            key, _, val = body.partition("|")
            return {"status": 1 if key == "OK" else 0, "request": val}
        except Exception:
            return {}

    def _get(self, client, request_id: str) -> dict:
        r = client.get(self.api_base + "/res.php",
                       data={"action": "get", "id": request_id})
        body = r.text.strip()
        if body.startswith("{"):
            return r.json()
        if body == "CAPCHA_NOT_READY":
            return {"status": 0, "request": "CAPCHA_NOT_READY"}
        key, _, val = body.partition("|")
        return {"status": 1 if key == "OK" else 0, "request": val}


class TwoCaptchaSolver(BaseSolver, _TwoCaptchaMixin):
    id = "2captcha"
    name = "2Captcha"
    kinds = ("text", "image", "recaptcha", "hcaptcha", "turnstile")
    priority = 100
    needs_key = True
    api_base = "https://2captcha.com"

    def enabled(self) -> bool:
        return bool(self.settings.captcha_2captcha_key)

    def solve(self, req: CaptchaRequest, timeout_s: float = 90.0, client=None) -> CaptchaResult:
        key = self.settings.captcha_2captcha_key
        img_b64 = self.fetch_image_b64(req, client)
        params: dict = {"key": key}
        if req.kind in ("recaptcha", "hcaptcha", "turnstile"):
            kind_map = {"recaptcha": "google", "hcaptcha": "hcaptcha", "turnstile": "turnstile"}
            params.update({"method": "userrecaptcha" if req.kind == "recaptcha" else "post",
                           "googlekey": req.recaptcha_sitekey,
                           "stoken": req.recaptcha_sitekey,
                           "s": req.site_url or "http://localhost",
                           "action": req.recaptcha_action})
            if req.kind == "hcaptcha":
                params.update({"method": "hcaptcha", "hcaptchakey": req.recaptcha_sitekey})
            if req.kind == "turnstile":
                params.update({"method": "turnstile", "tsitekey": req.recaptcha_sitekey})
        elif img_b64:
            params.update({"method": "base64", "body": img_b64})
        else:
            return CaptchaResult(ok=False, provider=self.id, error="nothing to send")

        created = self._api(client, "in", params)
        req_id = str(created.get("request", ""))
        if str(created.get("status", "")) != "1" or not req_id:
            return CaptchaResult(ok=False, provider=self.id,
                                 error=f"submit failed: {created}")
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            time.sleep(3)
            res = self._get(client, req_id)
            status = str(res.get("status", ""))
            val = str(res.get("request", ""))
            if status == "1" and val and val != "CAPCHA_NOT_READY":
                return CaptchaResult(ok=True, text=val, provider=self.id,
                                     cost=0.003, raw={"id": req_id})
        return CaptchaResult(ok=False, provider=self.id,
                             error="timed out waiting for solution")


class CapMonsterSolver(TwoCaptchaSolver):
    """CapMonster: same in.php/res.php protocol, different host + key field."""
    id = "capmonster"
    name = "CapMonster"
    api_base = "https://capmonster.cloud"

    def enabled(self) -> bool:
        return bool(self.settings.captcha_capmonster_key)

    def solve(self, req: CaptchaRequest, timeout_s: float = 90.0, client=None) -> CaptchaResult:
        key = self.settings.captcha_capmonster_key
        if not client:
            from .http_client import HttpClient
            from .settings import Settings
            client = HttpClient(Settings())
        img_b64 = self.fetch_image_b64(req, client)
        payload = {"clientKey": key}
        if req.kind in ("recaptcha",):
            payload.update({"googleKey": req.recaptcha_sitekey,
                            "pageUrl": req.site_url or "http://localhost",
                            "website": "recaptcha"})
        elif req.kind == "hcaptcha":
            payload.update({"websiteKey": req.recaptcha_sitekey,
                            "pageUrl": req.site_url or "http://localhost"})
        elif req.kind == "turnstile":
            payload.update({"websiteKey": req.recaptcha_sitekey,
                            "pageUrl": req.site_url or "http://localhost"})
        elif img_b64:
            payload.update({"body": img_b64, "website": "normal"})
        else:
            return CaptchaResult(ok=False, provider=self.id, error="nothing to send")

        r = client.post(self.api_base + "/in", json_body=payload)
        try:
            created = r.json()
        except Exception:
            return CaptchaResult(ok=False, provider=self.id,
                                 error=f"bad response: {r.text[:200]}")
        req_id = str(created.get("taskId", ""))
        if created.get("errorId"):
            return CaptchaResult(ok=False, provider=self.id,
                                 error=str(created.get("errorCode") or created))
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            time.sleep(3)
            rr = client.post(self.api_base + "/get", json_body={"taskId": req_id})
            try:
                res = rr.json()
            except Exception:
                continue
            if res.get("errorId"):
                continue
            status = res.get("status")
            if status == "ready":
                return CaptchaResult(ok=True,
                                     text=str(res.get("result") or res.get("code") or ""),
                                     provider=self.id, cost=0.004, raw={"id": req_id})
        return CaptchaResult(ok=False, provider=self.id,
                             error="timed out waiting for solution")


class CapSolverSolver(BaseSolver):
    id = "capsolver"
    name = "CapSolver"
    kinds = ("text", "image", "recaptcha", "hcaptcha", "turnstile")
    priority = 100
    needs_key = True

    def enabled(self) -> bool:
        return bool(self.settings.captcha_capsolver_key)

    def solve(self, req: CaptchaRequest, timeout_s: float = 90.0, client=None) -> CaptchaResult:
        key = self.settings.captcha_capsolver_key
        if not client:
            from .http_client import HttpClient
            from .settings import Settings
            client = HttpClient(Settings())
        img_b64 = self.fetch_image_b64(req, client)
        if req.kind in ("recaptcha", "hcaptcha", "turnstile"):
            kind_name = {"recaptcha": "ReCaptchaV2", "hcaptcha": "HCaptcha",
                         "turnstile": "Turnstile"}[req.kind]
            payload = {"clientKey": key, "task": {
                "type": kind_name,
                "websiteKey": req.recaptcha_sitekey,
                "websiteURL": req.site_url or "http://localhost",
            }}
            if req.kind == "recaptcha":
                payload["task"]["version"] = "v2"
        elif img_b64:
            payload = {"clientKey": key, "task": {
                "type": "ImageToText",
                "body": img_b64,
            }}
        else:
            return CaptchaResult(ok=False, provider=self.id, error="nothing to send")
        r = client.post("https://api.capsolver.com/createTask", json_body=payload)
        try:
            created = r.json()
        except Exception:
            return CaptchaResult(ok=False, provider=self.id,
                                 error=f"bad response: {r.text[:200]}")
        task_id = created.get("taskId")
        if not task_id:
            return CaptchaResult(ok=False, provider=self.id,
                                 error=str(created.get("error") or created))
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            time.sleep(3)
            rr = client.post("https://api.capsolver.com/getTaskResult",
                             json_body={"taskId": task_id})
            try:
                res = rr.json()
            except Exception:
                continue
            status = res.get("status")
            if status == "ready":
                sol = res.get("solution") or {}
                text = sol.get("text") or sol.get("token") or ""
                return CaptchaResult(ok=True, text=str(text), provider=self.id,
                                     cost=0.0025, raw={"id": task_id})
            if status == "failed":
                return CaptchaResult(ok=False, provider=self.id,
                                     error=str(res.get("error") or "task failed"))
        return CaptchaResult(ok=False, provider=self.id,
                             error="timed out waiting for solution")


class NopeCHASolver(BaseSolver):
    id = "nopecha"
    name = "NopeCHA"
    kinds = ("text", "image", "recaptcha", "hcaptcha", "turnstile")
    priority = 110
    needs_key = True
    poll_interval = 3.0
    
    def enabled(self) -> bool:
        return bool(self.settings.captcha_nopecha_key)
    
    def solve(self, req: CaptchaRequest, timeout_s: float = 90.0, client=None) -> CaptchaResult:
        key = self.settings.captcha_nopecha_key
        if not client:
            from ..http_client import HttpClient
            from ..settings import Settings
            client = HttpClient(Settings())
        if req.kind in ("recaptcha", "hcaptcha", "turnstile"):
            kind_map = {
                "recaptcha": "recaptcha2",
                "hcaptcha": "hcaptcha",
                "turnstile": "turnstile"
            }
            type_ = kind_map[req.kind]
            r = client.post("https://api.nopecha.com/token/", json_body={
                "key": key,
                "type": type_,
                "sitekey": req.recaptcha_sitekey,
                "url": req.site_url or "http://localhost"
            })
        else:
            img_b64 = self.fetch_image_b64(req, client)
            if not img_b64:
                return CaptchaResult(ok=False, provider=self.id, error="nothing to send")
            r = client.post("https://api.nopecha.com/", json_body={
                "key": key,
                "type": "textcaptcha",
                "image_data": [img_b64]
            })
        
        try:
            created = r.json()
        except Exception:
            return CaptchaResult(ok=False, provider=self.id, error=f"bad response: {r.text[:200]}")
            
        if "error" in created or "data" not in created:
            return CaptchaResult(ok=False, provider=self.id, 
                                error=str(created.get("message") or created.get("error") or created))
        
        job_id = created["data"]
        
        # Poll for solution
        t0 = time.time()
        base = "https://api.nopecha.com/token/" if req.kind in ("recaptcha", "hcaptcha", "turnstile") else "https://api.nopecha.com/"
        while time.time() - t0 < timeout_s:
            time.sleep(self.poll_interval)
            rr = client.get(base + "?" + urllib.parse.urlencode({"key": key, "id": job_id}))
            try:
                res = rr.json()
            except Exception:
                continue
            if res.get("error") == 14:
                continue  # Job incomplete
            if "error" in res:
                return CaptchaResult(ok=False, provider=self.id, error=str(res.get("message") or res.get("error")))
            if "data" in res:
                data = res["data"]
                text = data[0] if isinstance(data, list) and data else data
                return CaptchaResult(ok=True, text=str(text), provider=self.id, cost=0.001, raw={"id": job_id})
        
        return CaptchaResult(ok=False, provider=self.id, error="timeout")


class TesseractSolver(BaseSolver):
    id = "tesseract"
    name = "Local OCR (Tesseract)"
    kinds = ("text", "image")
    priority = 20
    needs_key = False

    def find_binary(self) -> str:
        p = (self.settings.captcha_tesseract_path or "").strip()
        if p:
            return p if os.path.isfile(p) else ""
        w = shutil.which("tesseract")
        if w:
            return w
        for c in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", 
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if os.path.isfile(c):
                return c
        return ""

    def enabled(self) -> bool:
        return bool(self.settings.captcha_tesseract_enabled) and bool(self.find_binary())

    def solve(self, req, timeout_s=90.0, client=None):
        img_b64 = self.fetch_image_b64(req, client)
        if not img_b64:
            return CaptchaResult(ok=False, provider=self.id, error="no image to OCR")
        
        exe = self.find_binary()
        if not exe:
            return CaptchaResult(ok=False, provider=self.id, error="tesseract not found")
            
        try:
            img = base64.b64decode(img_b64)
        except Exception:
            return CaptchaResult(ok=False, provider=self.id, error="bad image data")
            
        try:
            p = subprocess.run([exe, "stdin", "stdout", "--psm", "7", "-c", "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"], 
                                input=img, capture_output=True, timeout=min(float(timeout_s), 30.0))
        except subprocess.TimeoutExpired:
            return CaptchaResult(ok=False, provider=self.id, error="tesseract timeout")
        except OSError as e:
            return CaptchaResult(ok=False, provider=self.id, error=str(e))
            
        if p.returncode != 0:
            return CaptchaResult(ok=False, provider=self.id, 
                                error="tesseract failed: " + p.stderr.decode("utf-8", "replace")[:200])
        
        text = "".join(p.stdout.decode("utf-8", "replace").split())
        if not text:
            return CaptchaResult(ok=False, provider=self.id, error="OCR returned empty")
        else:
            return CaptchaResult(ok=True, text=text, provider=self.id)


class BrowserCaptchaSolver(BaseSolver):
    id = "browser"; name = "Real browser (Edge CDP / Camoufox)"; kinds = ("turnstile", "hcaptcha", "recaptcha"); priority = 30; needs_key = False
    def __init__(self, settings, store=None, make=None): super().__init__(settings); self.store = store; self.make = make
    def enabled(self): return bool(self.settings.captcha_browser_enabled)
    def solve(self, req, timeout_s=90.0, client=None):
        from .. import browser_solver as bs      # import INSIDE solve, and always call bs.solve_in_browser / bs.make_driver / bs.make_stealth_driver through the module so tests can monkeypatch them
        if not req.site_url: return CaptchaResult(ok=False, provider=self.id, error="no site url")
        make = self.make
        if make is None:
            make = bs.make_stealth_driver if (self.settings.captcha_browser_engine or "edge").lower() == "camoufox" else bs.make_driver
        if req.recaptcha_sitekey:
            # a widget in a site's form: show it on the site's own address, return its token
            try: token = bs.solve_widget_in_browser(req.site_url, req.kind, req.recaptcha_sitekey, make(), timeout=timeout_s)
            except (bs.BrowserSolverUnavailable, ValueError) as e: return CaptchaResult(ok=False, provider=self.id, error=str(e))
            if not token: return CaptchaResult(ok=False, provider=self.id, error="timeout")
            return CaptchaResult(ok=True, text=token, provider=self.id)
        if req.kind != "turnstile": return CaptchaResult(ok=False, provider=self.id, error="no sitekey")
        store = self.store if self.store is not None else (req.extra or {}).get("clearance_store")
        if store is None: return CaptchaResult(ok=False, provider=self.id, error="no clearance store")
        try: c = bs.solve_in_browser(req.site_url, store, make(), timeout=timeout_s)
        except bs.BrowserSolverUnavailable as e: return CaptchaResult(ok=False, provider=self.id, error=str(e))
        if c is None: return CaptchaResult(ok=False, provider=self.id, error="timeout")
        return CaptchaResult(ok=True, text="__CLEARANCE__", provider=self.id)


class AudioCaptchaSolver(BaseSolver):
    id = "audio"
    name = "Offline audio (Vosk)"
    kinds = ("audio",)
    priority = 22
    needs_key = False

    def enabled(self) -> bool:
        return bool(self.settings.captcha_audio_enabled) and bool(self.settings.captcha_vosk_model_path)

    def solve(self, req, timeout_s=90.0, client=None):
        from . import audio as _audio
        
        b64 = (req.extra or {}).get("audio_b64", "")
        if not b64:
            return CaptchaResult(ok=False, provider=self.id, error="no audio")
            
        try:
            data = base64.b64decode(b64)
        except Exception:
            return CaptchaResult(ok=False, provider=self.id, error="bad audio data")
            
        try:
            text = _audio.transcribe(data, self.settings.captcha_vosk_model_path, self.settings.captcha_ffmpeg_path)
        except _audio.AudioUnavailable as e:
            return CaptchaResult(ok=False, provider=self.id, error=str(e))
            
        if not text:
            return CaptchaResult(ok=False, provider=self.id, error="no speech recognised")
        else:
            return CaptchaResult(ok=True, text=text, provider=self.id)
