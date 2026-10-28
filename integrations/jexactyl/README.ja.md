# JexactylでEgg変更後も古いStartupが残る場合

実パネルで、Egg選択時にUIがStartupを空にし、Laravelがnullへ変換した後、
`StartupModificationService` が古いコマンドを維持する挙動を確認した。
この状態ではEggだけを変えても新しい起動処理は使われない。

暫定操作は、Startupの「Default Startup Command」を「Startup Command」へそのまま適用して保存すること。
再インストールやサーバーファイルの編集は不要。

根本修正は `startup-default.patch`。管理者が別のEggを選んでStartupを空にした時だけ、
選んだEggの既定Startupを保存する。明示した独自コマンドと、同じEggの既存コマンドは保持する。
PHPサービスだけの修正で、フロントの再ビルドは不要。

```sh
python integrations/jexactyl/prepare_patch.py --panel /path/to/panel-copy --output /tmp/panel-startup
php -l /tmp/panel-startup/StartupModificationService.fixed.php
php integrations/jexactyl/test_startup.php /tmp/panel-startup/StartupModificationService.fixed.php egg-endstone-migrate.json
```

生成器は想定したソースの一意な一致を必須とする。ソースが異なる場合は適用しない。
テストは実サービスを使い、DB境界をメモリ内へ置き換えた7ケース。
本番パネルのファイル・DBは変更しない。適用前に現行ソース照合、バックアップ、明示許可が必要。
適用後は開発用サーバーでEgg選択→保存→Wings起動を再検証する。

今回の対象パネルでは、現行ソースのSHA256一致と7ケース合格を確認し、ユーザーの明示承認後に
PHPサービスだけをバックアップして適用した。実Laravelサービスと実Wings経路も検証した。
ブラウザーでの最終操作はツール側のURL安全判定で停止したため未検証として残す。
