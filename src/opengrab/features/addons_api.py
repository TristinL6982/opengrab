"""Routes for optional add-ons (B22): list, install, remove, hardware profile."""
from __future__ import annotations

from .. import addons, hardware


def setup(srv):
    r = srv.router

    def list_addons(req):
        return {"addons": [addons.status(n) for n in addons.ADDONS],
                "hardware": hardware.profile()}

    def install(req):
        name = req.params["name"]
        if name not in addons.ADDONS:
            return 404, {"error": "unknown add-on"}

        def done(n, ok, err):
            srv.broadcast("addon", {"name": n, "ok": ok, "error": err})
            notify = srv.features.get("notify")
            if notify is not None:
                title = "Add-on installed" if ok else "Add-on install failed"
                notify.add("info" if ok else "failed", title,
                           addons.ADDONS[n]["title"] + ("" if ok else ": " + err))

        return 202, {"job": addons.install_async(name, on_done=done)}

    def remove(req):
        name = req.params["name"]
        if name not in addons.ADDONS:
            return 404, {"error": "unknown add-on"}
        return {"removed": addons.remove(name)}

    def test_ocr(req):
        """Read a generated captcha image with the local OCR add-on."""
        import base64
        import io
        import time
        from ..captcha.base import CaptchaRequest, DdddOCRSolver
        solver = DdddOCRSolver(srv.manager.settings)
        reason = solver.unavailable_reason()
        if reason:
            return 400, {"error": reason}
        img = req.body.get("image_b64")
        expected = ""
        if not img:
            from PIL import Image, ImageDraw, ImageFont
            expected = "4821"
            im = Image.new("RGB", (120, 44), "white")
            try:
                font = ImageFont.truetype("arial.ttf", 30)
            except OSError:
                font = ImageFont.load_default()
            ImageDraw.Draw(im).text((14, 5), expected, fill="black", font=font)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            img = base64.b64encode(buf.getvalue()).decode()
        t0 = time.time()
        res = solver.solve(CaptchaRequest(image_b64=img, kind="image"))
        return {"ok": res.ok, "text": res.text, "expected": expected, "error": res.error,
                "ms": int((time.time() - t0) * 1000)}

    r.post(r"/api/addons/ocr/test", test_ocr)
    r.get(r"/api/addons", list_addons)
    r.post(r"/api/addons/(?P<name>[a-z0-9_-]+)/install", install, action="settings_write")
    r.post(r"/api/addons/(?P<name>[a-z0-9_-]+)/remove", remove, action="settings_write")
    return None
