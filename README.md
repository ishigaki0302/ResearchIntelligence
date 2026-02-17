# Research Intelligence (ri) — v0.6

ローカル論文管理 + 検索 + 可視化システム。LaTeX/BibTeX ワークフロー向け。

## 主な機能

- **統合インポート**: ACL Anthology（一括）、BibTeX、PDF、URL
- **冪等な取り込み**: 同じ論文を2回インポートしても重複なし
- **ハイブリッド検索**: BM25（SQLite FTS5）+ ベクトル類似度（FAISS + sentence-transformers）
- **チャンク検索**: テキストをチャンク分割 → チャンク単位のベクトル検索で精密ヒット
- **検索フィルタ**: 年、venue、タグ、種別で絞り込み
- **BibTeX エクスポート**: venue/year/tag/collection でフィルタ → `.bib` 出力
- **ノート**: 論文ごとに Markdown ノートを自動生成、Web UI で編集可能
- **引用グラフ**: Semantic Scholar API 経由のメタデータベース引用構築 + PDF テキストからの参照抽出
- **Inbox レコメンド**: スコアリングによる推薦 + 自動タグ付与 + 自動承認
- **重複検出**: DOI/arXiv/タイトル類似度による重複検出 + マージ
- **定期同期 (sync)**: watch + recommend をまとめて実行（ローカル cron / GitHub Actions 対応）
- **週次ダイジェスト**: 新着・推薦・キーフレーズの Markdown レポート自動生成（GitHub Issue 公開対応）
- **高度な分析**: トピッククラスタリング（TF-IDF + KMeans）+ 引用ネットワーク分析（PageRank、コミュニティ検出）
- **バックアップ/マイグレーション**: DB + データの zip バックアップ、スキーマバージョン管理
- **Web UI**: FastAPI + Jinja2 + HTMX
- **CLI**: `ri` コマンド（Typer）

### v0.6 の新機能

- **メタデータベース引用構築**: PDF なしで Semantic Scholar API 経由の引用関係を構築（`ri build-citations`）
- **PDF ダウンロード + 引用抽出の一括実行**: `ri download-pdf --id <ID> --extract` でダウンロード → テキスト抽出 → 参照抽出 → 引用解決を一括実行
- **Web UI からの PDF 取得 & 引用抽出**: アイテム詳細ページにワンクリックボタン追加
- **検索タグ絞り込み**: 検索ページにタグドロップダウンフィルタ追加
- **Inbox 自動承認**: 品質スコアベースの自動承認パイプライン（`ri inbox auto-accept`）
- **重複検出 & マージ**: `ri dedup detect` / `ri dedup merge`
- **ノートテンプレート**: 論文ノートの自動生成テンプレート
- **ジョブ履歴ページ**: Web UI `/jobs` でジョブ実行履歴を確認
- **GitHub Actions ダイジェスト公開**: sync ワークフローから GitHub Issue にダイジェストを公開

### v0.5 の新機能

- **定期同期 (P12)**: `ri sync run` で watch + recommend をパイプライン実行。GitHub Actions ワークフロー付き
- **週次ダイジェスト (P13)**: `ri digest weekly` で Markdown + JSON レポート生成
- **高度な分析 (P14)**: トピッククラスタリング + 引用ネットワーク分析（PageRank、コミュニティ検出）
- **運用品質 (P15)**: `ri backup create` / `ri migrate` / ジョブサマリ

### v0.4 の新機能

- **チャンク埋め込み (P8)**: テキストをチャンク分割 → チャンク FAISS インデックスでピンポイント検索
- **引用品質向上 (P9)**: 多パターン参照抽出、タイトルフォールバック解決、References/Cited-by テーブル
- **Inbox 自動化 (P10)**: スコアリングによる推薦、自動タグ提案
- **DevOps 強化 (P11)**: CODEOWNERS、Dependabot、ベンチマークテスト

### v0.3 の新機能

- **ウォッチリスト**: arXiv / OpenAlex からの継続的な論文発見
- **Inbox**: 発見した論文の承認/却下ワークフロー
- **トレンド分析**: 年×venue、年×tag、キーフレーズ抽出（TF-IDF）
- **GitHub Actions CI**: pytest + ruff + black の自動チェック

