"""Run the bundled Fork manager inside an existing official Python yolk image."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def runtime(root, pin):
    if sys.version_info[:2] == (3, 13):
        return Path(sys.executable)
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise RuntimeError("Migration requires Linux x86-64")
    folder = root / ".fork/runtimes" / pin["sha256"]
    python = folder / "python/bin/python3.13"
    marker = folder / "verified.json"
    if any(p.is_symlink() for p in (folder.parent, folder, marker)):
        raise ValueError("Portable runtime control paths must not be symlinks")
    if marker.is_file() and json.loads(marker.read_text()) == pin and python.is_file():
        return python
    folder.parent.mkdir(parents=True, exist_ok=True)
    if folder.exists():
        raise RuntimeError("Incomplete Python runtime found; original server has not been changed")
    with tempfile.TemporaryDirectory(dir=folder.parent, prefix="python-download-") as temp:
        temporary = Path(temp)
        archive = temporary / "python.tar.gz"
        request = urllib.request.Request(pin["url"], headers={"User-Agent": "Endstone-Fork-Migration/1"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            if not response.url.startswith("https://"):
                raise ValueError("Python runtime download must stay on HTTPS")
            shutil.copyfileobj(response, output)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != pin["sha256"]:
            raise ValueError("Python runtime SHA256 mismatch")
        extracted = temporary / "extracted"
        extracted.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(extracted, filter="data")
        executable = extracted / "python/bin/python3.13"
        actual = subprocess.check_output([str(executable), "-I", "-c", "import platform;print(platform.python_version())"],
                                         text=True).strip()
        if actual != pin["version"]:
            raise ValueError("Downloaded Python runtime version mismatch")
        (extracted / "verified.json").write_text(json.dumps(pin), encoding="utf-8")
        os.replace(extracted, folder)
    return python


def main():
    root = Path.cwd().resolve()
    if (root / ".fork").is_symlink():
        raise ValueError("Migration control directory must not be a symlink")
    pin = json.loads(Path(__file__).with_name("python-runtime.json").read_text("utf-8"))
    original = sys.executable
    python = runtime(root, pin)
    env = os.environ.copy()
    env["MIGRATION_ORIGINAL_PYTHON"] = original
    env["MIGRATE_OFFICIAL"] = "true"
    # Selecting this dedicated migration Egg authorizes the first verified
    # stable migration. Subsequent updates retain the normal approval setting.
    if not (root / ".fork/current.json").exists():
        if env.get("UPDATE_CHANNEL", "stable") != "stable":
            raise ValueError("First migration from the panel requires a validated stable release")
        env["UPDATE_REQUIRE_APPROVAL"] = "false"
    for key in ("PYTHONPATH", "PYTHONHOME", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        env.pop(key, None)
    os.execve(str(python), [str(python), "-I", str(Path(__file__).with_name("manager.py")),
                          "start", "--root", str(root)], env)


if __name__ == "__main__":
    main()
