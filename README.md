# Research Index (ri) — v0.3

ローカル論文管理 + 検索 + 可視化システム。LaTeX/BibTeX ワークフロー向け。

## 主な機能

- **統合インポート**: ACL Anthology（一括）、BibTeX、PDF、URL
- **冪等な取り込み**: 同じ論文を2回インポートしても重複なし
- **ハイブリッド検索**: BM25（SQLite FTS5）+ ベクトル類似度（FAISS + sentence-transformers）
- **BibTeX エクスポート**: venue/year/tag/collection でフィルタ → `.bib` 出力
- **ノート**: 論文ごとに Markdown ノートを自動生成、Web UI で編集可能
- **引用グラフ**: ローカルサブグラフの可視化（D3.js）
- **Web UI**: FastAPI + Jinja2 + HTMX
- **CLI**: `ri` コマンド（Typer）

### v0.3 の新機能

- **ウォッチリスト**: arXiv / OpenAlex からの継続的な論文発見
- **Inbox**: 発見した論文の承認/却下ワークフロー
- **トレンド分析**: 年×venue、年×tag、キーフレーズ抽出（TF-IDF）
- **GitHub Actions CI**: pytest + ruff + black の自動チェック
- **タグ付きリリース**: タグ push で GitHub Release を自動作成

### v0.2 の機能

- **PDF ダウンロード**: ACL Anthology 等からの一括ダウンロード
- **参照抽出**: 論文テキストから引用関係を抽出 → 引用グラフ構築
- **タグ管理**: CLI / Web UI でのタグ追加・削除
- **外部 API 連携**: OpenAlex / Semantic Scholar からの ID 付与

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
# main + findings トラックをインポート
ri import "acl:2024{main,findings}"

# 統計確認
ri stats
```

### 2. その他のインポート

```bash
# BibTeX ファイル
ri import bib:/path/to/references.bib

# 単体 PDF
ri import pdf:/path/to/paper.pdf --title "My Paper" --year 2024

# URL（ブログ等）
ri import url:https://example.com/blog-post --type blog
```

### 3. 検索インデックス構築

```bash
ri index
```

テキスト抽出（PDF/URL）→ FTS5 全文索引 → FAISS ベクトル埋め込みを構築します。

### 4. 検索

```bash
# 基本検索
ri search "instruction tuning in long context"

# フィルタ付き
ri search "retrieval augmented generation" --year 2024 --venue ACL -k 10
```

### 5. BibTeX エクスポート

```bash
# 全件エクスポート
ri export-bib -o references.bib

# venue + year でフィルタ
ri export-bib --venue ACL --year 2024 -o acl2024.bib

# コレクション指定
ri export-bib --collection "ACL 2024" -o acl2024.bib
```

### 6. PDF ダウンロード

```bash
# コレクション単位でダウンロード
ri download-pdf --collection "ACL 2024 (main,findings)" --max 10

# 単体ダウンロード
ri download-pdf --id 42

# 失敗分のリトライ
ri download-pdf --failed-only
```

### 7. 参照抽出

```bash
# テキスト/PDF のある全アイテムから参照を抽出
ri extract-references --limit 50

# 単体
ri extract-references --id 42
```

References セクションを解析し、DOI/arXiv ID を抽出して引用リンクを作成します。

### 8. タグ管理

```bash
ri tag add 1 method/RAG    # タグ追加
ri tag ls 1                 # タグ一覧
ri tag rm 1 method/RAG     # タグ削除
```

Web UI のアイテム詳細ページからも操作できます。

### 9. 外部 API エンリッチ

```bash
# OpenAlex / Semantic Scholar の ID を付与
ri enrich --limit 10

# 単体
ri enrich --id 42

# メタデータも更新
ri enrich --limit 10 --update-metadata
```

### 10. ウォッチリスト（v0.3 新機能）

```bash
# ウォッチを作成
ri watch add --name rag-papers --source arxiv --query "retrieval augmented generation" --category cs.CL

# ウォッチ一覧
ri watch list

# ウォッチを実行（直近7日の論文を取得）
ri watch run --name rag-papers --since 7d

# OpenAlex ソースのウォッチ
ri watch add --name transformers-oa --source openalex --query "transformer language model"
ri watch run
```

### 11. Inbox で論文をレビュー（v0.3 新機能）

```bash
# 新着 inbox を確認
ri inbox list

# 承認 → メインライブラリに追加
ri inbox accept 1

