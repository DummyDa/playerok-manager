from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.web_security import budget


def test_public_bootstrap_is_blocked_even_if_proxy_rewrites_host_to_local(db):
    db.config.env["PUBLIC_ORIGIN"]="https://admin.example.com"
    for base in ["https://admin.example.com","http://127.0.0.1:8101"]:
        with TestClient(create_app(db),base_url=base) as client:
            assert client.post("/api/v1/auth/setup",headers={"X-Forwarded-For":"127.0.0.1","X-Forwarded-Host":"localhost"},json={"password":"attacker-password"}).status_code==403
    assert not db.secret("password")


def test_remote_peer_cannot_initialize_local_host(db):
    with TestClient(create_app(db),client=("203.0.113.4",1234)) as client:
        assert client.post("/api/v1/auth/setup",json={"password":"attacker-password"}).status_code==403


def test_forwarded_identity_rotation_cannot_escape_global_login_budget(db):
    app=create_app(db)
    for i in range(11):
        with TestClient(app,client=(f"203.0.113.{i}",1234)) as client:
            response=client.post("/api/v1/auth/login",headers={"X-Forwarded-For":f"198.51.100.{i}"},json={"password":"wrong-password"})
            assert response.status_code==(401 if i<10 else 429)


def test_request_budget_is_atomic_across_concurrent_callers(db):
    def take(_):
        try:budget(db,"test","all",5,60);return True
        except HTTPException as exc:assert exc.status_code==429;return False
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(take,range(20)))
    assert sum(results)==5


def test_auth_responses_are_not_cached_and_local_forwarded_proto_cannot_change_cookie(db):
    with TestClient(create_app(db)) as client:
        response=client.post("/api/v1/auth/setup",headers={"X-Forwarded-Proto":"https"},json={"password":"local-test-password"})
        assert response.status_code==200
        assert response.headers["cache-control"]=="no-store"
        assert response.headers["x-frame-options"]=="DENY"
        assert "Secure" not in response.headers["set-cookie"]


def test_mrkt_request_slots_are_shared_between_database_connections(db):
    from backend.app.db import Database
    from backend.app.safety import IntegrationError
    other=Database(db.config)
    try:
        assert db.reserve_throttle("mrkt",1.5)==0
        assert other.reserve_throttle("mrkt",1.5)>1
        import pytest
        with pytest.raises(IntegrationError):db.reserve_throttle("mrkt",1.5)
    finally:other.engine.dispose()
