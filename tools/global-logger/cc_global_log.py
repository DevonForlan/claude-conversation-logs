#!/usr/bin/env python3
"""Global Claude Code conversation logger v2 -> private GitHub repo (reliable final-response sync).

Hook mode (stdin = hook JSON; events UserPromptSubmit, Stop, SessionEnd, SessionStart, ...):
    reconcile the session transcript into <repo>/logs/<project>/<session>/conversation.md (+metadata.json),
    journal the event, enqueue the session in the DURABLE queue, spawn a detached worker. Never prints,
    never fails the caller (exit 0).
Worker (--worker): drain the queue: settle -> reconcile -> rebuild indexes -> commit -> push -> REMOTE VERIFY
    -> mark sync_complete. A failed step leaves the queue entry in place; every later hook retries it.
Tools:
    --health [--project P] [--session S] [--json]   is the latest complete response visible on GitHub?
    --backfill (--session S | --project P)           rebuild conversation from the local transcript + sync + verify
    --repair                                         detect stale/inconsistent state, fix, push, verify
    --selftest                                       redaction checks
Env overrides (tests): CC_LOG_REPO, CC_LOG_STATE, CC_LOG_PROJECTS (transcript root), CC_LOG_NO_SPAWN=1.

Design (see docs): canonical truth = conversation.md; turn lifecycle is DERIVED from it (user / assistant
markers) plus flags stored in state (stop_turn, end_seen); sync_complete is only set after the remote
(ls-remote + git show of the remote commit) holds the same conversation bytes and metadata.
"""
import hashlib, json, os, re, subprocess, sys, time, datetime as dt
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
REPO = Path(os.environ.get("CC_LOG_REPO") or HOME / ".claude" / "cc-log-repo")
STATE = Path(os.environ.get("CC_LOG_STATE") or HOME / ".claude" / "cc-log-state")
PROJECTS_ROOT = Path(os.environ.get("CC_LOG_PROJECTS") or HOME / ".claude" / "projects")
ERRLOG = STATE / "errors.log"
PENDING = STATE / "pending.json"      # debounce marker {"due_at": ts}
DIRTY = STATE / "dirty.flag"          # legacy marker, still honoured
JOURNAL = STATE / "journal.jsonl"     # append-only event journal (stable event ids)
QUEUE = STATE / "queue.json"          # durable queue: {sid: {...}}; entries are removed ONLY after remote verification
SYNC = STATE / "sync_status.json"     # last verified sync per session + global last push result
DEBOUNCE_PROMPT = 90   # seconds, UserPromptSubmit (mid-turn snapshot)
DEBOUNCE_STOP = 3      # seconds, Stop / SessionEnd
SETTLE_MAX = 20        # seconds the worker waits for the transcript to finish flushing after Stop
MAX_TOOL_IN, MAX_TOOL_OUT = 240, 300   # tool lines only; user/assistant text is NEVER truncated
SCHEMA = 2

# ------------------------------------------------------------------ redaction
R = "[REDACTED]"
_SECRET_KEY = (r"password|passwd|pwd|passphrase|secret|client[_-]?secret|api[_-]?key|apikey|access[_-]?key|"
               r"secret[_-]?key|private[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|"
               r"token|csrf[_-]?token|xsrf[_-]?token|session[_-]?(?:secret|token|key|cookie)|sessionid|"
               r"smtp[_-]?pass(?:word)?|ldap[_-]?pass(?:word)?|bind[_-]?pw|credential[s]?")
_QUERY_SECRETS = (r"token|access_token|id_token|refresh_token|api_key|apikey|key|sig|signature|session|jwt|auth|code|ticket|otp|"
                  r"password|secret|x-amz-signature|x-amz-credential|x-amz-security-token|sas|sv|se|sp")
