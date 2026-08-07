"""Tests for supa.cli - hermetic, no network, no real files outside tmp dirs."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

from supa import cli


def make_token(seed: str = "a") -> str:
    return "sbp_" + seed * 40


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Isolate all paths and the API, and reset module-level constants."""
    store_dir = tmp_path / "config"
    store_path = store_dir / "profiles.json"
    zsh_file = tmp_path / "tokens.sh"
    token_file = tmp_path / "access-token"
    for name, value in {
        "STORE_DIR": str(store_dir),
        "STORE_PATH": str(store_path),
        "ZSH_FILE": str(zsh_file),
        "ACCESS_TOKEN_FILE": str(token_file),
        "API_BASE": "https://api.example.invalid/v1",
    }.items():
        monkeypatch.setattr(cli, name, value)
    monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)
    store_dir.mkdir(exist_ok=True)

    def fake_fetch_orgs(token):
        assert cli.TOKEN_RE.match(token)
        return [{"name": "acme", "id": "org_1"}]

    monkeypatch.setattr(cli, "fetch_orgs", fake_fetch_orgs)
    return SimpleNamespace(store_dir=store_dir, store_path=store_path, zsh_file=zsh_file, token_file=token_file)


def args(**kw):
    return SimpleNamespace(**kw)


# ---------- token/profile validation ----------

def test_token_regex_accepts_valid():
    assert cli.TOKEN_RE.match(make_token())
    assert cli.TOKEN_RE.match("sbp_oauth_" + "b" * 40)


def test_token_regex_rejects_invalid():
    assert not cli.TOKEN_RE.match("not-a-token")
    assert not cli.TOKEN_RE.match("sbp_XYZ" + "a" * 40)
    assert not cli.TOKEN_RE.match("SBPA" + "a" * 40)


def test_profile_name_validation(env):
    store = cli.load_store()
    with pytest.raises(cli.SupaError):
        cli.cmd_login(args(profile="bad name!", token=make_token(), from_file=False), store)
    assert store["profiles"] == {}


# ---------- store ----------

def test_store_roundtrip_and_perms(env):
    store = cli.load_store()
    store["profiles"]["x"] = {"token": make_token("b"), "orgs": [], "created": "now", "updated": "now"}
    cli.save_store(store)
    assert (env.store_path.stat().st_mode & 0o777) == 0o600
    reloaded = cli.load_store()
    assert reloaded["profiles"]["x"]["token"] == make_token("b")


def test_load_store_corrupt(env, capsys):
    env.store_path.write_text("{not json")
    with pytest.raises(cli.SupaError):
        cli.load_store()


# ---------- login ----------

def test_login_token_flag_stores_and_activates(env):
    store = cli.load_store()
    rc = cli.cmd_login(args(profile="alpha", token=make_token(), from_file=False), store)
    assert rc == 0
    assert store["active"] == "alpha"
    assert store["profiles"]["alpha"]["orgs"] == [{"name": "acme", "id": "org_1"}]
    zsh = env.zsh_file.read_text()
    assert f'__sb_alpha="{make_token()}"' in zsh
    assert (env.zsh_file.stat().st_mode & 0o777) == 0o600


def test_login_does_not_steal_existing_active(env):
    store = cli.load_store()
    store["active"] = "alpha"
    store["profiles"]["alpha"] = {"token": make_token("a"), "orgs": [], "created": "n", "updated": "n"}
    cli.cmd_login(args(profile="beta", token=make_token("b"), from_file=False), store)
    assert store["active"] == "alpha"


def test_login_rejects_bad_format(env):
    store = cli.load_store()
    with pytest.raises(cli.SupaError):
        cli.cmd_login(args(profile="alpha", token="junk", from_file=False), store)
    assert "alpha" not in store["profiles"]


def test_login_non_tty_without_token_errors(env, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    store = cli.load_store()
    with pytest.raises(cli.SupaError):
        cli.cmd_login(args(profile="alpha", token=None, from_file=False), store)
    assert "alpha" not in store["profiles"]


def test_login_env_token_source(env, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", make_token())
    store = cli.load_store()
    rc = cli.cmd_login(args(profile="alpha", token=None, from_file=False), store)
    assert rc == 0
    assert store["profiles"]["alpha"]["token"] == make_token()


def test_login_from_file_falls_back_when_keychain_fails(env, monkeypatch):
    env.token_file.write_text(make_token("c"))

    class FailedRun:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: FailedRun())
    store = cli.load_store()
    rc = cli.cmd_login(args(profile="alpha", token=None, from_file=True), store)
    assert rc == 0
    assert store["profiles"]["alpha"]["token"] == make_token("c")


def test_login_from_file_uses_keychain_when_present(env, monkeypatch):
    class OkRun:
        returncode = 0
        stdout = make_token("d")

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: OkRun())
    store = cli.load_store()
    rc = cli.cmd_login(args(profile="alpha", token=None, from_file=True), store)
    assert rc == 0
    assert store["profiles"]["alpha"]["token"] == make_token("d")


def test_login_api_rejects_token(env, monkeypatch):
    def reject(token):
        raise cli.SupaError("token rejected by API (401) - wrong account or revoked")

    monkeypatch.setattr(cli, "fetch_orgs", reject)
    store = cli.load_store()
    with pytest.raises(cli.SupaError):
        cli.cmd_login(args(profile="alpha", token=make_token(), from_file=False), store)
    assert "alpha" not in store["profiles"]


