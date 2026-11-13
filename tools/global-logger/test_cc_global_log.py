"""Tests for the global Claude Code conversation logger (cc_global_log.py). Fully sandboxed: temp bare 'GitHub'
remote, temp clone, temp state dir, synthetic transcripts. Run:  python -m pytest tools/global-logger -q"""
import importlib.util, json, os, subprocess, sys, threading
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
SID = "11111111-2222-3333-4444-555555555555"
SID2 = "99999999-2222-3333-4444-555555555555"
CWD = r"C:\work\demo-project"


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, encoding="utf-8")


@pytest.fixture()
def L(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, capture_output=True)
    for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
        git(repo, "config", k, v)
    git(repo, "checkout", "-q", "-B", "main")
    (repo / "README.md").write_text("x")
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "init"); git(repo, "push", "-q", "-u", "origin", "main")
    monkeypatch.setenv("CC_LOG_REPO", str(repo))
    monkeypatch.setenv("CC_LOG_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("CC_LOG_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("CC_LOG_NO_SPAWN", "1")
    spec = importlib.util.spec_from_file_location("cc_global_log_t", HERE / "cc_global_log.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SETTLE_MAX = 2
    mod.remote_dir = remote
    mod.tmp = tmp_path
    return mod


class Transcript:
    def __init__(self, path):
        self.path, self.n = Path(path), 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def _w(self, obj):
        self.n += 1
        obj.setdefault("uuid", f"u{self.n:05d}")
        obj.setdefault("timestamp", "2026-11-13T04:00:00Z")
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def user(self, text):
        self._w({"type": "user", "message": {"role": "user", "content": text}})

    def assistant(self, text):
        self._w({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})

    def tool(self, cmd="ls"):
        self._w({"type": "assistant", "message": {"role": "assistant",
                                                   "content": [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}})


def payload(tr, event, sid=SID):
    return {"session_id": sid, "transcript_path": str(tr.path), "cwd": CWD, "hook_event_name": event}


def hook(L, tr, event, sid=SID):
    """What main() does for one hook call (without the stdin/worker plumbing)."""
    pl = payload(tr, event, sid)
    L.atomic_write(L.STATE / "sessions" / f"{sid}.payload.json", json.dumps(pl))
    L.process_session(pl)
    L.enqueue(sid, event)


def meta(L, sid=SID):
    return json.loads(next((L.REPO / "logs").glob(f"*/{sid}/metadata.json")).read_text(encoding="utf-8-sig"))


def md(L, sid=SID):
    return next((L.REPO / "logs").glob(f"*/{sid}/conversation.md")).read_text(encoding="utf-8")


def test_long_assistant_response_is_not_truncated(L):
    tr = Transcript(L.tmp / "t.jsonl")
    big = "line of text 0123456789\n" * 5000   # ~120k chars
    tr.user("q"); tr.assistant(big); hook(L, tr, "Stop")
    assert big.strip() in md(L) and "truncated" not in md(L)


def test_turn_lifecycle_states(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("first"); hook(L, tr, "UserPromptSubmit")
    m = meta(L)
    assert m["status"] == "working" and m["turn_state"]["assistant_response_seen"] is False and m["assistant_complete"] is False
    tr.assistant("answer"); hook(L, tr, "Stop")
    m = meta(L)
    assert m["status"] == "idle"
    assert m["turn_state"] == {"user_prompt_seen": True, "assistant_response_seen": True, "stop_seen": True, "session_end_seen": False}
    assert m["assistant_complete"] is True and m["latest_turn"] == 1
    hook(L, tr, "SessionEnd")
    assert meta(L)["status"] == "ended" and meta(L)["turn_state"]["session_end_seen"]


def test_stop_before_final_text_is_repaired_by_rescan(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); hook(L, tr, "UserPromptSubmit")
    hook(L, tr, "Stop")                                   # Stop fires, final text not on disk yet
    assert meta(L)["assistant_complete"] is False         # no assistant text -> never reported as a complete response
    tr.assistant("the final answer")                      # flushed late
    L.process_session({**payload(tr, "Rescan")})
    m = meta(L)
    assert m["status"] == "idle" and m["assistant_complete"] is True and "the final answer" in md(L)


def test_missing_stop_is_reconciled_by_next_hook(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("one"); hook(L, tr, "UserPromptSubmit")
    tr.assistant("answer one")                            # Stop never fires
    tr.user("two"); hook(L, tr, "UserPromptSubmit")       # next hook must pick up the older response
    t = md(L)
    assert "answer one" in t and t.index("answer one") < t.index("two")
    assert meta(L)["status"] == "working" and meta(L)["turns"] == 2
    assert [x["assistant_id"] is not None for x in L.analyze_md(t)] == [True, False]


def test_idempotent_and_concurrent_ingestion(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); tr.assistant("a")
    ths = [threading.Thread(target=lambda: L.process_session(payload(tr, "Stop"))) for _ in range(6)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    L.process_session(payload(tr, "Rescan"), force_full=True)
    t = md(L)
    assert t.count("<!--id:u00001-->") == 1 and t.count("<!--id:u00002-->") == 1


def test_push_failure_keeps_queue_then_recovers(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); tr.assistant("answer"); hook(L, tr, "Stop")
    good = git(L.REPO, "remote", "get-url", "origin").stdout.strip()
    git(L.REPO, "remote", "set-url", "origin", str(L.tmp / "does-not-exist.git"))
    assert L.flush_once() is False
    assert SID in L.queue_load() and L.sync_status()["last_push"]["result"].startswith("remote-unreachable")
    git(L.REPO, "remote", "set-url", "origin", good)
    assert L.flush_once() is True
    assert SID not in L.queue_load() and L.sync_status()["sessions"][SID]["sync_complete"] is True


def test_remote_verification_detects_unpushed_commit(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); tr.assistant("answer"); hook(L, tr, "Stop")
    assert L.flush_once() is True
    tr.user("q2"); tr.assistant("answer2"); hook(L, tr, "Stop"); L.process_session(payload(tr, "Rescan"))
    L.rebuild_indexes(); git(L.REPO, "add", "-A"); git(L.REPO, "commit", "-q", "-m", "local only")
    ok, info = L.verify_remote("main", [SID])
    assert ok is False and "contain" in info.get("error", "")
    assert L.flush_once() is True and L.verify_remote("main", [SID])[0] is True
    remote_md = git(L.remote_dir, "show", f"main:{meta(L)['log_path']}").stdout
    assert "answer2" in remote_md


def test_remote_has_complete_latest_turn_and_idle_status(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("hello"); hook(L, tr, "UserPromptSubmit")
    tr.assistant("complete reply"); hook(L, tr, "Stop")
    assert L.flush_once() is True
    rel = meta(L)["log_path"]
    rm = json.loads(git(L.remote_dir, "show", f"main:{rel.rsplit('/', 1)[0]}/metadata.json").stdout)
    assert rm["status"] == "idle" and rm["turns"] == 1 and rm["assistant_complete"] is True
    latest = json.loads(git(L.remote_dir, "show", "main:latest.json").stdout)
    assert latest["session_id"] == SID and latest["status"] == "idle" and latest["latest_turn"] == 1


def test_stale_detection_and_repair(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); hook(L, tr, "UserPromptSubmit")
    assert L.flush_once() is True                          # remote now says 'working'
    tr.assistant("final"); hook(L, tr, "Stop"); L.process_session(payload(tr, "Rescan"))
    kinds = {i["kind"] for i in L.detect_stale()}
    assert {"local_newer_than_remote", "remote_stuck_working", "pending_queue"} <= kinds
    ok, remaining = L.repair()
    assert ok and not [i for i in remaining if i["kind"] not in ("pending_queue",)]
    assert L.health(session=SID)["sessions"][0]["chatgpt_can_see_latest_complete_response"] is True


def test_latest_json_stale_is_detected_and_rebuilt(L):
    tr = Transcript(L.tmp / "a.jsonl")
    tr.user("a"); tr.assistant("A"); hook(L, tr, "Stop")
    tr2 = Transcript(L.tmp / "b.jsonl")
    tr2.user("b"); tr2.assistant("B"); hook(L, tr2, "Stop", SID2)
    assert L.flush_once() is True
    L.atomic_write(L.REPO / "latest.json", json.dumps({"session_id": "ghost-session", "latest_turn": 7, "status": "working", "updated_at": "2000-01-01"}))
    assert "latest_json_stale" in {i["kind"] for i in L.detect_stale(fetch=False)}
    L.repair()
    assert "latest_json_stale" not in {i["kind"] for i in L.detect_stale(fetch=False)}


def test_backfill_restores_missing_response_without_touching_other_sessions(L):
    tr = Transcript(L.tmp / "a.jsonl")
    tr.user("a"); tr.assistant("A answer"); hook(L, tr, "Stop")
    tr2 = Transcript(L.tmp / "b.jsonl")
    tr2.user("b"); tr2.assistant("B answer"); hook(L, tr2, "Stop", SID2)
    assert L.flush_once() is True
    other_before = md(L, SID2)
    p = next((L.REPO / "logs").glob(f"*/{SID}/conversation.md"))
    head = p.read_text(encoding="utf-8").split("### [")[0]
    p.write_text(head + "\n### [x] \U0001F464 User (turn 1) <!--id:u00001-->\n\na\n", encoding="utf-8")   # the answer was lost
    ok, res = L.backfill(session=SID)
    assert ok and res[0]["ok"] and "A answer" in md(L)
    assert md(L, SID2) == other_before
    assert L.health(session=SID)["sessions"][0]["local_remote_in_sync"] is True


def test_bom_in_payload_state_and_metadata(L):
    bom = "\ufeff"
    L.STATE.mkdir(parents=True, exist_ok=True)
    p = L.STATE / "bom.json"
    p.write_text(bom + json.dumps({"a": 1}), encoding="utf-8")
    assert L.read_json(p) == {"a": 1}
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); tr.assistant("a"); hook(L, tr, "Stop")
    mp = next((L.REPO / "logs").glob(f"*/{SID}/metadata.json"))
    mp.write_text(bom + mp.read_text(encoding="utf-8"), encoding="utf-8")
    L.process_session(payload(tr, "Rescan"))
    assert meta(L)["turns"] == 1
    r = subprocess.run([sys.executable, str(HERE / "cc_global_log.py")], input=(bom + json.dumps(payload(tr, "Stop"))).encode("utf-8"),
                       capture_output=True, env={**os.environ}, timeout=60)
    assert r.returncode == 0 and not (L.STATE / "errors.log").exists()


SECRETS = [
    "https://learn.example.com/certification-checkout?jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjIjoiZCJ9.abcdefabcdef",
    "mongodb+srv://admin:Sup3rS3cretPw@cluster0.example.net/db",
    "Authorization: Bearer abcdefghijklmnop1234567",
    "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE\n-----END OPENSSH PRIVATE KEY-----",
    "smtp_password: MailPass12345",
    "ldap://cn=admin:LdapPw12345@ldap.corp/",
    "Cookie: sessionid=abcdef123456; csrftoken=zzz999",
    "mail devon@example.com",
    "https://x.example/cb?code=abcdef123456&state=1",
]
FRAGMENTS = ("Sup3rS3cretPw", "abcdefghijklmnop1234567", "sk-abcdefghijklmnopqrstuvwxyz", "b3BlbnNzaC1rZXktdjE", "MailPass12345",
             "LdapPw12345", "abcdef123456", "devon@example.com", "eyJhbGci", "IjoiZCJ9")


@pytest.mark.parametrize("secret", SECRETS)
def test_secrets_never_reach_remote(L, secret):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("see " + secret); tr.assistant("echo " + secret); tr.tool("curl " + secret)
    hook(L, tr, "Stop")
    assert L.flush_once() is True
    rel = meta(L)["log_path"]
    remote = git(L.remote_dir, "show", f"main:{rel}").stdout + git(L.remote_dir, "show", "main:latest.json").stdout
    for frag in FRAGMENTS:
        assert frag not in remote


def test_health_reports_unsynced_then_synced(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); tr.assistant("a"); hook(L, tr, "Stop")
    h = L.health(session=SID)["sessions"][0]
    assert h["chatgpt_can_see_latest_complete_response"] is False and h["pending_queue_count"] == 1
    assert L.flush_once() is True
    h = L.health(session=SID)["sessions"][0]
    assert h["chatgpt_can_see_latest_complete_response"] is True and h["latest_local_turn"] == h["latest_remote_turn"] == 1
    assert h["session_sync"]["sync_complete"] is True and h["last_verified_commit"]


def test_no_commit_churn_when_nothing_changed(L):
    tr = Transcript(L.tmp / "t.jsonl")
    tr.user("q"); tr.assistant("a"); hook(L, tr, "Stop")
    assert L.flush_once()
    n = git(L.REPO, "rev-list", "--count", "HEAD").stdout
    for _ in range(3):
        L.reconcile_all(); L.flush_once(sids=[])
    assert git(L.REPO, "rev-list", "--count", "HEAD").stdout == n
