import os, subprocess, sys; from pathlib import Path
REPRO = Path(__file__).parent / "repro"; SRC = Path(__file__).resolve().parents[2] / "src"
def run_repro(name, *args, timeout=180):
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=str(SRC))
    return subprocess.run([sys.executable, "-u", str(REPRO / name), *map(str, args)], capture_output=True, text=True, timeout=timeout, env=env)