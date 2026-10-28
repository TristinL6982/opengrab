import html
import re

SCRIPT_URLS = {"recaptcha": "https://www.google.com/recaptcha/api.js", "hcaptcha": "https://js.hcaptcha.com/1/api.js", "turnstile": "https://challenges.cloudflare.com/turnstile/v0/api.js"}
WIDGET_CLASS = {"recaptcha": "g-recaptcha", "hcaptcha": "h-captcha", "turnstile": "cf-turnstile"}
TOKEN_FIELDS = {"recaptcha": "g-recaptcha-response", "hcaptcha": "h-captcha-response", "turnstile": "cf-turnstile-response"}

class UnsupportedKind(ValueError):
    pass

def build_harvest_page(kind: str, sitekey: str, request_id: str, submit_path: str = "/api/captcha/solve") -> str:
    if kind not in SCRIPT_URLS:
        raise UnsupportedKind(f"Unsupported captcha kind: {kind}")
    
    if not re.match(r'^[A-Za-z0-9_-]{1,100}$', sitekey):
        raise ValueError("Invalid sitekey")
        
    if not re.match(r'^[A-Za-z0-9]{1,64}$', request_id):
        raise ValueError("Invalid request_id")
        
    if not submit_path.startswith("/"):
        raise ValueError("submit_path must start with '/'")
    
    script_url = SCRIPT_URLS[kind]
    widget_class = WIDGET_CLASS[kind]
    escaped_sitekey = html.escape(sitekey, quote=True)
    escaped_request_id = html.escape(request_id, quote=True)
    escaped_submit_path = html.escape(submit_path, quote=True)
    
    page = f"""<!DOCTYPE html>
<html>
<head>
    <title>Captcha Harvest</title>
</head>
<body>
    <script src="{html.escape(script_url, quote=True)}" async defer></script>
    <div class="{html.escape(widget_class, quote=True)}" data-sitekey="{escaped_sitekey}" data-callback="ogSolved"></div>
    <script>
        function ogSolved(token) {{
            fetch("{escaped_submit_path}", {{
                method: "POST",
                headers: {{"Content-Type": "application/json"}},
                body: JSON.stringify({{id: "{escaped_request_id}", answer: token}})
            }});
        }}
    </script>
</body>
</html>"""
    
    return page

def extract_token(kind: str, form: dict) -> str | None:
    if kind not in TOKEN_FIELDS:
        raise UnsupportedKind(f"Unsupported captcha kind: {kind}")
        
    field = TOKEN_FIELDS[kind]
    token = form.get(field)
    if token is None:
        return None
        
    stripped = token.strip()
    if not stripped:
        return None
        
    return stripped