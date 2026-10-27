# Fork版EndstoneをWingsから動かす

## 現在の状態

このEggと更新管理コードは開発中です。BDS 1.26.60の検証済みEndstone Release、
GHCRのstableイメージはまだありません。実JexactylへのEggインポートと、非公開の比較用imageを使った
Wingsのinstall・BDS1.26.52の起動停止は確認しました。要求の1.26.60への対応完了とは扱いません。
既定の1.26.60を指定したまま、公式Wheelや1.26.52へ自動的に置き換えることはありません。
未配布のstable URLが404になることは、未検証版の導入を防ぐために想定した停止です。

## 構成

- ソース: `Yumiiiiiiiiii/endstone` と `Yumiiiiiiiiii/pterodactyl`
- Egg: `egg-endstone-fork.json`。元の `egg-endstone.json` は参照用として保存。
- Docker: Python 3.13 / Linux amd64、既定UID 1000、`/home/container`。
  Wingsが指定する実行UID（検証ノードでは985）で動作する。install用targetだけはroot。
- 配布予定: `ghcr.io/yumiiiiiiiiii/endstone-wings:stable`。現時点で未公開。
- `ENDSTONE_RELEASE`: Fork Releaseのタグを直接指定。`stable`は承認済み更新のエイリアス。
- Eggのインストール時は`/mnt/server`へFork Releaseのmanifest・Wheel・BDSを取得し、SHA256を検証。
  起動時に`/home/container`内で専用venvを作る。MojangのBDSは再配布しない。
- 公式のEndstoneソース・PyPI Wheelへのフォールバックはない。外部Python依存のみPyPIから取得。

## パネルへの導入（公開・実機検証後に実施）

1. Forkの同じコミットをcheckoutし、runtimeとinstallerを別々にビルド・試験する。
2. 承認後に検証済みイメージをGHCRへ配布し、Eggの`docker_images`とインストーラーのイメージを
   それぞれ試験した不変の`@sha256:...`へ固定する。runtimeとinstallerは異なるdigestを持つ。
   任意のイメージ名をEgg変数でevalしない。
3. パネルの管理画面 → Nests → Import Eggから`egg-endstone-fork.json`を読み込む。
4. Docker Imageは検証済みForkランタイムを選び、通常のWingsの割当ポートを設定する。
5. `ENDSTONE_FORK`、Releaseタグ、チャンネル、BDS版をmanifestと一致させる。
6. まず開発用サーバーでインストールし、表示されたmanifest SHA256を確認する。
   `UPDATE_APPROVED_MANIFEST`にその値を指定する。初回起動にも承認が必要。
7. 通常のStart/Stopで操作する。コンソールの`stop`とSIGTERMはBDSへ正常停止を要求する。
8. 別プレイヤーの接続、コマンド、イベント、保存後の再起動を確認してから本番導入を判断する。

パネル本体のテーマと入力コントロールを利用する。独自の画面・CSSは追加していない。
Better Designのフォーム・文言ガイドを適用したが、実パネルのDOMと表示間隔は未検証。

## 更新と復旧

通常の起動時だけ更新を確認する。実行中に自動再起動はしない。
`AUTO_UPDATE_CHECK=true`でも、既定ではmanifest SHA256の承認が必要。
`ENDSTONE_RELEASE`を特定タグへ固定すると別タグへ追従しない。

更新順序は取得・整合性確認 → 新venvへのFork Wheel導入 → 停止確認 → 全データのバックアップ →
BDS・プラグイン配置 → 一度起動して10秒間の確認 → 正常停止 → 更新確定 → 通常起動。
起動試験時のログは`.fork/logs`へ保存する。試験だけでプレイヤー機能の互換性を判断しない。

バックアップには`.fork`以外のすべてのサーバーファイルを含める。
worlds、プレイヤーデータ、pluginsとデータ、server.properties、endstone.toml、旧BDSを保存する。
その時点のmanifest・版・旧venv参照とアーカイブSHA256を`backup.json`へ記録する。
旧venvは`.fork/envs`に保持し、ワールドの形式が変わった後も一式のスナップショットへ戻す。
空き容量不足・読み込み失敗・SHA256不一致では更新しない。自動削除ポリシーはないので容量を管理する。

```sh
# 必ずWingsで停止してから、同じサーバーのコンテナ・ボリューム内で実行
python /opt/endstone-fork/manager.py check
python /opt/endstone-fork/manager.py backup
python /opt/endstone-fork/manager.py rollback --backup /home/container/.fork/backups/<backup-id>
```

失敗した更新後のファイルは`.fork/failed`へ退避する。rollback後も確認が終わるまで保管する。
強制終了中の更新は`transaction.json`を残し、復旧するまで次の起動を拒否する。
強制終了でPID/lockが残った場合は、対象PIDが存在せずBDSが停止していることを管理者が確認してから
`.fork/server.pid`と`.fork/manager.lock`を除去する。生存プロセスのマーカーを自動的に削除しない。

## プラグインとprivateソース

既存プラグインは公開Forkへコミットしない。承認済みの専用privateリポジトリを作成した。
private側の初回pushは別途確認してから行う。
`PLUGIN_REPOSITORIES`は許可する配布先の`owner/repo`をカンマで指定する。
manifestにはWheel名・配布先・SHA256を記録し、Endstoneと同時に更新・復旧する。
認証が必要なprivate Releaseの直接ダウンロードは現段階では未対応。秘密トークンをEgg変数へ入力しない。
private CIテンプレートはEndstone Forkの`fork/private-plugin-ci.yml.example`を参照する。

Friend JoinのGo helper、Xboxログイン、NetherNetの実接続はプラグインWheelのビルドとは別に必要。
Vanishのprotocol 2193制限は未検証の新protocolへ拡張しない。

## 試験

```sh
python build_egg.py
python validate.py egg-endstone-fork.json
python -m unittest discover -s tests -v
docker build -t endstone-wings:test .
docker build --target installer -t endstone-wings:installer-test .
bash tests/test_install_entrypoint.sh endstone-wings:installer-test
```

ローカルの9件はオフライン故障注入試験。実際の新BDSの起動やWingsの復旧に合格した証拠ではない。
GH ActionsのDockerジョブも、オフライン管理コードの試験とBDS/Wingsの実機試験を区別する。

Wingsのインストール用コンテナはイメージのENTRYPOINTを残してCMDに`bash /mnt/install/install.sh`を渡す。
ENTRYPOINTではこの固定スクリプトを実行し、通常起動と分ける。`tests/test_install_entrypoint.sh`が
実際のDocker引数とネットワークなしのコンテナでこの経路を検証する。
WingsはinstallコンテナのUSERを上書きしないため、通常imageのUSER=containerを使うとroot所有の
install.shを読めず、UID985のボリュームにも書けない。install用は`:stable-installer`、通常は`:stable`を使う。
CIは`--user 0`の指定でこの不具合を隠さず、実Wings同様の所有者・権限で検証する。