# ---------- use / current ----------

def test_use_sets_active_and_writes_file(env):
    store = cli.load_store()
    store["profiles"]["alpha"] = {"token": make_token(), "orgs": [], "created": "n", "updated": "n"}
    rc = cli.cmd_use(args(profile="alpha", none=False), store)
    assert rc == 0
    assert env.token_file.read_text() == make_token()
    assert (env.token_file.stat().st_mode & 0o777) == 0o600


def test_use_unknown_profile(env):
    store = cli.load_store()
    with pytest.raises(cli.SupaError):
        cli.cmd_use(args(profile="nope", none=False), store)


def test_use_none_clears(env):
    store = cli.load_store()
    store["active"] = "alpha"
    cli.cmd_use(args(profile=None, none=True), store)
    assert store["active"] is None


def test_current_no_active(capsys):
    rc = cli.cmd_current({"active": None, "profiles": {}})
    assert rc == 0
    assert "no active profile" in capsys.readouterr().out


# ---------- ls ----------

def test_ls_plain_and_json(env, capsys):
    store = {"active": "alpha", "profiles": {"alpha": {"token": make_token(), "orgs": [{"name": "acme"}], "updated": "t"}}}
    rc = cli.cmd_ls(args(json=False), store)
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "acme" in out and "yes" in out
    rc = cli.cmd_ls(args(json=True), store)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["profile"] == "alpha" and data[0]["active"] is True


def test_ls_refreshes_empty_orgs(env):
    store = {"active": None, "profiles": {"alpha": {"token": make_token(), "orgs": [], "updated": "old"}}}
    cli.cmd_ls(args(json=False), store)
    assert store["profiles"]["alpha"]["orgs"] == [{"name": "acme", "id": "org_1"}]


# ---------- rm / token ----------

def test_rm_removes_and_clears_active(env):
    store = cli.load_store()
    store["profiles"]["alpha"] = {"token": make_token(), "orgs": [], "created": "n", "updated": "n"}
    store["active"] = "alpha"
    rc = cli.cmd_rm(args(profile="alpha"), store)
    assert rc == 0
    assert "alpha" not in store["profiles"]
    assert store["active"] is None
    assert "__sb_alpha" not in env.zsh_file.read_text()


def test_token_prints(env, capsys):
    store = {"active": None, "profiles": {"alpha": {"token": make_token()}}}
    rc = cli.cmd_token(args(profile="alpha"), store)
    assert rc == 0
    assert capsys.readouterr().out.strip() == make_token()


# ---------- run ----------

def test_run_unknown_profile(env):
    store = {"active": None, "profiles": {}}
    with pytest.raises(cli.SupaError):
        cli.cmd_run(args(profile="nope", cmd=["projects", "list"]), store)


def test_run_no_command(env):
    store = {"active": None, "profiles": {}}
    with pytest.raises(cli.SupaError):
        cli.cmd_run(args(profile="alpha", cmd=[]), store)


def test_run_injects_token_and_execs(env, monkeypatch):
    captured = {}

    def fake_execvpe(exe, argv, envp):
        captured["exe"] = exe
        captured["argv"] = argv
        captured["token"] = envp.get("SUPABASE_ACCESS_TOKEN")
        raise SystemExit(0)

    monkeypatch.setattr(cli.os, "execvpe", fake_execvpe)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/local/bin/supabase")
    store = {"active": None, "profiles": {"alpha": {"token": make_token("e")}}}
    with pytest.raises(SystemExit):
        cli.cmd_run(args(profile="alpha", cmd=["db", "push"]), store)
    assert captured["exe"] == "/usr/local/bin/supabase"
    assert captured["argv"] == ["/usr/local/bin/supabase", "db", "push"]
    assert captured["token"] == make_token("e")


def test_run_passthrough_uses_active(env, monkeypatch):
    captured = {}

    def fake_execvpe(exe, argv, envp):
        captured["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(cli.os, "execvpe", fake_execvpe)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/local/bin/supabase")
    store = {"active": "alpha", "profiles": {"alpha": {"token": make_token("e")}}}
    with pytest.raises(SystemExit):
        cli.cmd_run(args(cmd=["projects", "list"]), store, passthrough=True)
    assert captured["argv"] == ["/usr/local/bin/supabase", "projects", "list"]


def test_run_passthrough_without_active(env):
    store = {"active": None, "profiles": {}}
    with pytest.raises(cli.SupaError):
        cli.cmd_run(args(cmd=["projects", "list"]), store, passthrough=True)


# ---------- zsh import ----------

def test_import_from_zsh_file(env):
    env.zsh_file.write_text(
        f'__sb_alpha="{make_token("a")}"\n__sb_beta=""\n__sb_gamma="not-a-token"\n'
    )
    store = cli.load_store()
    n = cli.import_from_zsh_file(store)
    assert n == 1
    assert "alpha" in store["profiles"]
    assert "beta" not in store["profiles"]
    assert "gamma" not in store["profiles"]


def test_import_skips_when_store_populated(env):
    store = {"active": None, "profiles": {"existing": {"token": make_token("a")}}}
    n = cli.import_from_zsh_file(store)
    assert n == 0
