"""First-boot migration of an official server, keeping its BDS and Docker image.

This module receives the update Manager, rather than importing a global server.
All paths refer to one stopped server. No panel credentials are used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path


def version(value):
    match = re.fullmatch(r"(?:1\.)?(\d+)\.(\d+)(?:\.(\d+))?", value.strip())
    if not match:
        raise ValueError("Cannot identify the installed BDS version")
    return (1, int(match[1]), int(match[2]), int(match[3] or 0))


def saved_world_version(path):
    raw = path.read_bytes()
    if len(raw) < 8 or struct.unpack_from("<I", raw, 4)[0] != len(raw) - 8:
        raise ValueError("Invalid level.dat header; original world is unchanged")
    cursor = 8

    def take(size):
        nonlocal cursor
        if size < 0 or cursor + size > len(raw):
            raise ValueError("Truncated level.dat NBT")
        data = raw[cursor:cursor + size]
        cursor += size
        return data

    def number(fmt):
        return struct.unpack("<" + fmt, take(struct.calcsize(fmt)))[0]

    def string():
        return take(number("H")).decode("utf-8")

    def payload(tag, depth=0):
        if depth > 64:
            raise ValueError("Excessive NBT nesting")
        if tag in {1, 2, 3, 4, 5, 6}:
            return number({1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}[tag])
        if tag in {7, 11, 12}:
            return take(number("i") * {7: 1, 11: 4, 12: 8}[tag])
        if tag == 8:
            return string()
        if tag == 9:
            child, count = number("B"), number("i")
            if not 0 <= count <= len(raw) or child == 0 and count:
                raise ValueError("Invalid NBT list")
            return child, [payload(child, depth + 1) for _ in range(count)]
        if tag == 10:
            result = {}
            while (child := number("B")):
                name = string()
                if name in result:
                    raise ValueError("Duplicate NBT compound key")
                result[name] = (child, payload(child, depth + 1))
            return result
        raise ValueError("Unknown level.dat NBT tag")

    if number("B") != 10:
        raise ValueError("Expected level.dat root compound")
    string()
    root = payload(10)
    tag, data = root.get("lastOpenedWithVersion", (None, None))
    if tag != 9 or not isinstance(data, tuple):
        raise ValueError("Cannot identify the saved world version")
    child, values = data
    if cursor != len(raw) or child != 3 or not 3 <= len(values) <= 8 or any(type(x) is not int or x < 0 for x in values):
        raise ValueError("Cannot identify the saved world version")
    return tuple([*values[:4], *([0] if len(values) == 3 else [])])


def inventory(manager):
    root = manager.root
    if not (root / "bedrock_server").is_file() or not (root / "server.properties").is_file():
        raise ValueError("Migration requires an existing, stopped official Endstone server")
    properties = {}
    for line in (root / "server.properties").read_text("utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            properties[key.strip()] = value.strip()
    level = properties.get("level-name", "Bedrock level")
    if not level or level in {".", ".."} or any(c in level for c in "/\\\0"):
        raise ValueError("Unsafe existing level-name")
    level_file = root / "worlds" / level / "level.dat"
    if not level_file.is_file():
        raise ValueError("Configured existing world is missing; refuse to generate a replacement world")
    worlds = []
    for path in sorted((root / "worlds").glob("*/level.dat")):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("World symlinks require a separate operator migration")
        worlds.append({"path": path.relative_to(root).as_posix(), "saved_version": saved_world_version(path),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    interpreter = os.environ.get("MIGRATION_ORIGINAL_PYTHON", sys.executable)
    if not Path(interpreter).is_absolute() or not Path(interpreter).is_file():
        raise ValueError("Original Python interpreter is unavailable")
    probe = """import importlib.metadata as m,json,sys,site
import endstone
for path in site.getsitepackages(prefixes=['plugins/.local']):
    site.addsitedir(path)
