# OpenGrab agent instructions

Before planning or changing OpenGrab, read `docs/project-spec.md` and `docs/progress.md`.

- Treat `docs/project-spec.md` as the source of truth for product requirements.
- Treat `docs/progress.md` as a status note, not proof that a feature exists or is
  complete. Inspect the current files and repository state; the progress note
  may be stale.
- Preserve all user requirements. Do not silently narrow platform or plugin
  compatibility goals. Record ambiguity in `docs/project-spec.md`'s Open decisions
  and ask the user when a decision is needed.
- Implement in small milestones with explicit acceptance criteria. Update
  `docs/progress.md` to reflect verified work and remaining items.
- Do not mark a feature complete based only on scaffolding or a successful
  import. Record the evidence used to verify it.
- Keep credentials and tokens out of source code, logs, and diagnostics.

