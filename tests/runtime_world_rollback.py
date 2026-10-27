"""Exercise snapshot restore after real newer vanilla BDS writes a scratch world.

This does not certify Endstone on the newer BDS. Run only in a loopback-only
network namespace, with an already stopped, disposable baseline server.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import queue
import re
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def world_version(server):
    # Bedrock level.dat has an 8-byte header followed by little-endian NBT.
    # Match the complete typed List<Int> tag, not an untyped name substring.
    raw = (server / "worlds/ForkCI/level.dat").read_bytes()
    if len(raw) < 8 or struct.unpack_from("<I", raw, 4)[0] != len(raw) - 8:
        raise ValueError("Unexpected level.dat header")
    name = b"lastOpenedWithVersion"
    marker = b"\x09" + struct.pack("<H", len(name)) + name + b"\x03"
    if raw.count(marker) != 1:
        raise ValueError("Expected one typed lastOpenedWithVersion tag")
    offset = raw.index(marker) + len(marker)
    count = struct.unpack_from("<i", raw, offset)[0]
    if not 3 <= count <= 8:
        raise ValueError("Unexpected saved world version length")
    return list(struct.unpack_from("<" + "i" * count, raw, offset + 4))


def vanilla(server, log):
    env = {key: value for key, value in os.environ.items() if key not in {"LD_PRELOAD", "LD_AUDIT"}}
    process = subprocess.Popen([str(server / "bedrock_server")], cwd=server, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    messages = queue.Queue()

    def read():
        for line in process.stdout:
            messages.put(line)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    lines = []
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=0.25)
                lines.append(line)
                if "Server started" in line:
                    process.stdin.write("list\nstop\n")
                    process.stdin.flush()
                    process.wait(timeout=60)
                    break
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError(f"Vanilla BDS exited {process.returncode} before startup; see log")
        else:
            raise TimeoutError("Vanilla BDS startup timeout")
        if process.returncode != 0:
            raise RuntimeError(f"Vanilla BDS exit {process.returncode}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=5)
        while not messages.empty():
            lines.append(messages.get_nowait())
        log.write_text("".join(lines), encoding="utf-8")
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-server", type=Path, required=True)
    parser.add_argument("--scratch-server", type=Path, required=True)
    parser.add_argument("--endstone-source", type=Path, required=True)
    parser.add_argument("--fork-commit", required=True)
    parser.add_argument("--target-archive", type=Path, required=True)
    parser.add_argument("--target-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        raise ValueError("Linux network namespace required")
    interfaces = json.loads(subprocess.check_output(["ip", "-j", "link"], text=True))
    if {item["ifname"] for item in interfaces} != {"lo"}:
        raise ValueError("Run with unshare --net and enable only loopback")
    if not re.fullmatch(r"[0-9a-f]{40}", args.fork_commit):
        raise ValueError("Full baseline Fork commit required")
    version = importlib.metadata.version("endstone")
    if not version.endswith("+ymii.g" + args.fork_commit[:12]):
        raise ValueError("Installed baseline Wheel does not match Fork commit")
    from endstone import __minecraft_version__
    baseline = __minecraft_version__ if __minecraft_version__.startswith("1.") else "1." + __minecraft_version__
    manager_module = load(ROOT / "runtime/manager.py", "world_rollback_manager")
    smoke = load(args.endstone_source / "fork/runtime_smoke.py", "world_rollback_smoke")
    source, server = args.baseline_server.resolve(), args.scratch_server.resolve()
    if server.exists() or server.is_relative_to(source) or source.is_relative_to(server):
        raise ValueError("Scratch server must be new and separate from baseline")
    if (source / ".fork/server.pid").exists():
        raise ValueError("Baseline server has a PID marker")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "cwd").resolve() == source:
                command = (entry / "cmdline").read_bytes().split(b"\0")
                if any(Path(os.fsdecode(arg)).name == "bedrock_server" for arg in command if arg):
                    raise ValueError("Baseline BDS is running")
        except (OSError, ProcessLookupError):
            continue
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Baseline contains symlinks")
    metadata = json.loads(args.target_metadata.read_text("utf-8-sig"))
    if manager_module.digest(args.target_archive) != metadata["binary"]["linux"]["sha256"]:
        raise ValueError("Target BDS archive SHA256 mismatch")
    shutil.copytree(source, server, ignore=shutil.ignore_patterns(".fork"))
    # Only this disposable copy uses offline RakNet. The original server and
    # production authentication remain untouched; this namespace has no uplink.
    properties = server / "server.properties"
    lines = [line for line in properties.read_text("utf-8").splitlines()
             if line.partition("=")[0] not in {"online-mode", "transport", "allow-list"}]
    properties.write_text("\n".join(lines) + "\nonline-mode=false\ntransport=raknet\nallow-list=false\n", encoding="utf-8")
    manager = manager_module.Manager(server)
    state = {"manifest": {"bds": {"version": baseline}, "commit": args.fork_commit,
                          "endstone_version": version}, "python": sys.executable, "control_only": True}
    manager_module.atomic_json(manager.state_path, state)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def contents():
        return {p.relative_to(server).as_posix(): manager_module.digest(p)
                for p in server.rglob("*") if p.is_file() and ".fork" not in p.relative_to(server).parts}

    result = {"schema": 1, "baseline_bds": baseline, "target_bds": metadata["version"],
              "fork_commit": args.fork_commit, "endstone": version,
              "target_endstone_verified": False, "stable_eligible": False,
              "control_network": "loopback-only offline RakNet", "checks": {}}
    backup = None
    try:
        old = contents()
        result["before_world_version"] = world_version(server)
        backup = manager.snapshot()
        result["backup"] = str(backup)
        with ZipFile(args.target_archive) as archive:
            for item in archive.infolist():
                relative = manager_module.safe_relative(item.filename)
                if relative.parts[0] in {".fork", "worlds", "plugins"} or stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("Unexpected target archive path")
                if item.filename in {"server.properties", "allowlist.json", "permissions.json", "endstone.toml"}:
                    continue
                archive.extract(item, server)
        (server / "bedrock_server").chmod(0o755)
        text = vanilla(server, args.output.with_suffix(".vanilla.log"))
        if "Version: " + metadata["version"] not in text or "Endstone" in text:
            raise ValueError("Target vanilla startup version does not match metadata")
        newer = contents()
        result["after_world_version"] = world_version(server)
        expected = [int(part) for part in metadata["version"].split(".")]
        if result["after_world_version"][:3] != expected[:3]:
            raise ValueError("Newer BDS did not persist its world version")
        changed_world = [name for name, value in newer.items()
                         if name.startswith("worlds/") and old.get(name) != value]
        if not changed_world:
            raise ValueError("No real world writes observed")
        result["changed_world_files"] = changed_world
        manager.restore(backup)
        if contents() != old or manager.current() != state:
            raise ValueError("Snapshot files or version state did not fully restore")
        result["restored_files"] = len(old)
        result["restored_world_version"] = world_version(server)
        result["checks"]["world_snapshot_restore"] = "passed"
        (server / "fork-probe.json").unlink(missing_ok=True)
        # All baseline dependencies must already be present in the retained
        # interpreter; a rollback must not depend on a package index.
        os.environ["PIP_NO_INDEX"] = "true"
        result["restart_probe"] = smoke.run_server(server, baseline, args.output.with_suffix(".restart.log"))
        result["checks"]["baseline_fork_restart"] = "passed"
        result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error=str(error))
        if backup is not None:
            manager.restore(backup)
            if contents() != old or manager.current() != state:
                raise RuntimeError("Error recovery did not restore the baseline snapshot") from error
            result["checks"]["restore_after_test_error"] = "passed"
        raise
    finally:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
