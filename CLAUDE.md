# CLAUDE.md — Project Rules for Research Intelligence

## Git ワークフロー（必須）

コード変更を伴う作業は、必ず以下の流れで行うこと。直接 main にコミット・プッシュしてはいけない。

1. **Issue 作成** — 作業内容を GitHub Issue として起票する（既存 Issue がある場合はスキップ）
2. **ブランチ作成** — `feature/<短い説明>` or `fix/<短い説明>` の命名規則でブランチを切る（例: `feature/view-history`, `fix/ci-python311`）
3. **コード実装** — ブランチ上でコミットする
4. **PR 作成** — `gh pr create` で PR を作成し、本文に `Closes #<Issue番号>` を含める
5. **マージ** — ユーザの承認後に `gh pr merge` でマージする（マージ前にユーザに確認すること）
6. **Issue 自動クローズ** — PR マージ時に `Closes #XX` により自動クローズされる

### 注意事項
- main ブランチへの直接 push は禁止
- PR 作成時は必ず関連 Issue を紐付ける
- 複数の無関係な変更を1つの PR にまとめない
- CI が通っていることを確認してからマージを提案する

## コーディング規約

- フォーマッタ: `black`
- リンタ: `ruff`
- CI は Python 3.11 で実行される。3.12+ 専用構文（f-string 内改行など）は使わないこと
- コミットメッセージは日本語可。prefix は `feat:` / `fix:` / `docs:` / `style:` / `refactor:` / `test:` を使用

## プロジェクト構成

- Python 仮想環境: `.venv/`
- DB: `db/app.sqlite`
- Web UI: FastAPI + Jinja2 + HTMX
- CLI: Typer (`ri` コマンド)
- テスト: `pytest tests/ -v`
- リポジトリ: `ishigaki0302/ResearchIntelligence`
