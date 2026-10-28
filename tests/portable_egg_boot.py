"""Exercise the generated startup on the unchanged official Python 3.14 yolk."""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    egg = json.loads((repo / "egg-endstone-migrate.json").read_text("utf-8"))
    image = "ghcr.io/parkervcp/yolks:python_3.14"
    with tempfile.TemporaryDirectory(prefix="portable-egg-") as temp:
        root = Path(temp)
        root.chmod(0o777)  # disposable host fixture; Docker writes as UID 1000
        run = subprocess.run(["docker", "run", "--rm", "--user", "1000:1000",
            "-e", "STARTUP=" + egg["startup"], "-e", "BDS_VERSION=1.26.60",
            "-e", "UPDATE_CHANNEL=stable", "-v", str(root) + ":/home/container", image],
            capture_output=True, text=True, timeout=300)
        report = {"image": image, "uid": 1000, "generated_egg_startup": True,
                  "python_runtime": [json.loads(p.read_text()) for p in root.glob(".fork/runtimes/*/verified.json")],
                  "server_files_created": [p.name for p in root.iterdir() if p.name != ".fork"],
                  "empty_server_rejected": run.returncode != 0 and "Migration requires an existing" in run.stdout + run.stderr}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Do not echo the startup's large base64 payload into Actions logs.
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        # GitHub's host runner can use UID 1001. Remove only this disposable
        # control folder as its actual owner before TemporaryDirectory cleanup.
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--user", "1000:1000",
            "--entrypoint", "python", "-v", str(root) + ":/home/container", image,
            "-c", "import shutil;shutil.rmtree('/home/container/.fork',ignore_errors=True)"], check=True)
        assert report["python_runtime"] and report["empty_server_rejected"] and not report["server_files_created"], report
        print("Official Python 3.14 image: portable bootstrap passed; empty world safely rejected")


if __name__ == "__main__":
    main()
