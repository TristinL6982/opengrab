"""Sign a release file with the key in the RELEASE_SIGNING_KEY environment
variable (PEM). Writes <file>.sig (base64 Ed25519 signature over the bytes).

    python tools/sign_release.py build/release/OpenGrab-0.2.0-win64.zip
"""
import base64
import os
import sys
from pathlib import Path

from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa


def main() -> int:
    pem = os.environ.get("RELEASE_SIGNING_KEY", "")
    if "BEGIN" not in pem:
        print("RELEASE_SIGNING_KEY is not set; the release stays unsigned and the in-app updater will refuse it")
        return 2
    key = ECC.import_key(pem)
    for name in sys.argv[1:]:
        path = Path(name)
        sig = eddsa.new(key, "rfc8032").sign(path.read_bytes())
        Path(str(path) + ".sig").write_text(base64.b64encode(sig).decode("ascii"), encoding="ascii")
        print("signed", path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