plugins={e.dist.metadata['Name']:e.dist.version for e in m.entry_points(group='endstone')}
print(json.dumps({'python':list(sys.version_info[:3]),'endstone':m.version('endstone'),
'native_bds':endstone.__minecraft_version__,'plugins':plugins}))
"""
    process = subprocess.run([interpreter, "-c", probe], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", timeout=30, check=False)
    try:
        old = json.loads(process.stdout.strip().splitlines()[-1])
        installed = version((root / "version.txt").read_text("utf-8-sig"))
        if process.returncode or "+ymii." in old["endstone"] or version(old["native_bds"])[:3] != installed[:3]:
            raise ValueError()
    except (ValueError, IndexError, KeyError):
        raise ValueError("Original official Endstone runtime/version could not be verified") from None
    if any(tuple(item["saved_version"])[:3] > installed[:3] for item in worlds):
        raise ValueError("A world was saved by a newer BDS; refuse downgrade before opening it")
    # Existing external databases cannot be included in a file snapshot. Refuse
    # known external storage instead of claiming a consistent automatic backup.
    for path in [root / "plugins/discord_srv/config.toml", root / "plugins/discord_srv/config.yml"]:
        if path.is_file():
            config = path.read_text("utf-8")
            sql = re.search(r"(?im)^\s*(?:backend|type|storage-type)\s*[:=]\s*['\"]?(?:mysql|mariadb|postgres)", config)
            jdbc = re.search(r"(?im)^\s*Experiment_JdbcAccountLinkBackend\s*:\s*['\"]?(jdbc:mysql://[^\r\n]+)", config)
            if sql or (jdbc and "HOST:PORT" not in jdbc[1]):
                raise ValueError("External plugin database requires a coordinated database snapshot")
    return {"schema": 1, "kind": "official", "python": interpreter, "runtime": old,
            "bds_version": list(installed), "bds_sha256": manager.file_digest(root / "bedrock_server"),
            "level_name": level, "worlds": worlds,
            "plugins": [{"filename": p.relative_to(root).as_posix(), "sha256": manager.file_digest(p)}
                        for p in sorted((root / "plugins").glob("*")) if p.is_file() and p.suffix in {".whl", ".so"}]}


def migrate(manager, manifest, checksum):
    """Called under Manager.lock; returns a candidate state after successful trial."""
    receipt = manager.control / "migration.json"
    if receipt.exists():
        old_receipt = json.loads(receipt.read_text("utf-8"))
        if old_receipt["state"] == "rolled_back":
            raise RuntimeError("Previous migration failed and was restored. Use the original Egg, or review and archive migration.json before retry")
        if old_receipt["state"] in {"preparing", "applying"}:
            manager.restore(Path(old_receipt["backup"]))
            old_receipt["state"] = "rolled_back"
            manager.write_json(receipt, old_receipt)
            raise RuntimeError("Interrupted migration restored; original server files are intact")
    original = inventory(manager)
    requested = version(manifest["bds"]["version"])
    if requested[:3] != tuple(original["bds_version"])[:3]:
        raise ValueError("First migration must keep the existing BDS version; no automatic upgrade or downgrade")
    build = re.search(r"bedrock-server-(1\.\d+\.\d+\.\d+)\.zip$", manifest["bds"]["url"])
    if not build:
        raise ValueError("First migration requires an identifiable exact BDS build URL")
    build_version = version(build[1])
    if any(tuple(world["saved_version"])[:4] > build_version for world in original["worlds"]):
        raise ValueError("A world was saved by a newer BDS build; refuse downgrade")
    def normalize(name):
        return re.sub(r"[-_.]+", "-", name).lower()
    available = {normalize(p.name.split("-")[0]) for p in (manager.root / "plugins").glob("*.whl")}
    available.update(normalize(item["filename"].split("-")[0]) for item in manifest.get("plugins", []))
    missing = [name for name in original["runtime"].get("plugins", {}) if normalize(name) not in available]
    if missing:
        raise ValueError("Existing Python plugins need matching Wheel artifacts before migration: " + ", ".join(missing))
    backup = manager.snapshot()
    journal = {"schema": 1, "state": "preparing", "backup": str(backup), "original": original,
               "candidate": checksum, "started_at": manager.timestamp()}
    manager.write_json(receipt, journal)
    try:
        stage, python, plugins = manager.stage(manifest, checksum)
        if manager.file_digest(stage / "bds/bedrock_server") != original["bds_sha256"]:
            raise ValueError("First migration must keep the exact BDS binary build")
        # A cold snapshot includes old plugins, packs, settings, player data and
        # .local. First migration deliberately does not copy BDS archive files.
        journal["state"] = "applying"
        manager.write_json(receipt, journal)
        manager.install_plugins(manifest, stage, plugins)
        candidate = {"manifest": manifest, "manifest_sha256": checksum, "python": str(python),
                     "installed_at": manager.timestamp(), "bds_files": [], "backup": str(backup),
                     "migration": {"from": "official", "receipt": str(receipt), "bds_preserved": True}}
        if manager.serve(candidate, health_trial=True) != 0:
            raise RuntimeError("Fork migration health trial failed")
        if manager.file_digest(manager.root / "bedrock_server") != original["bds_sha256"]:
            raise RuntimeError("BDS changed during first migration")
        manager.write_json(manager.state_path, candidate)
        journal["state"] = "committed"
        manager.write_json(receipt, journal)
        return candidate
    except BaseException:
        manager.restore(backup)
        journal["state"] = "rolled_back"
        manager.write_json(receipt, journal)
        raise
