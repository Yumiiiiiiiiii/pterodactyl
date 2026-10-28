"""Real official-to-Fork migration on a disposable Docker volume only.

Local exact artifacts replace network downloads in this fixture. This is not
a published stable release and does not certify a different native BDS target.
Run with the original official Python image, plus the portable Python runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--bds", type=Path, required=True)
    parser.add_argument("--plugins", type=Path)
    parser.add_argument("--original-python", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or sys.version_info[:2] != (3, 13) or os.getuid() != 1000:
        raise ValueError("Use portable Python 3.13 and the Wings UID 1000")
    root = args.server.resolve()
    root.mkdir(parents=True, exist_ok=False)
    manager_module = load(Path(__file__).resolve().parents[1] / "runtime/manager.py", "migration_manager")
    manager = manager_module.Manager(root)
    os.environ["MIGRATION_ORIGINAL_PYTHON"] = args.original_python
    os.environ["AUTO_UPDATE_PLUGINS"] = "true"
    os.environ["ROLLBACK_ON_FAILURE"] = "false"  # first migration must still restore
    for key in ("DISCORDSRV_TOKEN", "ENDSTONE_DISCORD_TOKEN"):
        os.environ.pop(key, None)
    with zipfile.ZipFile(args.bds) as archive:
        archive.extractall(root)
    (root / "bedrock_server").chmod(0o755)
    (root / "version.txt").write_text("26.52")
    (root / "server.properties").write_text(
        "server-name=Disposable migration fixture\nlevel-name=Survival\nserver-port=29292\n"
        "server-portv6=29293\nonline-mode=true\nenable-lan-visibility=false\nview-distance=4\nmax-players=2\n")
    (root / "endstone.toml").write_text("[network]\nipv6=false\nstun-servers=[]\n")
    (root / "plugins/friend_join").mkdir(parents=True)
    (root / "plugins/friend_join/config.toml").write_text("autostart=false\n")
    plugins = sorted(args.plugins.glob("*.whl")) if args.plugins else []
    # Baseline image already has its official plugin entry points installed.
    # Candidate plugins will instead load from plugins/*.whl, matching Wings.
    official = {"kind": "official", "python": args.original_python, "migration": {"fixture": True}}
    report = {"schema": 1, "stable_eligible": False, "published_release_tested": False,
              "bds_version": "1.26.52", "original_endstone": "0.11.14", "uid": os.getuid(),
              "fork_wheel": args.wheel.name, "fork_wheel_sha256": manager_module.digest(args.wheel)}
    try:
        with manager.lock():
            if manager.serve(official, health_trial=True):
                raise RuntimeError("Official baseline failed; inspect .fork/logs")
            inventory = manager.migration_module().inventory(manager)
            report["original_inventory"] = inventory
            assert (root / "worlds/Survival/db").is_dir()
            # A real independent SQLite file, included in the same cold snapshot.
            database = root / "plugins/migration-sentinel.sqlite"
            with sqlite3.connect(database) as connection:
                connection.execute("create table sentinel(value text)")
                connection.execute("insert into sentinel values ('before migration')")
            (root / "resource_packs/migration-sentinel").mkdir()
            (root / "resource_packs/migration-sentinel/preserved.txt").write_text("pack contents")
            initial = {p.relative_to(root).as_posix(): manager_module.digest(p)
                       for p in root.rglob("*") if p.is_file() and ".fork" not in p.relative_to(root).parts}
            wheel_version = args.wheel.name.split("-")[1]
            bds_url = "https://www.minecraft.net/bedrockdedicatedserver/bin-linux/" + args.bds.name
            assets = {"fixture://wheel": args.wheel, bds_url: args.bds}
            manifest = {"schema": 1, "repository": "Yumiiiiiiiiii/endstone", "channel": "candidate",
                        "release": "local-only-official-migration", "commit": wheel_version.split("+ymii.g")[1],
                        "endstone_version": wheel_version,
                        "bds": {"version": "1.26.52", "url": bds_url, "sha256": manager_module.digest(args.bds)},
                        "wheel": {"filename": args.wheel.name, "url": "fixture://wheel", "sha256": manager_module.digest(args.wheel)},
                        "plugins": []}
            for plugin in plugins:
                url = "fixture://" + plugin.name
                assets[url] = plugin
                manifest["plugins"].append({"filename": plugin.name, "url": url, "sha256": manager_module.digest(plugin)})
            relay = args.plugins / "endstone-friend-bridge" if args.plugins else None
            if relay and relay.is_file():
                assets["fixture://relay"] = relay
                manifest["relay"] = {"filename": relay.name, "url": "fixture://relay", "sha256": manager_module.digest(relay)}

            def local_fetch(url, destination, sha256=None, **_):
                source = assets[url]
                if manager_module.digest(source) != sha256:
                    raise ValueError("Local fixture artifact hash mismatch")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            with patch.object(manager_module, "fetch", side_effect=local_fetch):
                checksum = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
                state = manager.migrate_official(manifest, checksum)
                report["migration"] = {"status": "passed", "backup": state["backup"],
                    "bds_preserved": manager_module.digest(root / "bedrock_server") == initial["bedrock_server"],
                    "settings_preserved": all(manager_module.digest(root / name) == initial[name]
                        for name in ("server.properties", "endstone.toml", "resource_packs/migration-sentinel/preserved.txt")),
                    "plugins": [p.name for p in plugins]}
                assert report["migration"]["bds_preserved"] and report["migration"]["settings_preserved"]
                logs = [p.read_text() for p in (manager.control / "logs").glob("*.log")]
                for plugin_name in ("DiscordSrv", "FriendJoin", "Vanish") if plugins else ():
                    assert any(plugin_name in text and "Enabling" in text for text in logs), plugin_name
                manager.restore(Path(state["backup"]))
                restored = {p.relative_to(root).as_posix(): manager_module.digest(p)
                            for p in root.rglob("*") if p.is_file() and ".fork" not in p.relative_to(root).parts}
                assert restored == initial
                # Explicitly archive a completed receipt before testing another migration.
                os.replace(manager.control / "migration.json", manager.control / "first-migration.json")
                actual_serve = manager.serve

                def fail_after_real_boot(candidate, health_trial=False):
                    assert actual_serve(candidate, health_trial=health_trial) == 0
                    with sqlite3.connect(database) as connection:
                        connection.execute("update sentinel set value='failed update'")
                    (root / "worlds/Survival/db/fixture-failed-write").write_bytes(b"failed update")
                    return 1

                failed_checksum = hashlib.sha256((checksum + "failure").encode()).hexdigest()
                with patch.object(manager, "serve", side_effect=fail_after_real_boot):
                    try:
                        manager.migrate_official(manifest, failed_checksum)
                    except RuntimeError as error:
                        assert "health trial" in str(error)
                    else:
                        raise AssertionError("Injected failure was not rejected")
                restored = {p.relative_to(root).as_posix(): manager_module.digest(p)
                            for p in root.rglob("*") if p.is_file() and ".fork" not in p.relative_to(root).parts}
                assert restored == initial and manager.current() is None
                report["failed_migration_restore"] = {"status": "passed", "files": len(initial),
                    "all_sha256_identical": True, "world_and_sqlite_restored": True,
                    "normal_rollback_setting": False}
                assert manager.serve(official, health_trial=True) == 0
                report["official_restart_after_restore"] = "passed"
            report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
