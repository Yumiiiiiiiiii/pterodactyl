"""Generate the Fork Egg without changing the panel's existing UI components."""

import json
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


if __name__ == "__main__":
    main()
