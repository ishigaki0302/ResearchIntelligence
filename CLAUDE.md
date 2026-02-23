# CLAUDE.md — Project Rules for Research Intelligence

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.

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

## Workflow Orchestration

### 1. Plan Node Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

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
