"""
turbobit.net and its mirror domains: free (captcha + server delay) and
premium (signed-in account) downloads. The flow lives in _fdplatform.
"""
from ._fdplatform import FdPlatformPlugin

HOSTS = ("turbobit.net", "turbobit.cc", "turbobif.com", "turbobyt.net",
         # short-link domains (redirect to turbobit.net?short_domain=...)
         "trbt.cc", "turbo.to", "turb.to", "turb.cc", "turb.pw", "trbbt.net", "tbit.to")


class TurbobitPlugin(FdPlatformPlugin):
    id = "turbobit"
    name = "Turbobit"
    site = "turbobit.net"
    canonical = "turbobit.net"
    hosts = HOSTS
    label = "Turbobit"
    description = ("turbobit.net and mirrors: free downloads (captcha and wait) and "
                   "premium accounts (e-mail/password or cookies).")

    def page_url(self, fid: str) -> str:
        return f"https://{self.canonical}/{fid}.html"
