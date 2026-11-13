# Global Claude Code conversation logger (v2)

Source of truth on this machine: `~/.claude/hooks/cc_global_log.py` (this directory holds a copy plus tests).
Private conversation logs live under `logs/<project>/<session>/{conversation.md,metadata.json}`; indexes are
`latest.json` and `projects.json`.

## Why the v1 logger could leave GitHub stale (root cause)

Observed on session `347c1401…` (remote history of `metadata.json`): the remote stayed at `turn 9 / working`
for ~8 minutes, then jumped to `idle`.

1. **No mid-turn sync.** Only `UserPromptSubmit` and `Stop` ingested the transcript. Between them, remote snapshots
   appeared only as a side effect of *another* session's hook running the "rescan" step, so a long turn showed an
   old `working` snapshot.
2. **`idle` was a hook event, not a verified state.** `Stop` set `status = idle` even when the final assistant
   text had not reached the transcript yet; only one rescan happened 3 s later.
3. **A commit/push was treated as success.** Nothing verified that GitHub held the new bytes; push failures
   (`Could not resolve host`, see `errors.log`) were retried only by the next hook, and the worker gave up after 4 tries.
4. **Assistant text was cut at 20 000 characters** (`clip`), so very long final answers were incomplete.
5. **Metadata rewritten on every rescan** (`updated_at = now`) produced a commit per rescan and made "latest" jump between sessions.

## v2 design

| Requirement | Mechanism |
|---|---|
| Complete responses | user/assistant text is never truncated (tool lines are summarised). Canonical file = `conversation.md`. |
| Turn lifecycle | derived from `conversation.md` markers + hook flags. `metadata.json` carries `turn_state` {user_prompt_seen, assistant_response_seen, stop_seen, session_end_seen}, `assistant_complete`, `last_user_id`, `last_assistant_id`, `conversation_sha256`. `idle` only when Stop was seen **and** an assistant response exists (or the transcript settled quiet for 2 polls). |
| Reconciliation | every hook (UserPromptSubmit, Stop, SessionEnd, SessionStart) re-ingests idempotently (uuid markers); the worker re-ingests *all* known sessions before each commit and `settle`s sessions after Stop. A missed Stop is repaired by the next hook of any session. |
| Durable queue | `state/queue.json` (+ append-only `state/journal.jsonl`). A session leaves the queue only after remote verification. Failures keep it queued; any later hook retries. |
| Remote verification | after push: `git ls-remote` branch head must contain local HEAD; for each queued session the remote `conversation.md` and `metadata.json` must equal the committed blobs and contain the latest assistant id. Result in `state/sync_status.json` (`last_push`, `last_verify`, `last_verified_commit`, per-session `sync_complete`, `synced_turn`). |
| Stale detection | `detect_stale()`: local newer than remote, remote stuck `working`, user turn without assistant, metadata/hash mismatch, `latest.json`/`projects.json` stale, pending queue. `--repair` fixes and re-verifies. |
| Atomic / concurrent | atomic temp+replace writes, per-session / repo / queue / sync file locks, stable event ids, idempotent ingestion, no metadata churn when nothing changed. |
| Encoding | `utf-8-sig` everywhere (stdin, state, metadata). |
| Redaction | JWT (incl. fragments), OAuth tokens, API keys, cookies, Authorization, URI credentials (mongodb/ldap/smtp), SSH/PEM keys, `.env`-style values, signed-URL / checkout query secrets, e-mail. A final scan re-redacts every changed file before commit. |

## Commands

```
python cc_global_log.py --health [--project P] [--session S] [--json] [--no-fetch]
python cc_global_log.py --backfill --session S | --project P
python cc_global_log.py --repair
python cc_global_log.py --selftest
```

`--health` exits 0 only if the latest complete response of every selected session is on GitHub
(`chatgpt_can_see_latest_complete_response`), 2 otherwise.

`--backfill` re-reads the local transcript, rebuilds `conversation.md` (replacing it only if the rebuild is a superset
of existing ids), rebuilds metadata/indexes, commits, pushes and verifies. It never touches other sessions.

## Settings

`~/.claude/settings.json` hooks (merged, never overwritten): `UserPromptSubmit`, `Stop`, `SessionEnd`, `SessionStart`
(all `python cc_global_log.py`, async, timeout 20 s).

## Tests

`python -m pytest tools/global-logger -q` (sandboxed temp bare remote; ~10 min on Windows because of git setup per test).

## Known limitations

- Mid-turn freshness is bounded by hook events (no PostToolUse hook is installed to keep overhead near zero); a turn that
  is still running shows `working`, which is accurate.
- If Claude Code is killed before any hook fires, the next hook of any session (or `--repair`) recovers it.
- The remote verification proves GitHub's branch holds the committed bytes; it does not prove a reader's cache is fresh.
