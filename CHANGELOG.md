# 変更履歴

このプロジェクトの主な変更をこのファイルに記録します。

フォーマットは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に基づいています。

## [Unreleased]

## [0.5.0] - 2026-02-17

### 追加
- **自動同期パイプライン (P12)**: Watch の定期実行 + レコメンド
  - `ri sync run [--since 7d] [--watch <name>] [--recommend] [--out digest.md]`
  - `ri sync status` で直近の同期ジョブを表示
  - `ri sync digest` で最新ダイジェストを表示
  - ジョブ記録（件数・実行時間・失敗情報のサマリー付き）
  - `.github/workflows/sync.yml` による GitHub Actions 対応（スケジュール + 手動実行）
  - `config.yaml` に sync 設定を追加（enable, default_since_days, output_dir, actions.mode）
- **週次ダイジェスト (P13)**: 発見論文の Markdown レポート生成
  - `ri digest weekly --since 7d --out digest.md`
  - `ri digest watch --name <name> --since 14d`
  - サマリー: Watch 別の発見数・推薦数・承認数
  - Watch 別のスコア上位推薦論文
  - Inbox アイテムからの TF-IDF キーワード抽出
  - Markdown + JSON の両形式で出力
- **高度な分析 (P14)**: トピッククラスタリングと引用ネットワーク分析
  - `ri analytics cluster [--clusters N] [--out clusters.json]`
  - TF-IDF + KMeans によるクラスタリング（上位キーワード・代表論文付き）
  - `ri analytics graph-stats [--out graph.json]`
  - 引用ネットワーク: 被引用数、引用数、PageRank、コミュニティ検出
  - `/analytics` にクラスタ概要・影響力のある論文・コミュニティを表示
- **運用品質 (P15)**: バックアップ、マイグレーション、可観測性
  - `ri backup create --out backup.zip [--no-pdf] [--no-cache]`
  - `ri backup restore <backup.zip>`（復元手順を表示）
  - `ri migrate` で未適用の DB マイグレーションを実行
  - `schema_version` テーブルで適用済みマイグレーションを追跡
  - Job テーブルに `summary_json`, `started_at`, `finished_at` を追加
  - テスト 19 件追加（合計 88 件、全件パス）

### 変更
- マイグレーションフレームワークをアドホックな `_migrate_add_columns` からバージョン管理方式に刷新
- 引用ネットワーク分析のため `networkx>=3.1` を依存関係に追加
- バージョンを 0.5.0 に更新

## [0.4.0] - 2026-02-17

### 追加
- **チャンク埋め込み (P8)**: テキストチャンク単位の精密なベクトル検索
  - `Chunk` モデルによるアイテムテキストの分割・検索
  - 見出し対応のテキストチャンキング (`app/indexing/chunker.py`)
  - チャンク埋め込み用の独立した FAISS インデックス
  - `ri index --chunks` でチャンクインデックスを構築
  - `ri search --scope both` でアイテムとチャンクを横断検索
  - Web 検索結果とアイテム詳細でチャンクヒットをプレビュー
- **引用品質向上 (P9)**: 参考文献の抽出・解決の改善
  - 複数パターンの参考文献抽出（括弧、番号付きドット、段落）
  - 複数 ID 抽出: DOI, arXiv, ACL Anthology, OpenReview, URL, ISBN
  - ハッシュベースの引用重複排除（再実行可能、重複なし）
  - 解決強化: bibtex_key, DOI, arXiv, ACL, URL, タイトルフォールバック
  - 解決方法の内訳付き解決統計
  - 深さ 2 の引用サブグラフ対応
  - アイテム詳細で参照/被引用/グラフのタブ表示
  - アイテム詳細で未解決の参考文献を表示
- **Inbox 自動化 (P10)**: レコメンドスコアリングと自動タグ付け
  - `ri inbox recommend` で関連性・会場・著者重複・新しさに基づきスコアリング
  - Watch 名・会場・クエリキーワードからの自動タグ候補
  - Web Inbox に「推薦」フィルター
  - 承認時に自動タグを適用
- **DevOps 品質 (P11)**: CI/CD の強化
  - `CODEOWNERS` ファイル
  - pip と GitHub Actions 用の Dependabot
  - CI でのベンチマークテスト（警告のみ）

### 変更
- `hybrid_search()` に `scope` パラメータを追加: "item", "chunk", "both"
- `extract_references_for_item()` のスキップ方式をハッシュベース重複排除に変更
- `resolve_citations()` が解決方法の内訳付き詳細統計を返すよう変更
- `get_citation_subgraph()` に `unresolved_refs` と `depth` パラメータを追加
- バージョンを 0.4.0 に更新

## [0.3.0] - 2026-02-17

### 追加
- 継続的な論文収集のための Watchlist システム（arXiv, OpenAlex）
- 発見した論文のレビュー用 Inbox（承認/却下ワークフロー）
- トレンド分析ダッシュボード（年×会場、キーフレーズ、コレクション成長）
- CLI コマンド: `ri watch add/list/run`, `ri inbox list/accept/reject`, `ri analytics export`
- Web UI ページ: `/watches`, `/inbox`, `/analytics`
- GitHub Actions CI（pytest, ruff, black）とタグトリガーのリリース
- Issue と PR のテンプレート

### 変更
- バージョンを 0.3.0 に更新

## [0.2.0] - 2025-01-01

### 追加
- 冪等 upsert によるコアアイテム管理
- ACL Anthology コネクタ（BibTeX インポート）
- OpenAlex と Semantic Scholar によるエンリッチメント
- BM25 (FTS5) + FAISS ハイブリッド検索
- PDF ダウンロードパイプライン
- 参考文献抽出と引用グラフ
- タグ管理（CLI + Web）
- フィルター付き BibTeX エクスポート
- Web UI（検索、アイテム詳細、ノート、コレクション、引用グラフ）
- テスト 41 件パス