## セットアップ

```bash
cd repo
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## クイックスタート

### 1. ACL 2024 論文をインポート

```bash
ri import "acl:2024{main,findings}"
ri stats
```

### 2. 検索インデックス構築

```bash
ri index           # 基本インデックス（FTS5 + FAISS）
ri index --chunks  # チャンクインデックスも構築
```

### 3. 検索

```bash
ri search "instruction tuning in long context"
ri search "attention mechanism" --scope both
ri search "retrieval augmented generation" --year 2024 --venue ACL -k 10
```

### 4. ウォッチリスト + Inbox

```bash
ri watch add --name rag-papers --source arxiv --query "retrieval augmented generation" --category cs.CL
ri watch run --name rag-papers --since 7d
ri inbox recommend --threshold 0.6
ri inbox list
ri inbox accept 1
```

### 5. 定期同期（v0.5 新機能）

```bash
# watch run + inbox recommend を一括実行
ri sync run --since 7d --recommend --out digest.md

# 直近のジョブ確認
ri sync status

# 最新ダイジェスト表示
ri sync digest
```

cron で定期実行する場合:
```bash
# crontab -e
0 6 * * 1 cd /path/to/repo && .venv/bin/ri sync run --since 7d --out data/cache/sync/digest.md
```

GitHub Actions では `.github/workflows/sync.yml` が schedule + workflow_dispatch で動作し、
ダイジェストを Artifacts としてアップロードします（DB をコミットしない方式）。

### 6. 週次ダイジェスト（v0.5 新機能）

```bash
# 直近7日のダイジェスト生成
ri digest weekly --since 7d --out digest.md

# 特定ウォッチのダイジェスト
ri digest watch --name rag-papers --since 14d
```

出力例:
```markdown
# Research Intelligence Digest — Last 7 days
## Summary
| Metric | Count |
|--------|-------|
| Discovered | 42 |
| Recommended | 8 |
| Accepted | 3 |
## By Watch
### rag-papers
Discovered: 25 | Recommended: 5 | Accepted: 2
**Top recommended:**
1. Paper Title (ACL) [2024] score=0.85
...
## Top Keywords
- **retrieval augmented** (3.24)
- **language model** (2.87)
```

### 7. 高度な分析（v0.5 新機能）

```bash
# トピッククラスタリング
ri analytics cluster --clusters 5

# 引用ネットワーク分析
ri analytics graph-stats

# 分析結果を JSON でエクスポート
ri analytics export --out trends.json
```

Web UI の `/analytics` ページでも確認できます。クラスタ概要、影響力の高い論文（PageRank）、
コミュニティ検出結果が表示されます。

### 8. 引用構築（v0.6 新機能）

```bash
# Semantic Scholar API 経由でメタデータベースの引用関係を構築（PDF不要）
ri build-citations
ri build-citations --id 42        # 個別アイテム指定
ri build-citations --limit 100    # 処理件数制限

# PDF ダウンロード + テキスト抽出 + 参照抽出を一括実行
ri download-pdf --id 42 --extract
```

Web UI のアイテム詳細ページでも「PDF取得＆引用抽出」ボタンから実行可能です。

### 9. 重複検出 & マージ（v0.6 新機能）

```bash
ri dedup detect                    # 重複候補を表示
ri dedup merge 123 456 --dry-run   # マージのプレビュー
ri dedup merge 123 456 --apply     # 実際にマージ実行
```

### 10. バックアップとマイグレーション（v0.5 新機能）

```bash
# バックアップ作成
ri backup create --out backup.zip
ri backup create --out backup.zip --no-pdf --no-cache  # 軽量版

# バックアップ復元手順を確認
ri backup restore backup.zip

