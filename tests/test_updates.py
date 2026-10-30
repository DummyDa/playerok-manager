import json
import time
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app import updates
from scripts import install_update as runner

CURRENT="a"*40
LATEST="b"*40

@pytest.fixture
def client(db):
    with TestClient(create_app(db)) as client:
        response=client.post("/api/v1/auth/setup",json={"password":"updater-test-password"})
        client.headers["X-CSRF-Token"]=response.json()["csrf"]
        yield client

def response(sha=LATEST):
    value=Mock(status_code=200)
    value.json.return_value={"sha":sha,"commit":{"message":"Improve dashboard","committer":{"date":"2026-10-21T12:00:00Z"}}}
    return value

def local(monkeypatch):
    monkeypatch.setattr(updates,"installation",lambda root:{"current_commit":CURRENT,"install_supported":True,"install_reason":None})
    monkeypatch.setattr(updates,"cached_is_older",lambda *args:False)

def test_get_updates_is_cached_and_never_calls_github(db,client,monkeypatch):
    local(monkeypatch)
    fake=Mock();monkeypatch.setattr(updates.requests,"get",fake)
    assert client.get("/api/v1/updates").status_code==200
    fake.assert_not_called()
    assert client.post("/api/v1/updates/check").json()["task_id"]
    fake.assert_not_called()

def test_every_api_start_queues_update_check_even_when_clock_is_in_the_future(db):
    from backend.app.db import Task
    from sqlalchemy import select
    db.put("clock","check_updates",{"next":time.time()+86400})
    for _ in range(2):
        with TestClient(create_app(db)):
            with db.sessions() as session:
                tasks=list(session.scalars(select(Task).where(Task.kind=="check_updates",Task.state=="READY")))
                assert len(tasks)==1
                tasks[0].state="COMPLETE";tasks[0].dedupe=None;session.commit()
            assert db.get("clock","check_updates")["next"]<time.time()+901

def test_new_commit_dismissal_and_later_commit_reappear(db,client,monkeypatch):
    local(monkeypatch);http=Mock();http.get.return_value=response()
    updates.check(db,http)
    status=client.get("/api/v1/updates").json()
    assert status["available"] and not status["dismissed"]
    assert status["commit_url"]=="https://github.com/DummyDa/playerok-manager/commit/"+LATEST
    assert client.post("/api/v1/updates/dismiss",json={"commit":LATEST}).status_code==200
    assert client.get("/api/v1/updates").json()["dismissed"]
    http.get.return_value=response("c"*40);updates.check(db,http)
    assert not client.get("/api/v1/updates").json()["dismissed"]

def test_check_failure_retains_last_version_and_hides_external_error(db):
    db.put("update","latest",{"latest_commit":LATEST})
    http=Mock();http.get.side_effect=RuntimeError("secret-proxy-password")
    result=updates.check(db,http)
    assert result["latest_commit"]==LATEST
    assert "secret-proxy-password" not in str(result)


def test_cached_ancestor_never_appears_as_upgrade_or_installs(db,client,monkeypatch):
    local(monkeypatch)
    monkeypatch.setattr(updates,"cached_is_older",lambda *args:True)
    db.put("update","latest",{"latest_commit":LATEST,"checked_at":time.time()})
    state=client.get("/api/v1/updates").json()
    assert state["stale_catalog"] and not state["available"]
    assert client.post("/api/v1/updates/install",json={"commit":LATEST}).status_code==400
    assert not db.settings()["emergency_stop"]


def test_cached_ancestor_detected_from_local_git_without_fetch(tmp_path):
    import subprocess
    def command(*args):
        return subprocess.check_output(["git","-C",str(tmp_path),*args],text=True).strip()
    command("init")
    command("-c","user.name=Test","-c","user.email=test@example.com","commit","--allow-empty","-m","first")
    old=command("rev-parse","HEAD")
    command("-c","user.name=Test","-c","user.email=test@example.com","commit","--allow-empty","-m","second")
    new=command("rev-parse","HEAD")
    assert updates.cached_is_older(tmp_path,old,new)
    assert not updates.cached_is_older(tmp_path,new,old)
    assert not updates.cached_is_older(tmp_path,new,new)

def test_install_requires_exact_fresh_commit_clean_sources_and_csrf(db,client,monkeypatch):
    local(monkeypatch)
    db.put("update","latest",{"latest_commit":LATEST,"checked_at":time.time()})
    assert client.post("/api/v1/updates/install",json={"commit":CURRENT}).status_code==400
    db.put("update","latest",{"checked_at":time.time()-4000})
    assert client.post("/api/v1/updates/install",json={"commit":LATEST}).status_code==400
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/updates/install",json={"commit":LATEST}).status_code==403

