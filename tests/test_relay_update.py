"""FriendJoin's Wheel and executable share the world backup transaction."""

import copy
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import test_manager as fixtures

module = fixtures.module


class RelayUpdateTests(unittest.TestCase):
    setUp = fixtures.UpdateTests.setUp
    tearDown = fixtures.UpdateTests.tearDown
    stage = fixtures.UpdateTests.stage
    contents = fixtures.UpdateTests.contents

    def relay_candidate(self):
        candidate = fixtures.manifest()
        repository = "example/private-plugins"
        base = f"https://github.com/{repository}/releases/download/ymii-plugins-test/"
        wheel = "endstone_friend_join-0.1.3-py3-none-any.whl"
        candidate["plugins"] = [{"filename": wheel, "url": base + wheel,
                                 "sha256": "d" * 64, "repository": repository, "private": True}]
        candidate["relay"] = {"filename": module.RELAY_PATH.name, "url": base + module.RELAY_PATH.name,
                              "sha256": "e" * 64, "repository": repository, "private": True,
                              "platform": "linux-x86_64"}
        stage, _ = self.stage()
        helper = stage / module.RELAY_PATH.name
        if os.name == "posix":
            shutil.copy2(shutil.which("true"), helper)
        else:
            helper.write_bytes(b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\x00")
        candidate["relay"]["sha256"] = module.digest(helper)
        plugin = stage / wheel
        plugin.write_bytes(b"NEW_FRIENDJOIN_WHEEL")
        previous = self.root / module.RELAY_PATH
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b"OLD_RELAY")
        previous.chmod(0o700)
        config = previous.parent.parent / "config.toml"
        config.write_text("autostart = false\n", encoding="utf-8")
        return candidate, stage, plugin

    def test_manifest_requires_companion_wheel_and_supported_relay(self):
        candidate, _, _ = self.relay_candidate()
        module.validate_manifest(candidate, "Yumiiiiiiiiii/endstone", "1.26.60")
        for mutate in [lambda m: m.pop("relay"), lambda m: m.update(plugins=[]),
                       lambda m: m["relay"].update(platform="linux-arm64"),
                       lambda m: m["relay"].update(filename="../../relay"),
                       lambda m: m["relay"].update(sha256="invalid"),
                       lambda m: m["relay"].update(url="https://example.com/relay")]:
            bad = copy.deepcopy(candidate)
            mutate(bad)
            with self.assertRaises(ValueError):
                module.validate_manifest(bad, "Yumiiiiiiiiii/endstone", "1.26.60")

    def test_relay_and_wheel_installed_before_health_and_config_preserved(self):
        candidate, stage, plugin = self.relay_candidate()
        destination = self.root / module.RELAY_PATH

        def health(*_, **__):
            self.assertEqual(module.digest(destination), candidate["relay"]["sha256"])
            self.assertTrue((self.root / "plugins" / plugin.name).is_file())
            self.assertEqual((destination.parent.parent / "config.toml").read_text(), "autostart = false\n")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
                subprocess.run([str(destination)], check=True, timeout=5)
            return 0

        with patch.object(self.manager, "serve", side_effect=health):
            self.manager.apply(candidate, "f" * 64, stage, Path("/new/python"), [plugin])
        self.assertEqual(self.manager.current()["manifest"]["relay"], candidate["relay"])

    def test_failed_health_restores_old_relay_world_and_wheel(self):
        candidate, stage, plugin = self.relay_candidate()
        old = self.contents()
        destination = self.root / module.RELAY_PATH
        permissions = stat.S_IMODE(destination.stat().st_mode)

        def fail(*_, **__):
            self.assertEqual(module.digest(destination), candidate["relay"]["sha256"])
            (self.root / "worlds/world/db/data").write_bytes(b"NEW_WORLD_FORMAT")
            return 1

        with patch.object(self.manager, "serve", side_effect=fail), patch.dict(os.environ, {"ROLLBACK_ON_FAILURE": "true"}):
            with self.assertRaisesRegex(RuntimeError, "health trial"):
                self.manager.apply(candidate, "f" * 64, stage, Path("/new/python"), [plugin])
        self.assertEqual(self.contents(), old)
        self.assertEqual(self.manager.current(), self.old_state)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), permissions)

    def test_changed_staged_relay_never_commits(self):
        candidate, stage, plugin = self.relay_candidate()
        old = self.contents()
        (stage / module.RELAY_PATH.name).write_bytes(b"CORRUPTED_RELAY")
        with patch.object(self.manager, "serve") as health, patch.dict(os.environ, {"ROLLBACK_ON_FAILURE": "true"}):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                self.manager.apply(candidate, "f" * 64, stage, Path("/new/python"), [plugin])
            health.assert_not_called()
        self.assertEqual(self.contents(), old)

    def test_elf_validator_rejects_wrong_architecture_and_truncation(self):
        candidate, stage, _ = self.relay_candidate()
        path = stage / module.RELAY_PATH.name
        original = path.read_bytes()
        module.check_relay(path)
        for raw in [original[:19], b"#!/bin/sh\n", original[:18] + b"\xb7\x00" + original[20:]]:
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "Linux x86-64"):
                module.check_relay(path)
