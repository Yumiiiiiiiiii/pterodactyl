"""Generate the Fork Egg without changing the panel's existing UI components."""

import json
import base64
import hashlib
import io
import shlex
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def variable(label, name, value, description, rules="required|string", editable=False):
    return {"name": label, "env_variable": name, "default_value": value, "description": description,
            "user_viewable": True, "user_editable": editable, "rules": rules, "field_type": "text"}


def main():
    egg = json.loads((ROOT / "egg-endstone.json").read_text("utf-8"))
    image = "ghcr.io/yumiiiiiiiiii/endstone-wings:stable"
    egg.update({
        "name": "Endstone Fork (検証済み更新)",
        "author": "77167437+Yumiiiiiiiiii@users.noreply.github.com",
        "description": "自分のForkの検証済みReleaseから導入します。更新は停止中にバックアップと承認を確認し、起動失敗時は元の環境へ復旧します。BDS 1.26.60の検証済みReleaseはまだありません。",
        "docker_images": {"Fork runtime / Python 3.13": image},
        "startup": "python /opt/endstone-fork/manager.py start --root /home/container",
        "file_denylist": [".fork/credentials/*"],
        "scripts": {"installation": {
            "container": image + "-installer",
            "entrypoint": "bash",
            "script": "#!/bin/bash\nset -euo pipefail\nmkdir -p /mnt/server\npython /opt/endstone-fork/manager.py prepare --root /mnt/server\n",
        }},
        "variables": [
            variable("Endstone取得元", "ENDSTONE_SOURCE", "custom-fork", "custom-fork固定。本人ForkのReleaseを使用します。", "required|in:custom-fork"),
            variable("Endstone Fork", "ENDSTONE_FORK", "Yumiiiiiiiiii/endstone", "GitHubの所有者/リポジトリ。Releaseとmanifestの所有者を照合します。"),
            variable("固定Release", "ENDSTONE_RELEASE", "stable", "GitHub Releaseのタグ。stableは検証済み更新用。特定タグを指定するとその版に固定します。"),
            variable("BDSバージョン", "BDS_VERSION", "1.26.60", "検証済みmanifestと一致するBDSだけ導入します。未対応の版は起動前に停止します。"),
            variable("更新チャンネル", "UPDATE_CHANNEL", "stable", "stableは実行時テスト必須。candidateは開発用です。", "required|in:stable,candidate"),
            variable("起動前に更新確認", "AUTO_UPDATE_CHECK", "true", "通常の起動時に確認します。実行中のサーバーを再起動する機能ではありません。", "required|in:true,false"),
            variable("プラグインも更新", "AUTO_UPDATE_PLUGINS", "true", "検証済みmanifestに記載されたプラグインを同時に導入します。", "required|in:true,false"),
            variable("更新前バックアップ", "BACKUP_BEFORE_UPDATE", "true", "true必須。ワールド、プラグイン、設定、現在の版を保存できなければ更新しません。", "required|in:true"),
            variable("失敗時に復旧", "ROLLBACK_ON_FAILURE", "true", "起動確認に失敗したらBDS、Endstone、プラグイン、ワールドを一組で戻します。", "required|in:true,false"),
            variable("更新に承認が必要", "UPDATE_REQUIRE_APPROVAL", "true", "trueでは下のSHA256が一致した更新だけ適用します。承認待ちは現在の版を維持します。", "required|in:true,false"),
            variable("承認するmanifestのSHA256", "UPDATE_APPROVED_MANIFEST", "", "更新確認で表示された64桁のSHA256を貼り付けます。初回導入にも必要です。", "nullable|string|size:64"),
            variable("プラグイン配布リポジトリ", "PLUGIN_REPOSITORIES", "", "許可するGitHub所有者/リポジトリをカンマで区切ります。ソースはこの公開Forkへ入れません。", "nullable|string"),
            variable("詳細ログ", "DEBUG", "false", "起動時にReleaseとソースcommitを表示します。認証情報は表示しません。", "required|in:true,false", True),
        ],
    })
    egg["config"]["startup"] = json.dumps({"done": "Server started."})
    egg["exported_at"] = "2026-10-28T00:00:00+09:00"
    (ROOT / "egg-endstone-fork.json").write_text(json.dumps(egg, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    # A self-contained startup works with the existing official image. No
    # custom image, git clone, shell downloader or reinstall is required.
    payload = io.BytesIO()
    # libbz2 emits stable bytes across Python/zlib versions on Windows/Linux.
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_BZIP2) as bundle:
        for name in ("manager.py", "migration.py", "portable.py", "python-runtime.json"):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_BZIP2
            bundle.writestr(entry, (ROOT / "runtime" / name).read_bytes().replace(b"\r\n", b"\n"))
    encoded = base64.b64encode(payload.getvalue()).decode("ascii")
    checksum = hashlib.sha256(payload.getvalue()).hexdigest()
    boot = ("import base64,hashlib,io,os,sys,zipfile;from pathlib import Path;"
            f"data=base64.b64decode('{encoded}');assert hashlib.sha256(data).hexdigest()=='{checksum}';"
            "root=Path.cwd();control=root/'.fork';assert not control.is_symlink();"
            f"folder=control/'bootstrap'/'{checksum}';"
            "assert not any(p.is_symlink() for p in [control/'bootstrap',folder]);folder.mkdir(parents=True,exist_ok=True);"
            "z=zipfile.ZipFile(io.BytesIO(data));"
            "assert not any((folder/n).is_symlink() for n in z.namelist());"
            "[(folder/n).write_bytes(z.read(n)) for n in z.namelist()];"
            "os.execv(sys.executable,[sys.executable,str(folder/'portable.py')])")
    migration = json.loads(json.dumps(egg))
    migration.update(name="Endstone Fork (公式版から移行・再インストール不要)",
                     description="停止→このEggへ変更→起動。既存Pythonイメージと同じBDSを保持し、全体バックアップ後に検証済みForkへ移行します。互換Releaseがなければ整合性を確認した既存公式版を起動し、不整合があれば停止します。",
                     startup="python -c " + shlex.quote(boot))
    official = json.loads((ROOT / "egg-endstone.json").read_text("utf-8"))
    migration["docker_images"] = official["docker_images"]
    for item in migration["variables"]:
        if item["env_variable"] == "UPDATE_REQUIRE_APPROVAL":
            item["description"] = "初回だけはこの移行用Eggの選択を承認として検証済みstableを適用します。移行後の更新ではこの設定に従います。"
    migration["scripts"]["installation"] = {
        "container": "ghcr.io/pterodactyl/installers:alpine", "entrypoint": "ash",
        "script": "#!/bin/ash\nset -eu\necho 'Migration Egg: installation does not modify server files. Start the existing server to migrate.'\n"}
    # Wings must not rewrite allocation or properties before the cold snapshot.
    migration["config"]["files"] = "{}"
    (ROOT / "egg-endstone-migrate.json").write_text(json.dumps(migration, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