_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.S), R),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"), R),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), R),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}"), R),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), R),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), R),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), R),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), R),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), R),
    (re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"), R),                                                # Google OAuth access token
    (re.compile(r"\b1//[A-Za-z0-9_\-]{30,}"), R),                                                   # Google OAuth refresh token
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), R),            # JWT
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{20,}"), R),                                                   # JWT fragment / base64url JSON
    # URI credentials  scheme://user:pass@host  (mongodb, mongodb+srv, ldap, smtp, postgres, https ...)
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{1,20}://[^\s:/@]+:)([^\s@/]+)(@)"), r"\1" + R + r"\3"),
    # Authorization / Cookie / Set-Cookie headers: redact rest of line
    (re.compile(r"(?im)^(\s*[\"']?(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)[\"']?\s*[:=]\s*).+$"), r"\1" + R),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]{12,}=*"), r"\1 " + R),
    # ENV style  DB_PASSWORD=xxx  /  MONGO_URI_SECRET: xxx
    (re.compile(r"(?m)\b([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*_(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|CREDENTIALS?)\s*=\s*)(\"[^\"\n]+\"|'[^'\n]+'|[^\s\"']+)"), r"\1" + R),
    # key = value / "key": "value"
    (re.compile(r"(?i)(\"?\b(?:" + _SECRET_KEY + r")\b\"?\s*[:=]\s*)(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s,;'\"<>]{3,})"), r"\1" + R),
    # URL query secrets (checkout/SSO/signed URLs)
    (re.compile(r"(?i)([?&](?:" + _QUERY_SECRETS + r")=)[^&\s\"'<>]+"), r"\1" + R),
    # personal data (org policy): e-mail addresses
    (re.compile(r"\b(?!noreply@anthropic\.com\b)[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
]
_ENV_NAME = re.compile(r"(?i)(KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|CREDENTIAL)")


def _env_values():
    vals = []
    for k, v in os.environ.items():
        if _ENV_NAME.search(k) and len(v) >= 8 and not v.isdigit():
            vals.append(v)
    return sorted(set(vals), key=len, reverse=True)


def redact(text, counter=None):
    n = 0
    for v in _env_values():
        if v in text:
            n += text.count(v)
            text = text.replace(v, R)
    for pat, rep in _PATTERNS:
        text, k = pat.subn(rep, text)
        n += k
    if counter is not None:
        counter[0] += n
    return text


# ------------------------------------------------------------------ helpers
def log_err(msg):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(ERRLOG, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {redact(str(msg))}\n")
    except Exception:
        pass


class FileLock:
    """Cross-process lock via msvcrt (Windows) / fcntl; blocking with timeout or try-once."""
    def __init__(self, path, timeout=20):
        self.path, self.timeout, self.f = Path(path), timeout, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+b")
        end = time.time() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.time() >= end:
                    self.f.close(); self.f = None
                    return False
                time.sleep(0.1)

    def release(self):
        if not self.f:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0); msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close(); self.f = None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"lock timeout {self.path}")
        return self

    def __exit__(self, *a):
        self.release()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_json(path, default=None):
    """utf-8-sig tolerant (a BOM from PowerShell/Notepad must never break the logger); partial/corrupt -> default."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def read_text(path):
    return Path(path).read_text(encoding="utf-8-sig", errors="replace")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def project_safe(cwd):
    base = Path(cwd).name or "root"
    safe = re.sub(r"[^\w.\-]+", "-", base, flags=re.U).strip("-.") or "project"
    h = hashlib.sha1(str(cwd).lower().replace("\\", "/").encode("utf-8")).hexdigest()[:6]
    return f"{safe}-{h}"


def one_line(s, n):
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[:n] + "…"


def local_ts(iso):
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso or "?"


_SYS_REM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def tool_in_summary(name, inp):
    if isinstance(inp, dict):
        for k in ("command", "description", "file_path", "path", "pattern", "url", "query", "prompt", "skill"):
            if inp.get(k):
                return one_line(inp[k], MAX_TOOL_IN)
        return one_line(json.dumps(inp, ensure_ascii=False), MAX_TOOL_IN)
    return one_line(inp, MAX_TOOL_IN)


def result_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


# ------------------------------------------------------------------ journal / queue / sync status
def journal(event, sid, **extra):
    """Append-only, stable event id = sha1(sid|event|transcript-size|minute)."""
    try:
        eid = hashlib.sha1(f"{sid}|{event}|{extra.get('tsize', '')}|{int(time.time() // 60)}".encode()).hexdigest()[:16]
        rec = {"id": eid, "ts": now_iso(), "event": event, "sid": sid, **extra}
        with FileLock(STATE / "journal.lock", timeout=5):
            with open(JOURNAL, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return eid
    except Exception as ex:
        log_err(f"journal: {ex!r}")


def queue_load():
    return read_json(QUEUE, {}) or {}


def queue_update(fn):
    with FileLock(STATE / "queue.lock", timeout=10):
        q = queue_load()
        fn(q)
        atomic_write(QUEUE, json.dumps(q, ensure_ascii=False, indent=1))
        return q


def enqueue(sid, event, **extra):
    def f(q):
        e = q.setdefault(sid, {"since": now_iso(), "events": [], "attempts": 0})
        e["events"] = (e.get("events", []) + [event])[-20:]
        e["last_event_at"] = now_iso()
        e.update(extra)
    queue_update(f)


def sync_status():
    return read_json(SYNC, {}) or {}


def sync_update(fn):
    with FileLock(STATE / "sync.lock", timeout=10):
        s = sync_status()
        fn(s)
        atomic_write(SYNC, json.dumps(s, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ incremental render
def render_entry(e, st, counter):
    """Return markdown for one transcript entry (or '') and update st['turn']. Text is never truncated."""
    t = e.get("type")
    if t not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta"):
        return ""
    uid = e.get("uuid") or ""
    ts = local_ts(e.get("timestamp", ""))
    msg = e.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    out = []
    for b in content or []:
        bt = b.get("type") if isinstance(b, dict) else None
        if t == "user" and bt == "text":
            txt = _SYS_REM.sub("", b.get("text", "")).strip()
            if not txt:
                continue
            st["turn"] = st.get("turn", 0) + 1
            txt = redact(txt, counter)
            st["last_user"] = one_line(txt, 500)
            out.append(f"\n### [{ts}] 👤 User (turn {st['turn']}) <!--id:{uid}-->\n\n{txt}\n")
        elif t == "user" and bt == "tool_result":
            raw = result_text(b.get("content"))
            tag = " ⚠️error" if b.get("is_error") else ""
            out.append(f"- ↩︎ result{tag} ({len(raw)} chars): {redact(one_line(raw, MAX_TOOL_OUT), counter)} <!--id:{uid}-->\n")
        elif t == "assistant" and bt == "text":
            txt = b.get("text", "").strip()
            if not txt:
                continue
            txt = redact(txt, counter)
            st["last_assistant"] = one_line(txt, 700)
            out.append(f"\n### [{ts}] 🤖 Claude <!--id:{uid}-->\n\n{txt}\n")
        elif t == "assistant" and bt == "tool_use":
            s = redact(tool_in_summary(b.get("name"), b.get("input")), counter)
            out.append(f"- 🔧 **{b.get('name')}**: {s} <!--id:{uid}-->\n")
    return "".join(out)


_MARK = re.compile(r"^### \[[^\]]*\] (?:👤 User \(turn (\d+)\)|🤖 Claude) <!--id:([^>\s]+)-->", re.M)


def analyze_md(text):
    """Derive the turn lifecycle from the canonical conversation.md."""
    turns, cur = [], None
    for m in _MARK.finditer(text):
        if m.group(1):
            cur = {"turn": int(m.group(1)), "user_id": m.group(2), "assistant_id": None, "assistant_blocks": 0}
            turns.append(cur)
        elif cur is not None:
            cur["assistant_id"] = m.group(2)
            cur["assistant_blocks"] += 1
    return turns


def load_seen(md):
    if not md.exists():
        return set()
    return set(re.findall(r"<!--id:([^>\s]+)-->", read_text(md)))


def transcript_path_for(sid, hint=""):
    if hint and Path(hint).exists():
        return Path(hint)
    try:
        for p in PROJECTS_ROOT.glob(f"*/{sid}.jsonl"):
            return p
    except Exception:
        pass
    return None


def compute_status(turns, st, event):
    if st.get("end_seen") and event != "UserPromptSubmit":
        return "ended"
    if not turns:
        return "idle" if st.get("stop_turn") is not None else "working"
    last = turns[-1]
    if st.get("stop_turn") == last["turn"] and (last["assistant_id"] or st.get("settled")):
        return "idle"
    return "working"


def process_session(payload, force_full=False):
    """Reconcile one session: ingest new transcript entries (idempotent, uuid-deduped), update lifecycle flags,
    metadata and state. Safe to call from any hook / worker / repair at any time."""
    sid = payload.get("session_id") or ""
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    event = payload.get("hook_event_name") or "Stop"
    if not sid:
        return None
    (STATE / "sessions").mkdir(parents=True, exist_ok=True)
    tpath = transcript_path_for(sid, payload.get("transcript_path") or "")
    st_p = STATE / "sessions" / f"{sid}.json"
    with FileLock(STATE / "sessions" / f"{sid}.lock", timeout=20):
        st = read_json(st_p, {}) or {}
        proj = st.get("proj") or project_safe(cwd)
        st["proj"] = proj
        sdir = REPO / "logs" / proj / sid
        md, meta_p = sdir / "conversation.md", sdir / "metadata.json"
        counter = [0]
        meta = read_json(meta_p, {}) or {}
        new_md = ""
        tsize = 0
        if tpath and tpath.exists():
            tsize = tpath.stat().st_size
            offset = 0 if force_full else st.get("offset", 0)
            if offset > tsize:                     # transcript rewritten (compaction) -> reparse, dedup by id
                offset = 0
            seen = load_seen(md) if (offset == 0) else set()
            if offset == 0 and md.exists():       # state lost: recover turn counter from the log
                turns_found = re.findall(r"\(turn (\d+)\)", read_text(md))
                st["turn"] = max([int(x) for x in turns_found] + [st.get("turn", 0)])
            if offset and not md.exists():
                offset, seen = 0, set()
            with open(tpath, "rb") as f:
                f.seek(offset)
                data = f.read()
            end = data.rfind(b"\n") + 1            # only complete lines
            for line in data[:end].splitlines():
                try:
                    e = json.loads(line.decode("utf-8-sig", errors="replace"))
                except Exception:
                    continue
                uid = e.get("uuid") or ""
                if uid in seen:
                    continue
                if uid:
                    seen.add(uid)
                new_md += render_entry(e, st, counter)
            st["offset"] = offset + end
            st["transcript_path"] = str(tpath)
        if not md.exists():
            header = (f"# Claude Code conversation\n\n- project: `{Path(cwd).name}`\n- cwd: `{redact(str(cwd))}`\n"
                      f"- session: `{sid}`\n- started: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n")
            atomic_write(md, header + new_md)
        elif new_md:
            with open(md, "a", encoding="utf-8", newline="\n") as f:
                f.write(new_md)
        text = read_text(md) if md.exists() else ""
        turns = analyze_md(text)
        # lifecycle flags from the hook events (reconciled against the derived turns)
        last_turn = turns[-1]["turn"] if turns else 0
        if event == "UserPromptSubmit":
            st["stop_turn"] = None; st["end_seen"] = False; st["settled"] = False
        elif event == "Stop":
            st["stop_turn"] = last_turn; st["stop_seen_at"] = now_iso()
        elif event == "SessionEnd":
            st["end_seen"] = True; st["stop_turn"] = last_turn if st.get("stop_turn") is None else st["stop_turn"]
        if st.get("stop_turn") is not None and st["stop_turn"] != last_turn and turns:
            # a newer user turn exists than the one a Stop was recorded for -> that older turn finished; new one is working
            pass
        status = compute_status(turns, st, event)
        last = turns[-1] if turns else {}
        core = {
            "schema": SCHEMA, "session_id": sid, "project": Path(cwd).name, "project_safe": proj, "cwd": redact(str(cwd)),
            "log_path": f"logs/{proj}/{sid}/conversation.md", "turns": last_turn, "status": status,
            "latest_turn": last_turn,
            "turn_state": {
                "user_prompt_seen": bool(last), "assistant_response_seen": bool(last.get("assistant_id")),
                "stop_seen": st.get("stop_turn") == last_turn and last_turn > 0, "session_end_seen": bool(st.get("end_seen")),
            },
            "assistant_complete": bool(last.get("assistant_id")) and status in ("idle", "ended"),
            "last_user_id": last.get("user_id"), "last_assistant_id": last.get("assistant_id"),
            "conversation_sha256": sha(text), "conversation_bytes": len(text.encode("utf-8")),
            "last_user_message": st.get("last_user", meta.get("last_user_message", "")),
            "last_assistant_message": st.get("last_assistant", meta.get("last_assistant_message", "")),
        }
        old_core = {k: meta.get(k) for k in core}
        changed = bool(new_md) or core != old_core or not meta_p.exists()
        if changed or event == "UserPromptSubmit":
            meta.update(core)
            meta.update({
                "started_at": meta.get("started_at") or now_iso(), "updated_at": now_iso(),
                "last_event": meta.get("last_event", event) if event == "Rescan" else event,
                "redaction_applied": bool(meta.get("redaction_applied")) or counter[0] > 0,
                "redaction_count": meta.get("redaction_count", 0) + counter[0],
            })
            atomic_write(meta_p, json.dumps(meta, ensure_ascii=False, indent=2))
        atomic_write(st_p, json.dumps(st))
    return meta


# ------------------------------------------------------------------ scheduling / worker
def schedule(delay):
    due = time.time() + delay
    cur = (read_json(PENDING, {}) or {}).get("due_at")
    if cur is None or due < cur:
        atomic_write(PENDING, json.dumps({"due_at": due}))


def spawn_worker():
    if os.environ.get("CC_LOG_NO_SPAWN"):
        return
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)


_NO_WIN = 0x08000000 if os.name == "nt" else 0


def git(*args, check=False, timeout=90):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=check, creationflags=_NO_WIN,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def valid_meta(m):
    return isinstance(m, dict) and all(k in m for k in ("project_safe", "log_path", "updated_at", "session_id", "project"))


def all_metas():
    out = []
    for p in (REPO / "logs").glob("*/*/metadata.json"):
        m = read_json(p)
        if valid_meta(m):
            out.append(m)       # foreign/hand-written metadata with another schema is skipped
    return out


def build_indexes(sessions):
    """Pure: compute latest.json / projects.json content from session metadata."""
    sessions = sorted(sessions, key=lambda m: m.get("updated_at", ""), reverse=True)
    top = sessions[0]
    cutoff = (dt.datetime.now().astimezone() - dt.timedelta(hours=6)).isoformat(timespec="seconds")
    latest = {
        "updated_at": top["updated_at"], "project": top["project"], "project_safe": top["project_safe"],
        "session_id": top["session_id"], "latest_log": top["log_path"],
        "metadata": top["log_path"].rsplit("/", 1)[0] + "/metadata.json",
        "latest_turn": top.get("turns", 0), "status": top.get("status"),
        "assistant_complete": top.get("assistant_complete"), "last_assistant_id": top.get("last_assistant_id"),
        "conversation_sha256": top.get("conversation_sha256"),
        "last_user_message": top.get("last_user_message", ""),
        "last_assistant_message": top.get("last_assistant_message", ""),
        "active_sessions": [m["session_id"] for m in sessions if m.get("status") in ("working", "idle") and m["updated_at"] >= cutoff],
    }
    projects = {}
    for m in sessions:
        p = projects.setdefault(m["project_safe"], {
            "project": m["project"], "project_safe": m["project_safe"], "last_activity": m["updated_at"],
            "latest_session": m["session_id"], "latest_log": m["log_path"], "status": m.get("status"),
            "latest_turn": m.get("turns", 0), "sessions": 0})
        p["sessions"] += 1
    plist = sorted(projects.values(), key=lambda x: x["last_activity"], reverse=True)
    return latest, {"updated_at": top["updated_at"], "projects": plist}


def rebuild_indexes():
    sessions = all_metas()
    if not sessions:
        return
    latest, projects = build_indexes(sessions)
    for name, obj in (("latest.json", latest), ("projects.json", projects)):
        p = REPO / name
        old = read_json(p, {}) or {}
        if {k: v for k, v in old.items() if k != "updated_at"} != {k: v for k, v in obj.items() if k != "updated_at"} or not p.exists():
            atomic_write(p, json.dumps(obj, ensure_ascii=False, indent=2))


def final_scan():
    """Safety net: re-redact every changed md/json before commit; never push original secrets."""
    out = git("status", "--porcelain").stdout
    for line in out.splitlines():
        rel = line[3:].strip().strip('"')
        p = REPO / rel
        if not p.exists() or p.suffix not in (".md", ".json"):
            continue
        if "logs" in Path(rel).parts or rel in ("latest.json", "projects.json", "health.json"):
            t = read_text(p)
            t2 = redact(t, [0])
            if t2 != t:
                atomic_write(p, t2)


def known_payloads():
    out = []
    for pf in (STATE / "sessions").glob("*.payload.json"):
        pl = read_json(pf)
        if isinstance(pl, dict) and pl.get("session_id"):
            out.append(pl)
    return out


def reconcile_all(max_age=7 * 86400):
    """Re-ingest every known session (cheap when nothing changed). Repairs missed hooks: a Stop that fired before the
    final response hit disk, a session whose Stop never fired, etc."""
    for pl in known_payloads():
        try:
            tp = transcript_path_for(pl["session_id"], pl.get("transcript_path") or "")
            if tp is None or time.time() - tp.stat().st_mtime > max_age:
                continue
            process_session({**pl, "hook_event_name": "Rescan"})
        except Exception as ex:
            log_err(f"reconcile {pl.get('session_id')}: {ex!r}")


def settle(sids, max_wait=SETTLE_MAX):
    """After Stop the final assistant line may still be flushing: poll until the transcript is quiet for 2 polls."""
    pls = {}
    for p in known_payloads():
        if p["session_id"] in sids and (read_json(STATE / "sessions" / f"{p['session_id']}.json", {}) or {}).get("stop_turn") is not None                 and not (read_json(STATE / "sessions" / f"{p['session_id']}.json", {}) or {}).get("settled"):
            pls[p["session_id"]] = p
    last_size, quiet, t0 = {}, 0, time.time()
    while time.time() - t0 < max_wait:
        sizes = {}
        for sid, pl in pls.items():
            tp = transcript_path_for(sid, pl.get("transcript_path") or "")
            sizes[sid] = tp.stat().st_size if tp and tp.exists() else 0
            process_session({**pl, "hook_event_name": "Rescan"})
        quiet = quiet + 1 if sizes == last_size else 0
        last_size = sizes
        if quiet >= 2:
            break
        time.sleep(1)
    for sid in pls:        # quiet transcript after Stop: whatever exists is the final state
        s_p = STATE / "sessions" / f"{sid}.json"
        with FileLock(STATE / "sessions" / f"{sid}.lock", timeout=10):
            st = read_json(s_p, {}) or {}
            if st.get("stop_turn") is not None:
                st["settled"] = True
                atomic_write(s_p, json.dumps(st))
        process_session({**pls[sid], "hook_event_name": "Rescan"})


# ------------------------------------------------------------------ remote verification
def current_branch():
    return git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"


def remote_head(branch):
    r = git("ls-remote", "origin", f"refs/heads/{branch}", timeout=60)
    if r.returncode != 0:
        return None, r.stderr.strip()[:200]
    parts = r.stdout.split()
    return (parts[0] if parts else None), ""


def _nl(t):
    return t.replace(chr(13) + chr(10), chr(10))


def remote_blob(rev, rel):
    r = git("show", f"{rev}:{rel}")
    return r.stdout if r.returncode == 0 else None


def verify_remote(branch, sids):
    """True only if GitHub's branch head contains our HEAD and, for each session, the remote conversation.md /
    metadata.json equal the local committed files (so the latest turn + complete assistant response are remote)."""
    head = git("rev-parse", "HEAD").stdout.strip()
    rsha, err = remote_head(branch)
    info = {"local_head": head, "remote_head": rsha, "ok": False, "sessions": {}}
    if not rsha:
        info["error"] = err or "no remote head"
        return False, info
    if rsha != head:
        git("fetch", "-q", "origin", branch, timeout=90)
        anc = git("merge-base", "--is-ancestor", head, rsha)
        if anc.returncode != 0:
            info["error"] = "remote head does not contain local HEAD"
            return False, info
    ok = True
    for sid in sids:
        m = next((x for x in all_metas() if x["session_id"] == sid), None)
        if not m:
            continue
        rel_md = m["log_path"]
        rel_meta = rel_md.rsplit("/", 1)[0] + "/metadata.json"
        h_md, h_meta = remote_blob("HEAD", rel_md), remote_blob("HEAD", rel_meta)
        r_md, r_meta = remote_blob(rsha, rel_md), remote_blob(rsha, rel_meta)
        s_ok, why, hm = False, "", {}
        if h_md is None or h_meta is None:
            why = "not committed"
        elif r_md is None or r_meta is None:
            why = "not on remote"
        else:
            try:
                hm = json.loads(h_meta.lstrip("﻿"))
            except Exception:
                hm = {}
            wt = read_text(REPO / rel_md) if (REPO / rel_md).exists() else ""
            if sha(wt) != sha(_nl(h_md)):
                why = "local newer than committed"
            elif _nl(r_md) != _nl(h_md) or _nl(r_meta) != _nl(h_meta):
                why = "remote content differs from commit"
            elif hm.get("last_assistant_id") and f"<!--id:{hm['last_assistant_id']}-->" not in r_md:
                why = "latest assistant response missing on remote"
            else:
                s_ok = True
        info["sessions"][sid] = {"ok": s_ok, "why": why, "turn": hm.get("turns"), "status": hm.get("status"),
                                 "assistant_id": hm.get("last_assistant_id")}
        ok = ok and s_ok
    info["ok"] = ok
    return ok, info


# ------------------------------------------------------------------ flush
def flush_once(sids=None):
    """One sync attempt for queued sessions. Returns True only when remote-verified (or nothing to do)."""
    if not (REPO / ".git").exists():
        log_err(f"repo missing: {REPO}")
        return True
    q = queue_load()
    sids = list(q.keys()) if sids is None else sids
    if sids:
        settle(set(sids))
    reconcile_all()
    locks = []
    for sid in (sids or []):                       # a concurrent hook must not rewrite files between add and verify
        lk = FileLock(STATE / "sessions" / f"{sid}.lock", timeout=15)
        if lk.acquire():
            locks.append(lk)
    try:
        return _flush_locked(sids)
    finally:
        for lk in locks:
            lk.release()


def _flush_locked(sids):
    with FileLock(STATE / "repo.lock", timeout=60):
        rebuild_indexes()
        final_scan()
        git("add", "-A")
        sha_c = None
        if git("diff", "--cached", "--quiet").returncode != 0:
            latest = read_json(REPO / "latest.json", {}) or {}
            msg = f"conversation-log: {latest.get('project', 'multi')} {str(latest.get('session_id', ''))[:8]} t{latest.get('latest_turn', '?')} {latest.get('status', '')} {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            r = git("commit", "-q", "-m", msg)
            if r.returncode != 0:
                log_err(f"commit failed: {r.stderr.strip()[:300]}")
                sync_update(lambda s: s.update({"last_push": {"at": now_iso(), "result": "commit-failed", "detail": r.stderr.strip()[:200]}}))
                return False
        sha_c = git("rev-parse", "HEAD").stdout.strip()
        branch = current_branch()
        rsha, err = remote_head(branch)
        if rsha is None:
            res = f"remote-unreachable: {err}"
        elif rsha == sha_c:
            res = "up-to-date"
        else:
            r = git("push", "-u", "origin", branch)
            if r.returncode != 0:
                git("pull", "--rebase", "--autostash", "origin", branch)
                r = git("push", "-u", "origin", branch)
            res = "pushed" if r.returncode == 0 else f"push-failed: {r.stderr.strip()[:200]}"
        sha_c = git("rev-parse", "HEAD").stdout.strip()
        if res.startswith(("remote-unreachable", "push-failed")):
            log_err(f"push: {res}")
            sync_update(lambda s: s.update({"last_push": {"at": now_iso(), "result": res, "commit": sha_c}}))
            queue_update(lambda q2: [q2[k].__setitem__("attempts", q2[k].get("attempts", 0) + 1) for k in q2])
            return False
        ok, info = verify_remote(branch, sids or [m["session_id"] for m in all_metas()][:0])
        stamp = now_iso()

        def mark(s):
            s["last_push"] = {"at": stamp, "result": res, "commit": sha_c}
            s["last_verify"] = {"at": stamp, "ok": ok, "remote_head": info.get("remote_head"), "detail": info.get("error", "")}
            if ok:
                s["last_verified_commit"] = info.get("remote_head")
            for sid, si in info.get("sessions", {}).items():
                if si["ok"]:
                    s.setdefault("sessions", {})[sid] = {"sync_complete": True, "synced_turn": si["turn"], "status": si["status"],
                                                          "assistant_id": si["assistant_id"], "commit": sha_c, "verified_at": stamp}
        sync_update(mark)
        if ok:
            for sid, si in info.get("sessions", {}).items():
                if si["ok"]:
                    queue_update(lambda q2, sid=sid: q2.pop(sid, None))
        else:
            log_err(f"remote verification failed: {json.dumps(info)[:300]}")
        return ok


def worker():
    lock = FileLock(STATE / "worker.lock", timeout=0)
    if not lock.acquire():
        return                      # another worker is active; it re-reads pending/queue
    try:
        fails = 0
        for _ in range(200):
            if PENDING.exists():
                due = (read_json(PENDING, {}) or {}).get("due_at", 0)
                while time.time() < due:
                    time.sleep(min(1, max(0.05, due - time.time())))
                    due = (read_json(PENDING, {}) or {}).get("due_at", due)
                    if not PENDING.exists():
                        break
                try:
                    PENDING.unlink()
                except FileNotFoundError:
                    pass
            elif not queue_load() and not DIRTY.exists():
                break
            try:
                ok = flush_once()
            except Exception as ex:
                log_err(f"flush error: {ex!r}"); ok = False
            if ok:
                fails = 0
                try: DIRTY.unlink()
                except FileNotFoundError: pass
                if not PENDING.exists() and not queue_load():
                    break
            else:
                fails += 1
                DIRTY.write_text("1")
                if fails >= 4:
                    break           # entries stay queued; the next hook of ANY session retries
                time.sleep((5, 30, 120)[min(fails - 1, 2)])
    finally:
        lock.release()
        if PENDING.exists():
            spawn_worker()


# ------------------------------------------------------------------ stale detection / repair
def detect_stale(fetch=True):
    """Return a list of {kind, session, detail}. Never raises."""
    issues = []
    try:
        branch = current_branch()
        rsha, err = remote_head(branch) if fetch else (None, "")
        if fetch and rsha is None:
            issues.append({"kind": "remote_unreachable", "detail": err})
        if fetch and rsha and rsha != git("rev-parse", "HEAD").stdout.strip():
            git("fetch", "-q", "origin", branch)
        metas = all_metas()
        for m in metas:
            sid, rel = m["session_id"], m["log_path"]
            p = REPO / rel
            if not p.exists():
                issues.append({"kind": "missing_conversation", "session": sid}); continue
            text = read_text(p)
            turns = analyze_md(text)
            if (turns[-1]["turn"] if turns else 0) != m.get("turns"):
                issues.append({"kind": "metadata_turn_mismatch", "session": sid, "detail": f"md={turns[-1]['turn'] if turns else 0} meta={m.get('turns')}"})
            if m.get("conversation_sha256") and m["conversation_sha256"] != sha(text):
                issues.append({"kind": "metadata_hash_mismatch", "session": sid})
            if m.get("status") == "idle" and turns and not turns[-1]["assistant_id"]:
                issues.append({"kind": "user_turn_without_assistant", "session": sid, "detail": f"turn {turns[-1]['turn']}"})
            if rsha:
                r_md = remote_blob(f"origin/{branch}", rel)
                r_meta = remote_blob(f"origin/{branch}", rel.rsplit("/", 1)[0] + "/metadata.json")
                if r_md is None:
                    issues.append({"kind": "remote_missing_session", "session": sid})
                else:
                    if sha(r_md.replace("\r\n", "\n")) != sha(text):
                        issues.append({"kind": "local_newer_than_remote", "session": sid})
                    try:
                        rm = json.loads((r_meta or "{}").lstrip("\ufeff"))
                    except Exception:
                        rm = {}
                    if rm.get("status") == "working" and m.get("status") in ("idle", "ended"):
                        issues.append({"kind": "remote_stuck_working", "session": sid})
        if metas:
            lat, prj = build_indexes(metas)
            cur = read_json(REPO / "latest.json", {}) or {}
            if cur.get("session_id") != lat["session_id"] or cur.get("latest_turn") != lat["latest_turn"] or cur.get("status") != lat["status"]:
                issues.append({"kind": "latest_json_stale", "detail": f"{cur.get('session_id')} -> {lat['session_id']}"})
            curp = read_json(REPO / "projects.json", {}) or {}
            if {p["project_safe"]: p["latest_session"] for p in curp.get("projects", [])} != {p["project_safe"]: p["latest_session"] for p in prj["projects"]}:
                issues.append({"kind": "projects_json_stale"})
        q = queue_load()
        if q:
            issues.append({"kind": "pending_queue", "detail": f"{len(q)} session(s)"})
    except Exception as ex:
        issues.append({"kind": "detect_error", "detail": repr(ex)})
    return issues


def repair():
    reconcile_all()
    ok = flush_once(sids=list({m["session_id"] for m in all_metas()}))
    return ok, detect_stale()


def find_session_dirs(project=None, session=None):
    out = []
    for m in all_metas():
        if session and m["session_id"] == session:
            out.append(m)
        elif project and project in (m["project"], m["project_safe"]):
            out.append(m)
    return out


def backfill(project=None, session=None):
    """Rebuild conversation.md from the local transcript (full reparse, never deletes other sessions)."""
    results = []
    metas = find_session_dirs(project, session)
    for m in metas:
        sid = m["session_id"]
        pl = next((p for p in known_payloads() if p["session_id"] == sid), {"session_id": sid})
        tp = transcript_path_for(sid, pl.get("transcript_path") or "")
        if tp is None:
            results.append({"session": sid, "ok": False, "detail": "no local transcript"}); continue
        sdir = REPO / "logs" / m["project_safe"] / sid
        md_p = sdir / "conversation.md"
        old = read_text(md_p) if md_p.exists() else ""
        before = len(analyze_md(old))
        # rebuild into a scratch state so the existing file is replaced only if the rebuild is a superset
        tmp_state = {"turn": 0}
        counter, body = [0], ""
        with open(tp, "rb") as f:
            data = f.read()
        end = data.rfind(b"\n") + 1
        seen = set()
        for line in data[:end].splitlines():
            try:
                e = json.loads(line.decode("utf-8-sig", errors="replace"))
            except Exception:
                continue
            uid = e.get("uuid") or ""
            if uid in seen:
                continue
            if uid:
                seen.add(uid)
            body += render_entry(e, tmp_state, counter)
        head = old.split("\n---\n", 1)[0] + "\n---\n" if "\n---\n" in old else (
            f"# Claude Code conversation\n\n- project: `{m['project']}`\n- session: `{sid}`\n\n---\n")
        new = head + body
        old_ids = set(re.findall(r"<!--id:([^>\s]+)-->", old))
        new_ids = set(re.findall(r"<!--id:([^>\s]+)-->", new))
        if old_ids - new_ids:
            # existing log holds entries the transcript no longer has (e.g. rewritten transcript): keep both, append missing
            results.append({"session": sid, "ok": False, "detail": f"{len(old_ids - new_ids)} ids only in existing log; not replacing"})
            process_session({**pl, "hook_event_name": "Rescan"}, force_full=True)
        else:
            with FileLock(STATE / "sessions" / f"{sid}.lock", timeout=20):
                if new != old:
                    atomic_write(md_p, new)
                s_p = STATE / "sessions" / f"{sid}.json"
                st = read_json(s_p, {}) or {}
                st["offset"] = end; st["turn"] = tmp_state["turn"]
                st["last_user"] = tmp_state.get("last_user", st.get("last_user", ""))
                st["last_assistant"] = tmp_state.get("last_assistant", st.get("last_assistant", ""))
                atomic_write(s_p, json.dumps(st))
            process_session({**pl, "hook_event_name": "Rescan"})
        after = analyze_md(read_text(md_p))
        results.append({"session": sid, "ok": True, "turns_before": before, "turns_after": len(after),
                        "bytes_before": len(old.encode("utf-8")), "bytes_after": md_p.stat().st_size, "redactions": counter[0]})
        enqueue(sid, "Backfill")
    ok = flush_once() if metas else False
    return ok, results


# ------------------------------------------------------------------ health
def health(project=None, session=None, fetch=True):
    sync = sync_status()
    q = queue_load()
    metas = all_metas()
    sel = [m for m in metas if (session and m["session_id"] == session) or (project and project in (m["project"], m["project_safe"]))]
    if not sel:
        sel = sorted(metas, key=lambda m: m["updated_at"], reverse=True)[:1]
    branch = current_branch()
    rsha, err = remote_head(branch) if fetch else (None, "skipped")
    if fetch and rsha:
        git("fetch", "-q", "origin", branch)
    rows = []
    for m in sel:
        rel = m["log_path"]
        local_md = read_text(REPO / rel) if (REPO / rel).exists() else ""
        lt = analyze_md(local_md)
        r_md = remote_blob(f"origin/{branch}", rel) if rsha else None
        r_meta = remote_blob(f"origin/{branch}", rel.rsplit("/", 1)[0] + "/metadata.json") if rsha else None
        rt = analyze_md(r_md or "")
        try:
            rm = json.loads((r_meta or "{}").lstrip("\ufeff"))
        except Exception:
            rm = {}
        in_sync = bool(r_md) and sha(r_md.replace("\r\n", "\n")) == sha(local_md)
        sid = m["session_id"]
        last_transcript = transcript_path_for(sid, (read_json(STATE / "sessions" / f"{sid}.json", {}) or {}).get("transcript_path", ""))
        warn = []
        sp = read_json(STATE / "sessions" / f"{sid}.json", {}) or {}
        if last_transcript and sp.get("offset", 0) < last_transcript.stat().st_size - 1:
            warn.append("local transcript has data not yet in the log")
        if not in_sync: warn.append("remote conversation differs from local")
        if rm.get("status") == "working" and m.get("status") != "working": warn.append("remote metadata still 'working'")
        if lt and not lt[-1]["assistant_id"]: warn.append(f"turn {lt[-1]['turn']} has no assistant response yet")
        if sid in q: warn.append("session still in pending queue")
        if m.get("redaction_applied") is None: warn.append("no redaction state")
        rows.append({
            "project": m["project"], "session": sid,
            "latest_local_turn": lt[-1]["turn"] if lt else 0, "latest_remote_turn": rt[-1]["turn"] if rt else 0,
            "local_status": m.get("status"), "remote_status": rm.get("status"),
            "local_remote_in_sync": in_sync,
            "latest_assistant_response_remote": bool(rt and rt[-1]["assistant_id"]) ,
            "assistant_complete_local": m.get("assistant_complete"),
            "pending_queue_count": len(q), "queue_has_session": sid in q,
            "last_push": sync.get("last_push"), "last_verify": sync.get("last_verify"),
            "last_verified_commit": sync.get("last_verified_commit"),
            "session_sync": (sync.get("sessions") or {}).get(sid),
            "redaction_applied": m.get("redaction_applied"), "redaction_count": m.get("redaction_count"),
            "transcript_found": bool(last_transcript), "warnings": warn,
        })
    for r in rows:
        r["chatgpt_can_see_latest_complete_response"] = bool(
            r["local_remote_in_sync"] and r["latest_assistant_response_remote"] and r["remote_status"] in ("idle", "ended") and not r["queue_has_session"])
    return {"checked_at": now_iso(), "remote": {"branch": branch, "head": rsha, "error": err}, "sessions": rows}


def print_health(h, as_json=False):
    if as_json:
        print(json.dumps(h, ensure_ascii=False, indent=2)); return
    print(f"remote {h['remote']['branch']} head={h['remote']['head']} {h['remote']['error']}")
    for r in h["sessions"]:
        print(f"- {r['project']} {r['session'][:8]}  local turn {r['latest_local_turn']} / remote turn {r['latest_remote_turn']}"
              f"  status {r['local_status']}/{r['remote_status']}  in_sync={r['local_remote_in_sync']}")
        print(f"    assistant response on remote: {r['latest_assistant_response_remote']}  queue: {r['pending_queue_count']}"
              f"  redaction: {r['redaction_applied']} ({r['redaction_count']})")
        print(f"    last push: {r['last_push']}  last verified commit: {r['last_verified_commit']}")
        for w in r["warnings"]:
            print(f"    WARNING: {w}")
        print(f"    => ChatGPT can see the latest complete response: {'YES' if r['chatgpt_can_see_latest_complete_response'] else 'NO'}")


# ------------------------------------------------------------------ entry points
def selftest():
    c = [0]
    cases = {
        "mongodb+srv://admin:Sup3rS3cret@cluster0.x.mongodb.net/db": "Sup3rS3cret",
        "Authorization: Bearer abcdefghijklmnop1234": "abcdefghijklmnop1234",
        "Cookie: sid=abc123def456; other=zzz": "abc123def456",
        "token=ghp_" + "a" * 36: "a" * 36,
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----": "MIIEabc",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----": "b3BlbnNzaC1rZXk",
        "DB_PASSWORD=hunter2hunter2": "hunter2hunter2",
        '{"password": "p@ssw0rd!"}': "p@ssw0rd!",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk": "eyJhbGci",
        "https://x.example/certification-checkout?jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhIjoiYiJ9.sigsigsigsig": "eyJhbGci",
        "https://x.example/cb?code=abcdef123456&state=ok": "abcdef123456",
        "https://s.example/f?X-Amz-Signature=deadbeefcafe0123": "deadbeefcafe0123",
        "smtp_password: mailpass99": "mailpass99",
        "ldap://cn=admin:ldappw123@ldap.corp/": "ldappw123",
        "AKIAABCDEFGHIJKLMNOP": "AKIAABCDEFGHIJKLMNOP",
        "ya29.A0ARrdaM-abcdefghijklmnopqrstuvwxyz": "A0ARrdaM",
        "mail me at someone@example.com": "someone@example.com",
    }
    bad = 0
    for src, secret in cases.items():
        out = redact(src, c)
        ok = secret not in out and "REDACTED" in out
        bad += (not ok)
        print(("PASS " if ok else "FAIL ") + one_line(src, 50) + "  ->  " + one_line(out, 60))
    keep = "normal text about mongodb replica set, path C:\\work\\file.txt"
    ok = redact(keep) == keep
    bad += (not ok)
    print(("PASS " if ok else "FAIL ") + "benign text unchanged")
    return 1 if bad else 0


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    return None


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--worker" in sys.argv:
        worker(); return
    if "--health" in sys.argv:
        h = health(_arg("--project"), _arg("--session"), fetch="--no-fetch" not in sys.argv)
        print_health(h, "--json" in sys.argv)
        sys.exit(0 if all(r["chatgpt_can_see_latest_complete_response"] for r in h["sessions"]) else 2)
    if "--repair" in sys.argv:
        ok, issues = repair()
        print(json.dumps({"synced_and_verified": ok, "remaining_issues": issues}, ensure_ascii=False, indent=2))
        sys.exit(0 if ok else 2)
    if "--backfill" in sys.argv:
        ok, res = backfill(_arg("--project"), _arg("--session"))
        print(json.dumps({"synced_and_verified": ok, "results": res}, ensure_ascii=False, indent=2))
        sys.exit(0 if ok else 2)
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        payload = json.loads(raw.lstrip("\ufeff")) if raw.strip() else {}
        event = payload.get("hook_event_name") or "Stop"
        sid = payload.get("session_id")
        if sid:
            atomic_write(STATE / "sessions" / f"{sid}.payload.json", json.dumps({**payload, "hook_event_name": event}))
            process_session(payload)
            journal(event, sid, tsize=(lambda p: p.stat().st_size if p else 0)(transcript_path_for(sid, payload.get("transcript_path") or "")))
            enqueue(sid, event)
        schedule(DEBOUNCE_PROMPT if event == "UserPromptSubmit" else (0.5 if event == "SessionStart" else DEBOUNCE_STOP))
        spawn_worker()
    except Exception as ex:
        log_err(f"hook error: {ex!r}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as ex:
        log_err(f"fatal: {ex!r}")
    sys.exit(0)
