r"""Create the Ed25519 release signing key (run once, by the maintainer).

    .venv\Scripts\python.exe tools\release_key.py

Prints the public key (hex) to put in updater.RELEASE_KEYS and writes the
private key to a PEM file OUTSIDE the repository. Store that PEM as the
GitHub Actions secret RELEASE_SIGNING_KEY, e.g.

    gh secret set RELEASE_SIGNING_KEY < %USERPROFILE%\opengrab-release-key.pem

then delete the file or keep it offline. Never commit it.
"""
import sys
from pathlib import Path

from Cryptodome.PublicKey import ECC


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "opengrab-release-key.pem"
    if out.exists():
        print(f"{out} already exists; not overwriting it")
        return 1
    key = ECC.generate(curve="ed25519")
    out.write_text(key.export_key(format="PEM"), encoding="ascii")
    print("private key written to", out)
    print("public key (hex) for updater.RELEASE_KEYS:")
    print(key.public_key().export_key(format="raw").hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
