"""Cold official-server migration fixtures; no runtime compatibility claims."""
import copy
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_manager as fixtures

manager_module = fixtures.module
spec = importlib.util.spec_from_file_location("migration", fixtures.ROOT / "runtime/migration.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def level_dat(version=(1, 26, 60, 6, 0)):
    name = b"lastOpenedWithVersion"
    body = b"\x0a\0\0\x09" + struct.pack("<H", len(name)) + name + b"\x03" + struct.pack("<i", len(version))
    body += struct.pack("<" + "i" * len(version), *version) + b"\0"
    return struct.pack("<II", 10, len(body)) + body


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "server"
        self.root.mkdir()
        self.manager = manager_module.Manager(self.root)
        files = {"bedrock_server": b"ORIGINAL_BDS_ELF", "version.txt": b"26.60",
                 "server.properties": b"level-name=Survival\nserver-port=19132\nonline-mode=true\n",
                 "endstone.toml": b"[network]\nipv6=false\n",
                 "worlds/Survival/level.dat": level_dat(), "worlds/Survival/db/players": b"INVENTORY",
                 "plugins/endstone_friend_join-0.1-py3-none-any.whl": b"OLD_PLUGIN",
                 "plugins/friend_join/token.json": b"FIXTURE_ONLY_NOT_A_REAL_TOKEN",
                 "plugins/discord_srv/data.sqlite": b"OLD_DATABASE",
                 ".local/lib/original-package.txt": b"OFFICIAL_RUNTIME",
                 "resource_packs/custom/manifest.json": b"CUSTOM_RESOURCE_PACK"}
        for name, raw in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.manifest = fixtures.manifest()
        self.manifest["bds"]["url"] = "https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-1.26.60.6.zip"
        self.candidate = self.manager.control / "fixture-stage"
        (self.candidate / "bds").mkdir(parents=True)
        (self.candidate / "bds/bedrock_server").write_bytes(files["bedrock_server"])
        (self.candidate / "bds/server.properties").write_bytes(b"WRONG_DEFAULTS")
        plugin = self.candidate / "endstone_friend_join-0.2-py3-none-any.whl"
        plugin.write_bytes(b"NEW_PLUGIN")
        self.plugins = [plugin]
        self.probe = patch.object(migration.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
            stdout=json.dumps({"python": [3, 14, 7], "endstone": "0.11.14", "native_bds": "26.60"}), stderr=""))
        self.probe.start()
        self.environment = patch.dict(os.environ, {"MIGRATION_ORIGINAL_PYTHON": sys.executable, "ROLLBACK_ON_FAILURE": "false"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.probe.stop()
        self.temp.cleanup()

    def files(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*")
                if p.is_file() and ".fork" not in p.relative_to(self.root).parts}

    def run_migration(self, health=0, stage_error=None):
        with patch.object(self.manager, "stage", return_value=(self.candidate, Path("/retained/fork/python"), self.plugins),
                          side_effect=stage_error), patch.object(self.manager, "serve", return_value=health):
            return migration.migrate(self.manager, self.manifest, "a" * 64)

    def test_preserves_existing_bds_world_settings_packs_and_plugin_data(self):
        before = self.files()
        state = self.run_migration()
        after = self.files()
        for name, raw in before.items():
            if name.endswith(".whl"):
                self.assertNotIn(name, after)
            else:
                self.assertEqual(after[name], raw, name)
        self.assertEqual(state["migration"]["bds_preserved"], True)
        self.assertEqual(self.manager.current(), state)
        receipt = json.loads((self.manager.control / "migration.json").read_text())
        self.assertEqual(receipt["state"], "committed")
        self.assertEqual(receipt["original"]["runtime"]["endstone"], "0.11.14")
        self.assertTrue((Path(state["backup"]) / "server.tar.gz").is_file())

    def test_health_failure_restores_all_files_even_when_normal_rollback_is_disabled(self):
        before = self.files()
        with self.assertRaisesRegex(RuntimeError, "health trial"):
            self.run_migration(health=1)
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.manager.current())
        self.assertEqual(json.loads((self.manager.control / "migration.json").read_text())["state"], "rolled_back")
        with self.assertRaisesRegex(RuntimeError, "Previous migration failed"):
            self.run_migration()

    def test_trial_world_and_sqlite_changes_are_restored(self):
        before = self.files()

        def failure(*_, **__):
            (self.root / "worlds/Survival/db/players").write_bytes(b"TRIAL_PLAYER_DATA")
            (self.root / "plugins/discord_srv/data.sqlite").write_bytes(b"TRIAL_DATABASE")
            raise RuntimeError("test process crash")

        with patch.object(self.manager, "stage", return_value=(self.candidate, Path("/python"), self.plugins)), patch.object(self.manager, "serve", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "process crash"):
                migration.migrate(self.manager, self.manifest, "a" * 64)
        self.assertEqual(self.files(), before)

    def test_different_bds_build_and_failed_stage_leave_original_intact(self):
        before = self.files()
        (self.candidate / "bds/bedrock_server").write_bytes(b"ANOTHER_BUILD")
        with self.assertRaisesRegex(ValueError, "exact BDS binary"):
            self.run_migration()
        self.assertEqual(self.files(), before)

    def test_backup_failure_stops_before_stage(self):
        before = self.files()
        with patch.object(self.manager, "snapshot", side_effect=OSError("disk full")), patch.object(self.manager, "stage") as stage:
            with self.assertRaises(OSError):
                migration.migrate(self.manager, self.manifest, "a" * 64)
            stage.assert_not_called()
        self.assertEqual(self.files(), before)

    def test_refuses_version_change_before_snapshot(self):
        self.manifest["bds"]["version"] = "1.26.52"
        with patch.object(self.manager, "snapshot") as backup:
            with self.assertRaisesRegex(ValueError, "keep the existing BDS version"):
                self.run_migration()
            backup.assert_not_called()

    def test_refuses_a_newer_world_even_in_an_inactive_world_folder(self):
        other = self.root / "worlds/Other/level.dat"
        other.parent.mkdir()
        other.write_bytes(level_dat((1, 26, 70, 0, 0)))
        with self.assertRaisesRegex(ValueError, "newer BDS"):
            self.run_migration()

    def test_missing_configured_world_never_creates_a_new_world(self):
        (self.root / "server.properties").write_text("level-name=Missing\n")
        with self.assertRaisesRegex(ValueError, "replacement world"):
            self.run_migration()
        self.assertFalse((self.root / "worlds/Missing").exists())

    def test_unknown_or_malformed_world_version_is_rejected(self):
        path = self.root / "worlds/Survival/level.dat"
        for raw in [b"invalid", level_dat()[:-1], level_dat((1, 26))]:
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                migration.inventory(self.manager)

    def test_world_version_must_be_an_int_list_not_a_long_list(self):
        path = self.root / "worlds/Survival/level.dat"
        name = b"lastOpenedWithVersion"
        body = b"\x0a\0\0\x09" + struct.pack("<H", len(name)) + name + b"\x04" + struct.pack("<i", 4)
        body += struct.pack("<4q", 1, 26, 60, 6) + b"\0"
        path.write_bytes(struct.pack("<II", 10, len(body)) + body)
        with self.assertRaisesRegex(ValueError, "saved world version"):
            migration.inventory(self.manager)

    def test_same_family_newer_build_is_rejected_before_snapshot(self):
        (self.root / "worlds/Survival/level.dat").write_bytes(level_dat((1, 26, 60, 7, 0)))
        with patch.object(self.manager, "snapshot") as backup:
            with self.assertRaisesRegex(ValueError, "newer BDS build"):
                self.run_migration()
            backup.assert_not_called()

    def test_unavailable_globally_installed_plugin_is_not_silently_lost(self):
        self.probe.stop()
        probe = {"python": [3, 14, 7], "endstone": "0.11.14", "native_bds": "26.60",
                 "plugins": {"endstone-original-private-plugin": "1.0"}}
        with patch.object(migration.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(probe))), patch.object(self.manager, "snapshot") as backup:
            with self.assertRaisesRegex(ValueError, "matching Wheel artifacts"):
                self.run_migration()
            backup.assert_not_called()

    def test_interrupted_apply_restores_before_a_new_attempt(self):
        before = self.files()
        backup = self.manager.snapshot()
        (self.root / "bedrock_server").write_bytes(b"PARTIAL_UPDATE")
        manager_module.atomic_json(self.manager.control / "migration.json", {"state": "applying", "backup": str(backup)})
        with self.assertRaisesRegex(RuntimeError, "Interrupted migration restored"):
            self.run_migration()
        self.assertEqual(self.files(), before)

    def test_external_database_requires_a_coordinated_backup(self):
        (self.root / "plugins/discord_srv/config.toml").write_text('[storage]\nbackend = "mysql"\n')
        with self.assertRaisesRegex(ValueError, "coordinated database snapshot"):
            self.run_migration()

    def test_actual_discord_jdbc_config_is_detected_but_template_is_allowed(self):
        config = self.root / "plugins/discord_srv/config.yml"
        config.write_text('Experiment_JdbcAccountLinkBackend: "jdbc:mysql://HOST:PORT/DATABASE"\n')
        migration.inventory(self.manager)
        config.write_text('Experiment_JdbcAccountLinkBackend: "jdbc:mysql://db.internal/live"\n')
        with self.assertRaisesRegex(ValueError, "coordinated database snapshot"):
            migration.inventory(self.manager)

    def test_restore_failure_never_starts_original_or_candidate(self):
        def restore_failed(*_):
            manager_module.atomic_json(self.manager.control / "migration.json", {"state": "applying"})
            raise OSError("restore failed")

        with patch.object(manager_module, "Manager", return_value=self.manager), \
             patch.object(self.manager, "release_manifest", return_value=(self.manifest, "a" * 64)), \
             patch.object(self.manager, "migrate_official", side_effect=restore_failed), \
             patch.object(self.manager, "serve_original") as original, patch.object(self.manager, "serve") as serve, \
             patch.dict(os.environ, {"MIGRATE_OFFICIAL": "true", "UPDATE_REQUIRE_APPROVAL": "false", "BACKUP_BEFORE_UPDATE": "true"}), \
             patch.object(sys, "argv", ["manager.py", "start", "--root", str(self.root)]):
            with self.assertRaisesRegex(OSError, "restore failed"):
                manager_module.main()
            original.assert_not_called()
            serve.assert_not_called()

    def test_trial_blocks_player_logins_and_restores_exact_settings_on_exception(self):
        properties = (self.root / "server.properties").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "trial error"):
            with self.manager.trial_access():
                self.assertIn("allow-list=true", (self.root / "server.properties").read_text())
                self.assertEqual(json.loads((self.root / "allowlist.json").read_text()), [])
                raise RuntimeError("trial error")
        self.assertEqual((self.root / "server.properties").read_bytes(), properties)
        self.assertFalse((self.root / "allowlist.json").exists())

    @unittest.skipUnless(sys.platform == "linux", "Linux lock recovery")
    def test_dead_manager_marker_recovers_but_live_process_is_blocked(self):
        (self.manager.control / "manager.lock").write_text("999999999")
        (self.manager.control / "server.pid").write_text("999999999")
        with self.manager.lock():
            self.assertFalse((self.manager.control / "server.pid").exists())
            with self.assertRaises(RuntimeError):
                with self.manager.lock():
                    pass
