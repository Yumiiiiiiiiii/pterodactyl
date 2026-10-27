#!/usr/bin/env python3
"""Fork Release installer and stopped-server transactional updater for Wings."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.parse import quote

CHECKS = {"native_abi", "boot", "commands", "scheduler", "world_save_restart", "player_events",
          "block_entity", "network", "plugins", "docker", "wings"}
HEX = re.compile(r"[0-9a-f]{64}")
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+")
RELAY_PATH = Path("plugins/friend_join/bin/endstone-friend-bridge")


def plugin_assets(manifest):
    return [*manifest.get("plugins", []), *([manifest["relay"]] if manifest.get("relay") else [])]


def check_relay(path):
    with path.open("rb") as stream:
        header = stream.read(20)
    if len(header) != 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
        raise ValueError("Relay must be Linux x86-64 ELF")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def bool_env(name, default=True):
    value = os.environ.get(name, str(default)).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def safe_relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def validate_manifest(data, repository, bds_version, channel="stable"):
    if data.get("schema") != 1 or data.get("repository") != repository:
        raise ValueError("Manifest schema or Fork repository mismatch")
    if data.get("channel") != channel or channel not in {"stable", "candidate"}:
        raise ValueError("Manifest channel mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", data.get("commit", "")):
        raise ValueError("Manifest must identify a full Fork commit")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", data.get("release", "")):
        raise ValueError("Invalid release tag")
    if data.get("bds", {}).get("version") != bds_version:
        raise ValueError("Requested BDS version has no matching verified release")
    if not re.fullmatch(r"1\.\d+\.\d+", bds_version):
        raise ValueError("Invalid BDS version")
    if "+ymii.g" not in data.get("endstone_version", ""):
        raise ValueError("Official or unidentified Endstone wheel is not allowed")
    python_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if data.get("python_tag") != python_tag:
        raise ValueError("Release Python ABI does not match this container")
    checks = data.get("validation", {})
    if channel == "stable" and (checks.get("commit") != data["commit"] or
                                any(checks.get("checks", {}).get(key) != "passed" for key in CHECKS)):
        raise ValueError("Stable release is missing required runtime checks for this commit")
    extras = plugin_assets(data)
    artifacts = [data.get("wheel", {}), data["bds"], *extras]
    wheel_name = data.get("wheel", {}).get("filename", "")
    if not re.fullmatch(rf"endstone-[A-Za-z0-9_.+]+-{python_tag}-{python_tag}-manylinux_[A-Za-z0-9_]*x86_64\.whl", wheel_name):
        raise ValueError("Expected a Fork Linux x86-64 wheel matching the container Python ABI")
    plugin_names = set()
    for item in artifacts:
        if not HEX.fullmatch(item.get("sha256", "")):
            raise ValueError("Missing artifact SHA256")
        parsed = urlparse(item.get("url", ""))
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Artifacts require plain HTTPS URLs")
        if item is data["bds"]:
            if parsed.hostname != "www.minecraft.net" or not parsed.path.startswith("/bedrockdedicatedserver/bin-linux/"):
                raise ValueError("BDS artifact must use Mojang's Linux release endpoint")
        else:
            expected_repo = item.get("repository", repository) if item in extras else repository
            if not REPO.fullmatch(expected_repo) or expected_repo.lower().startswith("endstonemc/"):
                raise ValueError("Invalid Fork/plugin artifact repository")
            if parsed.hostname != "github.com" or not parsed.path.startswith(f"/{expected_repo}/releases/download/"):
                raise ValueError("Endstone and plugin artifacts must come from the configured repositories")
        if item in extras:
            name = item.get("filename", "")
            relay = item is data.get("relay")
            if safe_relative(name).name != name or (not relay and not name.endswith(".whl")) or name in plugin_names:
                raise ValueError("Invalid or duplicate plugin wheel name")
            if relay and (name != RELAY_PATH.name or item.get("platform") != "linux-x86_64"):
                raise ValueError("Unsupported relay filename or platform")
            if type(item.get("private", False)) is not bool:
                raise ValueError("Plugin private flag must be a boolean")
            if item.get("private") and not item.get("repository"):
                raise ValueError("Private plugins require an explicit repository")
            plugin_names.add(name)
    friend_join = any(item["filename"].startswith("endstone_friend_join-") for item in data.get("plugins", []))
    if bool(data.get("relay")) != friend_join:
        raise ValueError("FriendJoin Wheel and relay must be updated together")
    return data


class DownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise ValueError("Download redirected outside HTTPS")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        # GitHub redirects private assets to a signed object-storage URL. The
        # repository credential must never accompany that redirect.
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def fetch(url, path, sha256=None, *, github_token=None):
    if sha256 and path.is_file() and digest(path) == sha256:
        return
    if urlparse(url).scheme != "https":
        raise ValueError("Only HTTPS downloads are permitted")
    request = urllib.request.Request(url, headers={"User-Agent": "Ymii-Endstone-Fork/1"})
    if github_token:
        if urlparse(url).hostname != "api.github.com":
            raise ValueError("GitHub credentials are restricted to api.github.com")
        request.add_unredirected_header("Authorization", "Bearer " + github_token)
        request.add_header("Accept", "application/octet-stream")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(DownloadRedirect())
    with opener.open(request, timeout=60) as response, path.open("wb") as stream:
        if urlparse(response.url).scheme != "https":
            raise ValueError("Download redirected outside HTTPS")
        shutil.copyfileobj(response, stream, 1024 * 1024)
    if sha256 and digest(path) != sha256:
        path.unlink()
        raise ValueError("Artifact SHA256 mismatch; active server unchanged")


class Manager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.control = self.root / ".fork"
        self.control.mkdir(parents=True, exist_ok=True)
        self.state_path = self.control / "current.json"

    def current(self):
        return json.loads(self.state_path.read_text("utf-8")) if self.state_path.exists() else None

    def plugin_artifact(self, item, path):
        if not item.get("private", False):
            return fetch(item["url"], path, item["sha256"])
        repository = item["repository"]
        allowed = {value.strip() for value in os.environ.get("PLUGIN_REPOSITORIES", "").split(",") if value.strip()}
        if repository not in allowed:
            raise ValueError("Private plugin repository is not allowed")
        parsed = urlparse(item["url"])
        if not REPO.fullmatch(repository) or parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.query or parsed.fragment:
            raise ValueError("Invalid private plugin release URL")
        # Credentials are operator-provisioned files, never Egg variables,
        # manifests, image layers, environment variables or command arguments.
        folder = self.control / "credentials"
        credential = folder / "github-token"
        if folder.is_symlink() or credential.is_symlink():
            raise ValueError("GitHub credential paths must not be symlinks")
        descriptor = os.open(credential, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "r", encoding="ascii") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid())):
                raise ValueError("GitHub credential must be owned by the container user with mode 0600")
            token = stream.read(1025).strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{20,1024}", token):
            raise ValueError("Invalid GitHub credential file")
        # Resolve an asset ID within the allowlisted repository and the exact
        # release URL. Never accept an arbitrary authenticated URL from a manifest.
        match = re.fullmatch(r"/" + re.escape(repository) + r"/releases/download/([A-Za-z0-9_.+-]+)/([^/]+)", urlparse(item["url"]).path)
        if not match or match[2] != item["filename"]:
            raise ValueError("Private plugin URL must identify its exact release and asset")
        api = f"https://api.github.com/repos/{repository}/releases/tags/{quote(match[1], safe='')}"
        request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json", "User-Agent": "Ymii-Endstone-Fork/1",
                                                       "X-GitHub-Api-Version": "2022-11-28"})
        request.add_unredirected_header("Authorization", "Bearer " + token)
        with urllib.request.build_opener(DownloadRedirect()).open(request, timeout=30) as response:
            if response.url != api:
                raise ValueError("Private release metadata redirected; confirm repository ownership")
            release = json.load(response)
        if release.get("draft") is not False or release.get("tag_name") != match[1]:
            raise ValueError("Private plugin release must be published with the exact tag")
        assets = [asset for asset in release.get("assets", []) if asset.get("name") == item["filename"]]
        if len(assets) != 1 or type(assets[0].get("id")) is not int or assets[0]["id"] <= 0:
            raise ValueError("Private plugin release asset is missing or ambiguous")
        asset_url = f"https://api.github.com/repos/{repository}/releases/assets/{assets[0]['id']}"
        fetch(asset_url, path, item["sha256"], github_token=token)

    @contextlib.contextmanager
    def lock(self):
        path = self.control / "manager.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError("Another manager is active. If it crashed, verify its PID before removing .fork/manager.lock") from None
        try:
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            self.assert_stopped()
            yield
        finally:
            path.unlink(missing_ok=True)

    def assert_stopped(self):
        if (self.control / "server.pid").exists():
            raise RuntimeError("Server PID marker exists; refuse to update a running or uncleanly stopped server")
        if sys.platform == "linux":
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit() or int(entry.name) == os.getpid():
                    continue
                try:
                    command = (entry / "cmdline").read_bytes().split(b"\0")
                    if any(Path(os.fsdecode(arg)).name == "bedrock_server" for arg in command if arg):
                        if (entry / "cwd").resolve() == self.root:
                            raise RuntimeError("A BDS process is using this directory")
                except (OSError, ProcessLookupError):
                    continue

    def snapshot(self):
        self.assert_stopped()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        folder = self.control / "backups" / stamp
        folder.mkdir(parents=True)
        archive = folder / "server.tar.gz"
        # Back up ALL mutable files: worlds (including player data), plugin data,
        # configuration and old binaries. Immutable venvs stay in .fork/envs.
        with tarfile.open(archive, "w:gz") as tar:
            for item in self.root.iterdir():
                if item.name == ".fork":
                    continue
                for candidate in [item, *item.rglob("*")] if item.is_dir() else [item]:
                    if candidate.is_symlink():
                        raise ValueError(f"Snapshot refuses a symlink: {candidate}")
                tar.add(item, arcname=item.name, recursive=True)
        with tarfile.open(archive, "r:gz") as tar:
            for item in tar:
                safe_relative(item.name)
                if not (item.isfile() or item.isdir()):
                    raise ValueError("Backup contains a non-file entry")
                if item.isfile():
                    stream = tar.extractfile(item)
                    while stream.read(1024 * 1024):
                        pass
        atomic_json(folder / "backup.json", {"schema": 1, "created_at": now(), "root": str(self.root),
                                            "sha256": digest(archive), "current": self.current()})
        return folder

    def restore(self, backup: Path):
        self.assert_stopped()
        backup = backup.resolve()
        if backup.parent != (self.control / "backups").resolve():
            raise ValueError("Backup must belong to this server")
        metadata = json.loads((backup / "backup.json").read_text("utf-8"))
        archive = backup / "server.tar.gz"
        if metadata["root"] != str(self.root) or digest(archive) != metadata["sha256"]:
            raise ValueError("Backup identity or SHA256 validation failed")
        with tempfile.TemporaryDirectory(dir=self.control, prefix="restore-") as directory:
            staged = Path(directory)
            with tarfile.open(archive, "r:gz") as tar:
                for item in tar.getmembers():
                    safe_relative(item.name)
                    if not (item.isfile() or item.isdir()) or PurePosixPath(item.name).parts[0] == ".fork":
                        raise ValueError("Unsafe backup entry")
                tar.extractall(staged, filter="data")
            # Preserve a failed updated world separately; restore a complete snapshot.
            failed = self.control / "failed" / uuid.uuid4().hex
            failed.mkdir(parents=True)
            for item in list(self.root.iterdir()):
                if item.name != ".fork":
                    shutil.move(str(item), failed / item.name)
            for item in staged.iterdir():
                shutil.move(str(item), self.root / item.name)
            if metadata["current"] is None:
                self.state_path.unlink(missing_ok=True)
            else:
                atomic_json(self.state_path, metadata["current"])
        atomic_json(self.control / "last-rollback.json", {"restored_at": now(), "backup": str(backup)})

    def stage(self, manifest, manifest_hash):
        stage = self.control / "staging" / manifest_hash
        stage.mkdir(parents=True, exist_ok=True)
        wheel = stage / manifest["wheel"]["filename"]
        fetch(manifest["wheel"]["url"], wheel, manifest["wheel"]["sha256"])
        bds = stage / "bds.zip"
        fetch(manifest["bds"]["url"], bds, manifest["bds"]["sha256"])
        extracted = stage / "bds"
        extracted.mkdir(exist_ok=True)
        with zipfile.ZipFile(bds) as archive:
            for item in archive.infolist():
                relative = safe_relative(item.filename)
                if relative.parts and relative.parts[0] == ".fork":
                    raise ValueError("BDS archive contains a reserved path")
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("Symlinks are not allowed in BDS archives")
            archive.extractall(extracted)
        executable = extracted / "bedrock_server"
        with executable.open("rb") as stream:
            header = stream.read(20)
        if header[:5] != b"\x7fELF\x02" or header[18:20] != b"\x3e\x00":
            raise ValueError("BDS archive is not Linux x86-64")
        executable.chmod(0o755)
        venv = self.control / "envs" / manifest_hash
        if venv.exists():
            raise FileExistsError("Candidate venv exists from an earlier attempt; preserve and remove it before retry")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = venv / "bin/python"
        subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)
        constraints = venv / "fork-constraints.txt"
        constraints.write_text(f"endstone=={manifest['endstone_version']}\n", encoding="utf-8")
        installed = subprocess.check_output([str(python), "-c", "import importlib.metadata as m; print(m.version('endstone'))"],
                                            text=True).strip()
        if installed != manifest["endstone_version"]:
            raise ValueError("Installed Endstone version does not match manifest")
        native = subprocess.check_output([str(python), "-c", "from endstone import __minecraft_version__; print(__minecraft_version__)"],
                                         cwd=stage, text=True).strip()
        native = native if native.startswith("1.") else "1." + native
        if native != manifest["bds"]["version"]:
            raise ValueError("Wheel native ABI target does not match requested BDS")
        plugins = []
        if bool_env("AUTO_UPDATE_PLUGINS"):
            for item in manifest.get("plugins", []):
                path = stage / "plugin-wheels" / item["filename"]
                self.plugin_artifact(item, path)
                subprocess.run([str(python), "-m", "pip", "install", "--constraint", str(constraints), str(path)], check=True)
                plugins.append(path)
            if manifest.get("relay"):
                item = manifest["relay"]
                relay = stage / item["filename"]
                self.plugin_artifact(item, relay)
                check_relay(relay)
        return stage, python, plugins

    def apply(self, manifest, manifest_hash, stage, python, plugins):
        # A backup failure raises before any active file changes.
        backup = self.snapshot()
        atomic_json(self.control / "transaction.json", {"state": "applying", "backup": str(backup), "candidate": manifest_hash})
        previous = self.current()
        try:
            defaults = {"server.properties", "allowlist.json", "permissions.json", "endstone.toml"}
            if previous:
                new_paths = {p.relative_to(stage / "bds").as_posix() for p in (stage / "bds").rglob("*") if p.is_file()}
                for name in previous.get("bds_files", []):
                    safe_relative(name)
                    if name not in new_paths and name not in defaults:
                        path = self.root / name
                        if path.is_file():
                            path.unlink()
            bds_files = []
            for source in (stage / "bds").rglob("*"):
                relative = source.relative_to(stage / "bds")
                destination = self.root / relative
                if source.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    bds_files.append(relative.as_posix())
                    if relative.as_posix() in defaults and destination.exists():
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            (self.root / "version.txt").write_text(manifest["bds"]["version"].removeprefix("1."), encoding="utf-8")
            plugin_dir = self.root / "plugins"
            plugin_dir.mkdir(exist_ok=True)
            for source in plugins:
                distribution = source.name.split("-")[0]
                for old in plugin_dir.glob(distribution + "-*.whl"):
                    old.unlink()
                shutil.copy2(source, plugin_dir / source.name)
            if manifest.get("relay"):
                relay = stage / manifest["relay"]["filename"]
                if digest(relay) != manifest["relay"]["sha256"]:
                    raise ValueError("Staged relay SHA256 changed")
                check_relay(relay)
                destination = self.root / RELAY_PATH
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(relay, destination)
                destination.chmod(0o755)
            candidate = {"manifest": manifest, "manifest_sha256": manifest_hash, "python": str(python),
                         "installed_at": now(), "bds_files": bds_files, "backup": str(backup)}
            if self.serve(candidate, health_trial=True) != 0:
                raise RuntimeError("Candidate health trial failed")
            atomic_json(self.state_path, candidate)
            atomic_json(self.control / "transaction.json", {"state": "committed", "candidate": manifest_hash, "backup": str(backup)})
        except BaseException:
            if bool_env("ROLLBACK_ON_FAILURE"):
                self.restore(backup)
                atomic_json(self.control / "transaction.json", {"state": "rolled_back", "backup": str(backup)})
            raise

    def serve(self, state, health_trial=False):
        marker = self.control / "server.pid"
        logdir = self.control / "logs"
        logdir.mkdir(exist_ok=True)
        log = logdir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".log")
        env = os.environ.copy()
        env["ENDSTONE_PYTHON_EXECUTABLE"] = state["python"]
        env["PIP_CONSTRAINT"] = str(Path(state["python"]).parent.parent / "fork-constraints.txt")
        process = subprocess.Popen([state["python"], "-m", "endstone", "-s", str(self.root), "-y", "--no-interactive"],
                                   cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        marker.write_text(str(process.pid), encoding="ascii")
        started = threading.Event()
        failed = threading.Event()
        stopped = threading.Event()
        previous_handlers = {}

        def stop(*_):
            if process.poll() is None and not stopped.is_set():
                stopped.set()
                try:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                except OSError:
                    pass

        def output():
            with log.open("w", encoding="utf-8") as stream:
                for line in process.stdout:
                    stream.write(line)
                    stream.flush()
                    # Trial logs must not trigger Wings' configured readiness marker.
                    if not health_trial:
                        print(line, end="", flush=True)
                    if "Server started" in line:
                        started.set()
                    if re.search(r"failed to (load|enable)|could not (load|enable)|\[.*ERROR\].*(plugin|exception)|Traceback \(most recent", line, re.I):
                        failed.set()

        def forward_input():
            for line in sys.stdin:
                if process.poll() is not None:
                    break
                if line.strip().lower() == "stop":
                    stop()
                else:
                    try:
                        process.stdin.write(line)
                        process.stdin.flush()
                    except OSError:
                        break

        thread = threading.Thread(target=output, daemon=True)
        thread.start()
        if not health_trial:
            threading.Thread(target=forward_input, daemon=True).start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.signal(sig, stop)
        try:
            if health_trial:
                deadline = time.monotonic() + 180
                while not started.is_set() and process.poll() is None and time.monotonic() < deadline and not failed.is_set():
                    time.sleep(0.2)
                healthy = started.is_set() and not failed.is_set() and process.poll() is None
                if healthy:
                    time.sleep(10)
                    healthy = process.poll() is None and not failed.is_set()
                stop()
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    healthy = False
                    process.kill()
                    process.wait(timeout=10)
                thread.join(timeout=5)
                return 0 if healthy and not failed.is_set() and process.returncode == 0 else 1
            return process.wait()
        finally:
            stop()
            if process.poll() is None:
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            marker.unlink(missing_ok=True)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)

    def release_manifest(self):
        if os.environ.get("ENDSTONE_SOURCE", "custom-fork") != "custom-fork":
            raise ValueError("Only ENDSTONE_SOURCE=custom-fork is implemented")
        repository = os.environ.get("ENDSTONE_FORK", "Yumiiiiiiiiii/endstone").strip()
        if not REPO.fullmatch(repository) or repository.lower().startswith("endstonemc/"):
            raise ValueError("Specify a personal Fork as owner/repo")
        release = os.environ.get("ENDSTONE_RELEASE", "stable").strip()
        channel = os.environ.get("UPDATE_CHANNEL", "stable").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", release):
            raise ValueError("Invalid release selector")
        url = f"https://github.com/{repository}/releases/download/{release}/manifest.json"
        path = self.control / "downloaded-manifest.json"
        fetch(url, path)
        manifest = json.loads(path.read_text("utf-8"))
        validate_manifest(manifest, repository, os.environ.get("BDS_VERSION", "1.26.60"), channel)
        allowed_plugins = {name.strip() for name in os.environ.get("PLUGIN_REPOSITORIES", "").split(",") if name.strip()}
        if any(item.get("repository", repository) not in allowed_plugins for item in plugin_assets(manifest)):
            raise ValueError("Plugin release repository must be explicitly listed in PLUGIN_REPOSITORIES")
        return manifest, digest(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "prepare", "install", "start", "backup", "rollback"])
    parser.add_argument("--root", type=Path, default=Path("/home/container"))
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    manager = Manager(args.root)
    with manager.lock():
        transaction = manager.control / "transaction.json"
        if transaction.exists() and json.loads(transaction.read_text())["state"] == "applying" and args.action != "rollback":
            raise RuntimeError("Interrupted update found. Roll back the recorded snapshot before starting")
        if args.action == "backup":
            print(manager.snapshot())
            return 0
        if args.action == "rollback":
            if not args.backup:
                parser.error("rollback requires --backup")
            manager.restore(args.backup)
            atomic_json(transaction, {"state": "rolled_back", "backup": str(args.backup)})
            return 0
        current = manager.current()
        if args.action in {"check", "prepare", "install"} or (args.action == "start" and (not current or bool_env("AUTO_UPDATE_CHECK"))):
            try:
                manifest, checksum = manager.release_manifest()
                atomic_json(manager.control / "available.json", {"manifest": manifest, "sha256": checksum, "checked_at": now()})
            except Exception as exc:
                if current and args.action == "start":
                    print(f"Update check failed; keeping installed release: {exc}", flush=True)
                    manifest, checksum = None, None
                else:
                    raise
            if manifest and (not current or current["manifest_sha256"] != checksum):
                print(f"Verified release available: {manifest['release']}; manifest SHA256={checksum}", flush=True)
                if args.action == "prepare":
                    folder = manager.control / "staging" / checksum
                    for item, target in [(manifest["wheel"], folder / manifest["wheel"]["filename"]),
                                         (manifest["bds"], folder / "bds.zip")]:
                        fetch(item["url"], target, item["sha256"])
                    print("Fork Release artifacts downloaded and SHA256 verified; runtime installation happens at /home/container")
                    return 0
                if args.action == "check":
                    return 0
                approved = os.environ.get("UPDATE_APPROVED_MANIFEST", "").strip() == checksum
                requires_approval = bool_env("UPDATE_REQUIRE_APPROVAL")
                if requires_approval and not approved:
                    if not current:
                        raise RuntimeError("Set UPDATE_APPROVED_MANIFEST to the displayed SHA256 before first install")
                    print("Update is waiting for manifest approval; keeping installed release", flush=True)
                else:
                    if not bool_env("BACKUP_BEFORE_UPDATE"):
                        raise ValueError("Backup is mandatory; BACKUP_BEFORE_UPDATE must be true")
                    if plugin_assets(manifest) and not bool_env("AUTO_UPDATE_PLUGINS"):
                        raise ValueError("Release contains plugin updates; cannot keep a mixed unverified plugin set")
                    stage, python, plugins = manager.stage(manifest, checksum)
                    manager.apply(manifest, checksum, stage, python, plugins)
                    current = manager.current()
        if args.action == "start":
            if not current:
                raise RuntimeError("No verified Fork release installed. Run install first")
            requested = os.environ.get("BDS_VERSION", "1.26.60")
            if current["manifest"]["bds"]["version"] != requested:
                raise ValueError("Installed BDS differs from configured BDS_VERSION")
            if bool_env("DEBUG", False):
                print(f"Fork release: {current['manifest']['release']}; commit: {current['manifest']['commit']}")
            return manager.serve(current)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Fork manager: {error}", file=sys.stderr)
        raise SystemExit(1) from error
