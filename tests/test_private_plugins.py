"""Private release authentication and redirect boundaries; no real credentials."""
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from test_manager import module


class Response(io.BytesIO):
    def __init__(self, url, data):
        super().__init__(data)
        self.url = url


class PrivatePluginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manager = module.Manager(self.root)
        self.repository = "Yumiiiiiiiiii/endstone-private-plugins"
        self.payload = b"fixture wheel bytes"
        self.item = {"repository": self.repository, "private": True, "filename": "plugin-1-py3-none-any.whl",
                     "url": f"https://github.com/{self.repository}/releases/download/ymii-test/plugin-1-py3-none-any.whl",
                     "sha256": hashlib.sha256(self.payload).hexdigest()}
        self.path = self.root / "wheel.whl"
        folder = self.manager.control / "credentials"
        folder.mkdir()
        self.credential = folder / "github-token"
        self.credential.write_text("fixture_credential_never_send_to_storage")
        self.credential.chmod(0o600)
        self.environment = patch.dict(os.environ, {"PLUGIN_REPOSITORIES": self.repository})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def opener(self, wrong_hash=False, draft=False):
        parent = self

        class Opener:
            def open(self, request, timeout):
                parent.assertEqual(request.get_header("Authorization"), "Bearer fixture_credential_never_send_to_storage")
                parent.assertEqual(urllib.request.urlparse(request.full_url).hostname, "api.github.com")
                parent.assertNotIn("Authorization", request.headers)
                if "/releases/tags/" in request.full_url:
                    body = {"draft": draft, "tag_name": "ymii-test", "assets": [{"name": parent.item["filename"], "id": 123}]}
                    return Response(request.full_url, json.dumps(body).encode())
                parent.assertEqual(request.full_url, f"https://api.github.com/repos/{parent.repository}/releases/assets/123")
                parent.assertEqual(request.get_header("Accept"), "application/octet-stream")
                return Response("https://release-assets.githubusercontent.com/signed-fixture", b"bad" if wrong_hash else parent.payload)

        return Opener()

    def test_authenticated_release_asset_and_hash(self):
        with patch.object(module.urllib.request, "build_opener", return_value=self.opener()):
            self.manager.plugin_artifact(self.item, self.path)
        self.assertEqual(self.path.read_bytes(), self.payload)

    def test_redirect_never_forwards_authorization(self):
        request = urllib.request.Request("https://api.github.com/repos/example/plugins/releases/assets/1")
        request.add_header("Authorization", "Bearer fixture_credential_never_send_to_storage")
        for target in ("https://release-assets.githubusercontent.com/signed-fixture", "https://api.github.com/new"):
            redirected = module.DownloadRedirect().redirect_request(request, None, 302, "Found", {}, target)
            self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaisesRegex(ValueError, "outside HTTPS"):
            module.DownloadRedirect().redirect_request(request, None, 302, "Found", {}, "http://example.com/asset")

    def test_wrong_hash_removes_download_and_never_changes_active_world(self):
        world = self.root / "world.dat"
        world.write_bytes(b"untouched world")
        with patch.object(module.urllib.request, "build_opener", return_value=self.opener(wrong_hash=True)):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                self.manager.plugin_artifact(self.item, self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(world.read_bytes(), b"untouched world")

    def test_unlisted_repository_or_missing_credential_never_requests_network(self):
        with patch.object(module.urllib.request, "build_opener") as network:
            with patch.dict(os.environ, {"PLUGIN_REPOSITORIES": ""}):
                with self.assertRaisesRegex(ValueError, "not allowed"):
                    self.manager.plugin_artifact(self.item, self.path)
            self.credential.unlink()
            with self.assertRaises(FileNotFoundError):
                self.manager.plugin_artifact(self.item, self.path)
            network.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX permission check")
    def test_world_readable_credential_is_rejected(self):
        self.credential.chmod(0o644)
        with patch.object(module.urllib.request, "build_opener") as network:
            with self.assertRaisesRegex(ValueError, "0600"):
                self.manager.plugin_artifact(self.item, self.path)
            network.assert_not_called()

    def test_draft_release_is_rejected(self):
        with patch.object(module.urllib.request, "build_opener", return_value=self.opener(draft=True)):
            with self.assertRaisesRegex(ValueError, "published"):
                self.manager.plugin_artifact(self.item, self.path)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