# DBマイグレーション（スキーマ更新後に実行）
ri migrate
```

### 11. BibTeX エクスポート

```bash
ri export-bib -o references.bib
ri export-bib --venue ACL --year 2024 -o acl2024.bib
```

### 12. Web UI

```bash
ri serve
# http://127.0.0.1:8000 を開く
```

ページ一覧:
- **ホーム**: 統計、最近のアイテム、コレクション
- **検索**: ハイブリッド検索（年、venue、タグ、タイプでフィルタ）
- **アイテム詳細**: メタデータ、要旨、BibTeX、タグ、ノートエディタ、PDF取得＆引用抽出
- **グラフ**: 引用サブグラフ可視化（D3.js）
- **Inbox**: 発見した論文の承認/却下/自動承認
- **Watches**: ウォッチの管理・実行
- **ジョブ履歴**: パイプライン実行履歴
- **分析**: トレンド + クラスタ + 引用ネットワーク

### エンドツーエンド ワークフロー

```bash
ri import "acl:2024{main,findings}"                  # インポート
ri enrich --limit 100                                 # 外部 ID 付与
ri build-citations --limit 100                        # メタデータベース引用構築
ri download-pdf --collection "ACL 2024 (main,findings)" --max 100
ri index --chunks                                     # 検索インデックス構築
ri extract-references --limit 100                     # PDF からの参照抽出
ri dedup detect                                       # 重複チェック
ri watch add --name rag --source arxiv --query "RAG" --category cs.CL
ri sync run --since 7d --out digest.md                # 同期 + ダイジェスト
ri analytics cluster                                  # トピック分析
ri analytics graph-stats                              # 引用ネットワーク
ri backup create --out backup.zip                     # バックアップ
ri serve                                              # Web UI 起動
```

## プロジェクト構成

```
repo/
├── app/
│   ├── cli/main.py            # CLI コマンド（Typer）
│   ├── web/
│   │   ├── server.py           # FastAPI アプリ
│   │   └── templates/          # Jinja2 テンプレート（HTMX）
│   ├── core/
│   │   ├── config.py           # 設定ローダー
│   │   ├── models.py           # SQLAlchemy ORM モデル
│   │   ├── db.py               # DB セッション管理 + マイグレーション
│   │   ├── bibtex.py           # BibTeX パース/生成
│   │   └── service.py          # CRUD + upsert ロジック
│   ├── connectors/             # 外部 API コネクタ
│   ├── pipelines/
│   │   ├── sync.py             # 同期パイプライン（v0.5）
│   │   ├── backup.py           # バックアップ（v0.5）
│   │   ├── auto_accept.py      # Inbox 自動承認（v0.6）
│   │   ├── dedup.py            # 重複検出 & マージ（v0.6）
│   │   ├── watch.py            # ウォッチパイプライン
│   │   ├── inbox_recommend.py  # Inbox レコメンド
│   │   └── ...
│   ├── analytics/
│   │   ├── trends.py           # トレンド分析
│   │   ├── digest.py           # ダイジェスト生成（v0.5）
│   │   ├── clustering.py       # トピッククラスタリング（v0.5）
│   │   └── network.py          # 引用ネットワーク分析（v0.5）
│   ├── indexing/               # FTS5 + FAISS インデックス
│   └── graph/                  # 引用グラフクエリ
├── .github/workflows/
│   ├── ci.yml                  # CI（pytest, ruff, black）
│   ├── release.yml             # タグ付きリリース
│   └── sync.yml                # 定期同期（v0.5）
├── configs/config.yaml         # アプリ設定
├── data/                       # ライブラリ + キャッシュ
├── db/app.sqlite               # SQLite データベース
├── tests/                      # pytest テスト（118件）
├── CHANGELOG.md
├── pyproject.toml
└── README.md
```

## 設定

`configs/config.yaml` を編集:

```yaml
storage:
  library_dir: "data/library/papers"
  db_path: "db/app.sqlite"

embedding:
  model: "all-MiniLM-L6-v2"

search:
  default_top_k: 20
  bm25_weight: 0.5
  vector_weight: 0.5

sync:
  enable: true
  default_since_days: 7
  run_recommend: true
  output_dir: "data/cache/sync"
  actions:
    mode: "digest-only"  # digest-only or apply

analytics:
  default_clusters: 5
```

## テスト

```bash
pytest tests/ -v          # 全テスト実行（118件）
ruff check app/ tests/    # Lint
black --check app/ tests/ # フォーマットチェック
```

## ライセンス

[LICENSE](LICENSE) を参照。
