# お弁当 Instagram 自動投稿

Google Drive に写真を入れるだけで、GitHub Actions が見つけて加工し、Instagram に投稿します。
投稿後の反応（いいね・保存・リーチなど）を毎日集めて分析し、**次の投稿の作り方に自動で反映**します。

```
Google Drive（写真を入れる）
   │  15分ごとにチェック
   ▼
GitHub Actions ── Claude で解析 → 1枚目（4:5に整える）／2枚目（手書き風おかずメモ）／キャプション
   ▼
GitHub Issue に「下書き」が届く（スマホの GitHub アプリに通知）
   │  キャプションは Issue を編集して直せる
   │  コメントで指示： /post  /post 19:30  /redo メモ  /skip
   ▼
Instagram にカルーセル投稿（2枚）
   │  毎日 4時に集計
   ▼
分析 → docs/report.html（レポート）＋ data/strategy.json（次回の方針）
```

## 使い方（セットアップ後）

1. Google Drive の「お弁当」フォルダに写真をアップロード（HEIC もOK）
   - エピソードを入れたいときは、写真の「説明」欄か同名の `.txt` に書く
2. 15分以内に Issue「🍱 下書き：〇〇弁当」が届く。**ここでは投稿されません**
3. 画像とキャプションを確認して、Issue にコメント

| コメント | 動き |
|---|---|
| `/post` | 今すぐ投稿 |
| `/post 19:30` | 時刻を指定して予約（`/post 明日 8:00` も可。15分おきのチェックで投稿） |
| `/redo` | 作り直し。`/redo 肉巻きは豚バラ、お隣さんからの差し入れ` のようにメモも足せる |
| `/skip` | 投稿しない |

- キャプションを直したいときは、Issue 本文の枠の中を編集（右上「…」→ Edit）。投稿時に編集後の内容が使われます
- コマンドはリポジトリの持ち主と、Settings → Collaborators で招待した人のコメントだけ受け付けます（👀 が付いたら受付済み）
- 分析で「伸びる時間帯」が分かってくると、下書きにおすすめ時刻が表示されます

## 学習のしくみ

毎回の投稿で、次の「上げ方」をいくつか変えて試します（`config.yaml` の `experiment`）。

| 試すこと | 選択肢 |
|---|---|
| タイトルの書き方 | `＼ 〇〇弁当 ／` / `〇〇弁当` / `11.21 〇〇弁当` |
| 手書き文字のペン | マーカー / 細ペン |
| コメントの長さ | 短め / 長め |
| ハッシュタグ数 | 8 / 15 / 25 |

- 評価は投稿から72時間後の **スコア =（いいね＋保存×2＋シェア×2＋コメント×3）÷ リーチ × 100**。フォロワーが増えても公平に比べられます。
- 各パターンを最低3回試したら、70% は一番良いパターン、30% は他を試し続けます（傾向が変わっても追従できるように）。
- 毎週月曜に Claude がデータを読んで考察し、「キャプションのコツ」「伸びるタグ・使わないタグ」「伸びる時間帯」「次に作ると良さそうなお弁当」をまとめます。8件たまると、これらがキャプション生成と投稿時間帯に反映されます。
- 主菜（肉・魚…）・色味（茶色・カラフル）・品数・曜日・時間帯ごとの比較もレポートに出ます。

運用状況と分析は claude.ai のダッシュボード（Artifact）にまとまっています。Claude の定期実行が `src/export_artifact.py` で書き出したデータを1日4回取り込みます。

---

## セットアップ

### 1. Instagram 側

