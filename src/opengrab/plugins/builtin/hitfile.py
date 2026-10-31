"""
hitfile.net (same platform as Turbobit): free (captcha + server delay) and
premium (signed-in account) downloads. The flow lives in _fdplatform.
"""
from ._fdplatform import FdPlatformPlugin

HOSTS = ("hitfile.net",
         # short-link domains (redirect to hitfile.net?short_domain=...)
         "hitf.to", "hitf.cc", "hil.to", "htfl.net", "htfl.to")


class HitfilePlugin(FdPlatformPlugin):
    id = "hitfile"
    name = "Hitfile"
    site = "hitfile.net"
    canonical = "hitfile.net"
    hosts = HOSTS
    label = "Hitfile"
    description = ("hitfile.net: free downloads (captcha and wait) and premium accounts "
                   "(e-mail/password or cookies).")
