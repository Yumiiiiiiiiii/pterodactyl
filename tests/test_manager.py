"""Offline failure injection tests, using disposable data rather than a live server."""

import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fork_manager", ROOT / "runtime/manager.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def manifest():
    return {"schema": 1, "repository": "Yumiiiiiiiiii/endstone", "channel": "stable",
            "release": "ymii-test", "commit": "a" * 40, "endstone_version": "0.11.14+ymii.gaaaaaaaaaaaa",
            "python_tag": f"cp{os.sys.version_info.major}{os.sys.version_info.minor}",
            "wheel": {"filename": f"endstone-0.11.14+ymii.gaaaaaaaaaaaa-cp{os.sys.version_info.major}{os.sys.version_info.minor}-cp{os.sys.version_info.major}{os.sys.version_info.minor}-manylinux_2_31_x86_64.whl",
                      "url": "https://github.com/Yumiiiiiiiiii/endstone/releases/download/ymii-test/fixture.whl", "sha256": "b" * 64},
            "bds": {"version": "1.26.60", "url": "https://www.minecraft.net/bedrockdedicatedserver/bin-linux/fixture.zip", "sha256": "c" * 64},
            "validation": {"commit": "a" * 40, "checks": dict.fromkeys(module.CHECKS, "passed")}, "plugins": []}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "server"
        self.root.mkdir()
        self.manager = module.Manager(self.root)
        for name, value in {"worlds/world/db/data": b"OLD_WORLD", "plugins/private/data.json": b"PRIVATE_DATA",
                            "plugins/private.whl": b"OLD_PLUGIN", "server.properties": b"USER_SETTINGS",
                            "endstone.toml": b"USER_CONFIG", "bedrock_server": b"OLD_BDS"}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        self.old_state = {"manifest": {"bds": {"version": "1.26.52"}}, "python": "/retained/old-env/bin/python"}
        module.atomic_json(self.manager.state_path, self.old_state)

    def tearDown(self):
        self.temporary.cleanup()

    def contents(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and ".fork" not in p.relative_to(self.root).parts}

    def stage(self):
        stage = self.manager.control / "test-stage"
        (stage / "bds").mkdir(parents=True)
        (stage / "bds/bedrock_server").write_bytes(b"NEW_BDS")
        (stage / "bds/server.properties").write_bytes(b"DEFAULT_SETTINGS")
        plugins = stage / "private-0.2-py3-none-any.whl"
        plugins.write_bytes(b"NEW_PLUGIN")
        return stage, plugins

    def test_full_snapshot_round_trip(self):
        old = self.contents()
        backup = self.manager.snapshot()
        (self.root / "worlds/world/db/data").write_bytes(b"NEW_FORMAT")
        (self.root / "new-config.json").write_bytes(b"NEW")
        self.manager.restore(backup)
        self.assertEqual(self.contents(), old)
        self.assertEqual(self.manager.current(), self.old_state)
        self.assertTrue(list((self.manager.control / "failed").rglob("new-config.json")))

    def test_failed_health_restores_world_binaries_plugins_and_version(self):
        old = self.contents()
        stage, plugins = self.stage()

        def failed_trial(*_, **__):
            (self.root / "worlds/world/db/data").write_bytes(b"UPGRADED_WORLD")
            return 1

        with patch.object(self.manager, "serve", side_effect=failed_trial), patch.dict(os.environ, {"ROLLBACK_ON_FAILURE": "true"}):
            with self.assertRaisesRegex(RuntimeError, "health trial"):
                self.manager.apply(manifest(), "d" * 64, stage, Path("/new/python"), [plugins])
        self.assertEqual(self.contents(), old)
        self.assertEqual(self.manager.current(), self.old_state)
        self.assertEqual(json.loads((self.manager.control / "transaction.json").read_text())["state"], "rolled_back")

    def test_backup_failure_never_mutates_active_files(self):
        old = self.contents()
        stage, plugins = self.stage()
        with patch.object(self.manager, "snapshot", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.manager.apply(manifest(), "d" * 64, stage, Path("/new/python"), [plugins])
        self.assertEqual(self.contents(), old)

    def test_corrupt_backup_is_rejected_before_restore(self):
        backup = self.manager.snapshot()
        old = self.contents()
        with (backup / "server.tar.gz").open("ab") as file:
            file.write(b"CORRUPTION")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.manager.restore(backup)
        self.assertEqual(self.contents(), old)

    def test_pid_marker_prevents_live_update(self):
        (self.manager.control / "server.pid").write_text(str(os.getpid()))
        with self.assertRaises(RuntimeError):
            self.manager.snapshot()

    def test_exclusive_manager_lock(self):
        with self.manager.lock():
            with self.assertRaises(RuntimeError):
                with self.manager.lock():
                    pass

    def test_success_keeps_settings_and_backup(self):
        stage, plugins = self.stage()
        with patch.object(self.manager, "serve", return_value=0):
            self.manager.apply(manifest(), "d" * 64, stage, Path("/new/python"), [plugins])
        self.assertEqual((self.root / "bedrock_server").read_bytes(), b"NEW_BDS")
        self.assertEqual((self.root / "server.properties").read_bytes(), b"USER_SETTINGS")
        self.assertEqual((self.root / "plugins/private/data.json").read_bytes(), b"PRIVATE_DATA")
        self.assertTrue(Path(self.manager.current()["backup"]).is_dir())

    def test_stable_gate_rejects_missing_checks_wrong_commit_and_official_artifact(self):
        valid = manifest()
        module.validate_manifest(valid, "Yumiiiiiiiiii/endstone", "1.26.60")
        for mutate in [lambda m: m["validation"]["checks"].pop("wings"),
                       lambda m: m["validation"].update(commit="0" * 40),
                       lambda m: m["wheel"].update(url="https://github.com/EndstoneMC/endstone/releases/download/v1/x.whl"),
                       lambda m: m["bds"].update(version="1.26.52"),
                       lambda m: m["wheel"].update(sha256="bad")]:
            bad = copy.deepcopy(valid)
            mutate(bad)
            with self.assertRaises(ValueError):
                module.validate_manifest(bad, "Yumiiiiiiiiii/endstone", "1.26.60")

    def test_archive_path_traversal_rejected(self):
        for name in ["../worlds", "/etc/passwd", "C:/windows", "x\\y"]:
            with self.assertRaises(ValueError):
                module.safe_relative(name)


if __name__ == "__main__":
    unittest.main()