# 却下
ri inbox reject 2

# 全ステータス表示
ri inbox list --status all
```

### 12. トレンド分析（v0.3 新機能）

```bash
# 分析結果を JSON でエクスポート
ri analytics export --out trends.json
```

Web UI の `/analytics` ページでも確認できます（年×venue、年×tag、キーフレーズ等）。

### 13. Web UI

```bash
ri serve
# http://127.0.0.1:8000 を開く
```

ページ一覧:
- **ホーム**: 統計、最近のアイテム、コレクション
- **検索**: ハイブリッド検索（年、venue、タイプでフィルタ）
- **アイテム詳細**: メタデータ、要旨、BibTeX、タグ、ノートエディタ
- **グラフ**: 引用サブグラフ可視化（D3.js）
- **Inbox**: 発見した論文の承認/却下
- **Watches**: ウォッチの管理・実行
- **分析**: トレンドダッシュボード

### エンドツーエンド ワークフロー

```bash
ri import "acl:2024{main,findings}"                              # インポート
ri download-pdf --collection "ACL 2024 (main,findings)" --max 100 # PDF ダウンロード
ri index                                                          # 検索インデックス構築
ri extract-references --limit 100                                  # 参照抽出 → 引用グラフ
ri enrich --limit 100                                              # 外部 ID 付与
ri watch add --name rag --source arxiv --query "RAG" --category cs.CL  # ウォッチ設定
ri watch run                                                       # 新着論文取得
ri inbox list                                                      # Inbox 確認
ri analytics export --out trends.json                              # トレンド分析
ri serve                                                           # Web UI 起動
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
│   │   ├── db.py               # DB セッション管理
│   │   ├── bibtex.py           # BibTeX パース/生成
│   │   └── service.py          # CRUD + upsert ロジック
│   ├── connectors/
│   │   ├── acl.py              # ACL Anthology コネクタ
│   │   ├── arxiv.py            # arXiv API コネクタ（v0.3）
│   │   ├── openalex.py         # OpenAlex API コネクタ
│   │   └── semantic_scholar.py # Semantic Scholar API コネクタ
│   ├── pipelines/
│   │   ├── importer.py         # インポート処理
│   │   ├── exporter.py         # BibTeX エクスポート
│   │   ├── extract.py          # テキスト抽出（PDF/URL）
│   │   ├── downloader.py       # PDF ダウンロード
│   │   ├── references.py       # 参照抽出
│   │   ├── enricher.py         # 外部 API エンリッチ
│   │   └── watch.py            # ウォッチパイプライン（v0.3）
│   ├── analytics/
│   │   └── trends.py           # トレンド分析（v0.3）
│   ├── indexing/
│   │   └── engine.py           # FTS5 + FAISS インデックス
│   └── graph/
│       └── citations.py        # 引用グラフクエリ
├── .github/
│   ├── workflows/ci.yml        # CI（pytest, ruff, black）
│   └── workflows/release.yml   # タグ付きリリース
├── configs/config.yaml         # アプリ設定
├── data/
│   ├── library/papers/{id}/    # 論文ごとのファイル（PDF, テキスト, ノート）
│   └── cache/                  # ダウンロードキャッシュ、埋め込み
├── db/app.sqlite               # SQLite データベース
├── tests/                      # pytest テスト（51件）
├── CHANGELOG.md                # 変更履歴
├── pyproject.toml              # Python パッケージ設定
└── README.md
```

## 設定

`configs/config.yaml` を編集:

```yaml
storage:
  library_dir: "data/library/papers"
  db_path: "db/app.sqlite"

embedding:
  backend: "sentence-transformers"
  model: "all-MiniLM-L6-v2"
  dimension: 384

download:
  max_workers: 4
  sleep_sec: 1.0

external:
  openalex:
    enabled: true
    email: ""              # polite pool
  semantic_scholar:
    enabled: true
    api_key: ""            # 任意（レート制限緩和用）

search:
  default_top_k: 20
  bm25_weight: 0.5
  vector_weight: 0.5

watch:
  arxiv:
    sleep_sec: 3.0
    max_results: 100
  openalex:
    sleep_sec: 1.0
    max_results: 100
```

## テスト

```bash
pytest tests/ -v          # 全テスト実行（51件）
ruff check app/ tests/    # Lint
black --check app/ tests/ # フォーマットチェック
```

## ライセンス

[LICENSE](LICENSE) を参照。