def test_install_is_opt_in_pauses_automation_and_deduplicates_clicks(db,client,monkeypatch):
    local(monkeypatch)
    db.put("update","latest",{"latest_commit":LATEST,"checked_at":time.time()})
    source=db.config.root/"scripts/install_update.py";source.parent.mkdir();source.write_text("# fake updater")
    (db.config.data/"processes.json").write_text(json.dumps({"heartbeat":time.time()}))
    popen=Mock();monkeypatch.setattr(updates.subprocess,"Popen",popen)
    for _ in range(2):assert client.post("/api/v1/updates/install",json={"commit":LATEST}).status_code==200
    popen.assert_not_called()  # Only the launcher may own the installer process.
    assert db.settings()["emergency_stop"]
    assert "dry_run" not in db.settings()
    assert updates.read_progress(db)["state"]=="QUEUED"
    request=json.loads((db.config.data/"updates/request.json").read_text())
    assert request["target"]==LATEST and request["previous"]==CURRENT

def test_launcher_starts_updater_once_as_a_sibling_of_api(db,monkeypatch):
    from scripts import launcher
    folder=db.config.data/"updates";folder.mkdir()
    (folder/"runner.py").write_text("# test runner")
    (folder/"request.json").write_text(json.dumps({"target":LATEST,"previous":CURRENT,"created_at":time.time()}))
    monkeypatch.setattr(launcher,"ROOT",db.config.root);monkeypatch.setattr(launcher,"DATA",db.config.data)
    popen=Mock();monkeypatch.setattr(launcher.subprocess,"Popen",popen)
    assert launcher.launch_pending_update()
    assert not launcher.launch_pending_update()
    assert popen.call_count==1 and LATEST in popen.call_args.args[0]
    assert not (folder/"request.json").exists()

def test_runner_rejects_changed_upstream_before_stopping(tmp_path,monkeypatch):
    monkeypatch.setattr(runner.shutil,"which",lambda name:"git")
    values=iter([str(tmp_path),CURRENT,"",None,"c"*40])
    monkeypatch.setattr(runner,"run",lambda *args,**kwargs:next(values))
    with pytest.raises(RuntimeError,match="Upstream changed"):runner.verify_checkout(tmp_path,LATEST,CURRENT)

def test_backup_and_restore_preserve_database_keys_and_session(db,tmp_path):
    db.secret("example","private-value");session=db.config.data/"example.session";session.write_bytes(b"session-bytes")
    folder=tmp_path/"copy";folder.mkdir()
    runner.backup_data(db.config.root,folder)
    db.secret("example","changed")
    db.engine.dispose()  # A real update closes every service before restoring SQLite.
    runner.restore_data(db.config.root,folder)
    assert db.secret("example")=="private-value"
    assert session.read_bytes()==b"session-bytes"
    assert db.settings()["emergency_stop"]

def test_installer_success_and_rollback_without_network_or_real_processes(db,monkeypatch):
    calls=[];monkeypatch.setattr(runner,"verify_checkout",lambda *args:"git")
    monkeypatch.setattr(runner,"stop",lambda *args:None)
    def run(root,args,**kwargs):calls.append(args);return "" if kwargs.get("capture") else None
    monkeypatch.setattr(runner,"run",run)
    runner.install(db.config.root,LATEST,CURRENT)
    assert updates.read_progress(db)["state"]=="COMPLETE"
    assert ["git","merge","--ff-only",LATEST] in calls
    calls.clear();failed=False
    def fail_new_setup(root,args,**kwargs):
        nonlocal failed
        calls.append(args)
        if "scripts/setup.py" in args and not failed:failed=True;raise RuntimeError("simulated build failure")
        return "" if kwargs.get("capture") else None
    monkeypatch.setattr(runner,"run",fail_new_setup)
    runner.install(db.config.root,"c"*40,CURRENT)
    assert updates.read_progress(db)["state"]=="FAILED"
    assert ["git","reset","--hard",CURRENT] in calls

def test_legacy_preview_migration_does_not_turn_on_automation(db):
    db.put("settings","default",{"dry_run":True,"purchases_enabled":True,"transfers_enabled":True})
    db.initialize()
    assert "dry_run" not in db.get("settings")
    assert db.settings()["emergency_stop"] and not db.settings()["purchases_enabled"]
