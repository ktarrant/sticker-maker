# Repository instructions

## Commits

- Commit completed, verified work at sensible milestones without waiting for another request. Keep commits focused and exclude local captures and generated output.

## Documentation ownership

- Keep `README.md` focused on what the project does and how to install and use the implemented functionality. Clearly distinguish intended behavior from available commands.
- Keep all project context documents in `docs/`: planning, roadmaps, architecture, decisions, research, audits, and experiment findings. Repository-wide contributor and agent instructions belong in this root `AGENTS.md`.
- Use `docs/system-design.md` for current technical decisions and constraints, and `docs/roadmap.md` for verified progress, upcoming work, acceptance checks, and open decisions. Link to these sources rather than duplicating their contents.

## Keep context concise and current

- Update affected documentation in the same change as the behavior, decision, or validation result it describes.
- Edit the current account in place. Proactively remove superseded plans, resolved questions, completed task lists, stale environment details, abandoned alternatives, and repeated information encountered during work.
- Preserve useful current decisions, supporting evidence, constraints, and unresolved risks. Distinguish verified results, assumptions, and proposed targets.
- Prefer short sections, concrete bullets, and links. Add a document only when it serves a distinct ongoing purpose; avoid session logs and accumulating dated reports.
- Fold useful audit or experiment findings into the relevant current document, then remove the redundant report. Keep reproducibility artifacts in job data rather than turning context docs into historical logs.

## Phase boundaries

- Keep image capture, sticker-region parsing, and Cricut export independently callable and testable. Pass saved, versioned artifacts between phases; parsing must accept existing images, and export must accept reviewed geometry without a camera attached.
- Keep phase logic outside CLI and GUI code. The eventual all-in-one GUI should compose the same phase APIs used by standalone commands and tests.