1. Instagram アカウントを **プロアカウント（クリエイター or ビジネス）** に切り替える
2. [Meta for Developers](https://developers.facebook.com/) でアプリを作成 → ユースケースで「Instagram の API」を追加
3. 「Instagram ログインによる API 設定」で自分のアカウントを追加し、**アクセストークンを生成**
   - 権限：`instagram_business_basic` / `instagram_business_content_publish` / `instagram_business_manage_insights`
   - 自分のアカウントにだけ投稿する用途なので、アプリは開発モードのままで使えるはずです（アカウントにアプリのテスター等の役割が付いていること）
4. 統合ログイン（Unified login）の場合はビジネスポートフォリオを紐づけ、ステップ1でテンプレートから設定を作成 → ステップ2でトークン生成。システムユーザートークン（`EAA...`）は期限なし
   - Instagram ログインのトークン（`IGAA...`、60日）を使う場合は `config.yaml` の `instagram.host` を `graph.instagram.com` に

### 2. Google Drive 側

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成し、**Google Drive API を有効化**
2. 「IAM と管理 → サービスアカウント」で作成 → 「鍵 → JSON」でダウンロード
3. Drive に「お弁当」フォルダを作り、サービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）に **閲覧者** で共有
4. フォルダ URL の `folders/` の後ろが **フォルダ ID**

### 3. Claude API

[console.anthropic.com](https://console.anthropic.com/) で API キーを発行（1投稿あたり Claude 呼び出し2回＋週1回の分析）

### 4. GitHub 側

1. このフォルダを **公開リポジトリ** として push
   - Instagram API は「誰でも見られる URL の画像」しか受け付けないため、加工後の画像を `media` ブランチに置いてその URL を渡しています
   - 非公開にしたい場合：画像置き場用の公開リポジトリを別に作り、Secrets に `MEDIA_REPO`（`owner/name`）と `MEDIA_REPO_TOKEN` を追加
   - 公開リポジトリだと `data/posts.json`（キャプション・メモ・反応の数字）も見える点に注意。気になる場合は上の分離構成に
2. Settings → Actions → General → Workflow permissions を **Read and write** に
3. Settings → Secrets and variables → Actions に登録：

| Secret | 内容 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API キー |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウントの JSON（ファイルの中身をそのまま） |
| `DRIVE_FOLDER_ID` | お弁当フォルダの ID |
| `IG_ACCESS_TOKEN` | Instagram のアクセストークン |
| `IG_USER_ID` | Instagram のユーザーID（トークン生成画面に出る `1784...` の数字） |

### 5. 動作確認

1. Drive に写真を1枚入れる
2. Actions → 「お弁当の下書きを作成・予約投稿」→ Run workflow（または15分待つ）
3. Issues に下書きが届くので、確認して `/post`

手元で試す場合：
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python src/post.py --local 写真.jpg --memo "お隣さんから手羽元をもらった"
# → out/ に画像2枚とキャプション
```

## Apps Script で起動を安定させる

GitHub の定期実行は遅れたり飛んだりするので、Google Apps Script のタイマーから起動します（`tools/apps-script/Code.gs`）。

1. GitHub で Fine-grained トークンを作る：Settings → Developer settings → Fine-grained tokens → Generate new token
   - Repository access: Only select repositories → `nkud/bento-insta`
   - Permissions: **Actions: Read and write**
2. https://script.google.com で新しいプロジェクトを作り、`Code.gs` の中身を貼り付けて保存
3. 歯車（プロジェクトの設定）→ スクリプト プロパティに追加
   - `GITHUB_TOKEN` … 1 のトークン
   - `FOLDER_ID` … お弁当フォルダの ID
4. 関数 `setup` を選んで実行 → 権限を許可（Drive の閲覧・外部への接続）
5. 以後、5分ごとに新しい写真と予約を確認し、毎日4時に集計を起動します。`runNow` で手動起動もできます

## カスタマイズ

- `config.yaml`
  - `caption.voice` / `signature` / `base_hashtags`：口調・毎回の署名・必ず付けるタグ
  - `experiment.dimensions`：試すパターンを増減
- 手書きの見た目：`src/annotate.py`（文字サイズ・線の太さ・影など）

## 構成

```
config.yaml               設定
src/post.py               Drive → 解析 → 加工 → 下書きIssue／予約投稿
src/approve.py            Issue コメント（/post /redo /skip）の処理
src/vision.py             Claude で写真解析
src/annotate.py           手書き風の2枚目を作成（レイアウト・引き出し線）
src/handwriting.py        書き順データで文字を手書きする
src/caption.py            キャプション生成（学習結果を反映）
src/strategy.py           どのパターンで投稿するか選ぶ
src/metrics.py            反応の集計
src/analyze.py            分析・レポート・方針の更新
src/export_artifact.py    claude.ai のダッシュボード用データの書き出し
data/                     投稿記録・方針（Actions が自動更新）
docs/report.html          レポート
fonts/                    手書き風フォント よもぎ（OFL）。書き順データ KanjiVG（CC BY-SA 3.0, © Ulrich Apel）と Yusei Magic（OFL）は src/annotate.py の LETTERING を "strokes" にしたとき用
.github/workflows/        定期実行
```

## 注意

- GitHub Actions の定期実行は混雑時に数分〜数十分遅れることがあります
- Instagram の投稿上限は24時間で50件（カルーセルは1件扱い）
- 下書き作成に失敗した写真は最大3回まで再挑戦し、それでもダメなら `data/posts.json` の `error` に理由が残ります
