# 公式EndstoneからEgg変更だけで移行する

既存サーバー向けは `egg-endstone-migrate.json`。既存の公式Python Dockerを使い、
起動処理とFork用Python環境をサーバー内へ自動配置します。専用GHCRの導入や再インストールは不要です。

**現在、BDS 1.26.60用の検証済みFork Releaseは未公開です。
移行機構の実装があっても、1.26.60でForkを動かせる状態にはまだなっていません。**
未対応版を26.52へ下げて起動しません。

## パネルからの操作

1. 管理画面 → Nests → Minecraft → Importで移行用JSONを登録する。
2. 対象サーバーを正常に停止する。
3. Startup設定で移行用Eggを選ぶ。Docker Imageは現在の公式
   `ghcr.io/parkervcp/yolks:python_3.xx` を維持し、Startup Commandは新しいEggの既定値を使う。
   パネルが古いコマンドを保持する場合は、既定Startupを適用する。**Reinstallは押さない。**
4. Fork、stableチャンネル、BDS版を確認して起動する。
   privateプラグインを更新する場合は許可リポジトリと認証ファイルも事前に設定する。

既定値は `ENDSTONE_FORK=Yumiiiiiiiiii/endstone`、`ENDSTONE_RELEASE=stable`、
`UPDATE_CHANNEL=stable`、`BDS_VERSION=1.26.60`。
初回移行とBDSの更新は一度に行いません。既存BDSと同じ実ビルドの検証済みReleaseが必要です。
ポート、`level-name`、packsや設定は変更しません。

## 自動処理

1. 公式Python3.14/3.12/3.11環境では、SHA256固定の独立Python3.13を
   `.fork/runtimes`へ導入する。既存Pythonと公式Endstoneを保持する。
2. 本人Forkのmanifest、全成果物のSHA256、stableの必須検証結果を照合する。
3. 停止状態、元の公式runtime、設定されたworldの存在、全worldの保存版を検査する。
   既存globalプラグインに移行可能なWheelがなければ移行しない。
4. `.fork`以外の全ファイルを保存し、アーカイブを再読込してSHA256を記録する。
   BDS、world/player data、plugins/data、packs、設定、旧Pythonパッケージが対象。
5. 候補BDSと既存 `bedrock_server` の実SHA256を比較する。
   初回は同じ実バイナリだけ許可し、BDSアーカイブのファイルは配置しない。
   同じ26.60系でも、保存buildが候補より新しいworldは拒否する。
6. Forkを独立venvへ導入し、プラグインとrelayを配置する。
   二重登録を防ぎ、プラグイン本体はEndstoneのローダーから読み込む。
7. 空のallowlistとonline-modeで接続を制限して試験起動・正常停止し、元の設定バイト列を戻す。
   合格後に移行を確定し通常起動する。試験中の起動完了ログはWingsへ表示しない。

初回は移行用Eggの選択を承認として扱い、検証済みstableだけを自動適用します。
以後は `UPDATE_REQUIRE_APPROVAL=true` に従いmanifestのSHA256承認が必要です。
実行中のサーバーを自動再起動する機能はありません。

## 移行できない場合と復旧

- 互換stableが未配布なら、整合性を確認した既存公式版を起動する。
  コンソールに `Fork migration not applied` と表示され、Forkへ移れたことは意味しない。
- world欠落、破損level.dat、公式runtimeとBDSの版不一致、新しい版で保存されたworldでは停止する。
  代わりの新worldは作らない。
- 試験失敗時は、初回に限り `ROLLBACK_ON_FAILURE=false` でも全snapshotを復元し、公式版を起動する。
  復元自体に失敗した場合はどちらも起動しない。
- 強制終了した初回移行は次の起動で先に復元する。復元済みの移行は自動再試行しない。
  内容を確認した `migration.json` を管理者が保管してから再試行する。
- 成功後に公式版へ戻す場合は、Wingsで停止して対応するsnapshot一式へ戻す。
  Eggだけ戻す操作では、新しいプラグインデータの形式まで戻せない。
  保持済みPython3.13でbootstrap内の `manager.py rollback --root /home/container --backup <backup>` を実行する。
  snapshotのパスは `.fork/migration.json` の `backup` に記録する。

backupは `.fork/backups`、失敗側ファイルは `.fork/failed`、ログは `.fork/logs`。
管理コード・venv・認証ファイルは `.fork` に保持し、全体backupとは別に管理します。
自動削除はしません。容量不足でbackupに失敗した場合は移行を中止します。

## 実測と適用範囲

Linuxで管理コード38試験を実施。公式Python3.14 Docker/UID1000上で、公式Endstone0.11.14から
本人Fork70b5640bへの実移行、3プラグイン有効化、失敗注入後の3,058ファイル完全復元、
公式版再起動を確認しました。これは26.52.3での比較試験です。
ローカル成果物を使い、stable検証結果の偽装はしていません。
`tests/runtime_official_migration.py`で再現でき、privateソースは必要ありません。

外部DBはファイルsnapshotだけでは一貫して戻せません。既知のDiscord SQL/JDBC設定は拒否しますが、
任意の第三者プラグインの外部保存先を一般に検出できるわけではありません。
外部DB利用時は管理者がDB snapshotを合わせて行う必要があります。
全プラグインのDiscord/Xbox通信を停止する処理ではありません。
symlinkのあるサーバーは自動移行を拒否します。
実プレイヤー操作、BDS1.26.60の全native ABI、実Release/private配布は別途検証が必要です。
