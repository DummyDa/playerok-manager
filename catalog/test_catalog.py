import json
import time

import pytest
from fastapi.testclient import TestClient

import server


def test_nginx_real_ip_requires_explicit_trust_and_configured_host(monkeypatch):
    from starlette.requests import Request
    monkeypatch.setenv("CATALOG_TRUSTED_PROXY","127.0.0.1,::1")
    def request(peer,host="gifts.example.com",forwarded="203.0.113.9"):
        return Request({"type":"http","scheme":"https","method":"GET","path":"/api/gifts","query_string":b"","server":(host,443),"client":(peer,1),"headers":[(b"host",host.encode()),(b"x-real-ip",forwarded.encode())]})
    assert server.client_ip(request("127.0.0.1"),"https://gifts.example.com")=="203.0.113.9"
    assert server.client_ip(request("198.51.100.1"),"https://gifts.example.com")=="198.51.100.1"
    assert server.client_ip(request("127.0.0.1","other.example.com"),"https://gifts.example.com")=="127.0.0.1"
    assert server.client_ip(request("127.0.0.1",forwarded="bad"),"https://gifts.example.com")=="127.0.0.1"


def test_public_catalog_allows_only_configured_host_and_origin(client,monkeypatch):
    monkeypatch.setattr(server,"public_origin",lambda:"https://gifts.example.com")
    assert client.get("/api/health",headers={"Host":"gifts.example.com"}).status_code==200
    assert client.get("/api/health",headers={"Host":"evil.example.com"}).status_code==403
    response=client.post("/api/check/missing",headers={"Host":"gifts.example.com","Origin":"https://evil.example.com"})
    assert response.status_code==403
    assert client.post("/api/check/missing",headers={"Host":"gifts.example.com","Origin":"https://gifts.example.com","Sec-Fetch-Site":"cross-site"}).status_code==403
    assert client.post("/api/check/missing",headers={"Host":"gifts.example.com","Origin":"https://gifts.example.com"}).status_code==404
    assert client.get("/api/v1/settings").status_code==404


def test_checkout_request_quota_survives_new_http_client_and_ignores_forwarded_ip(client):
    for index in range(20):
        assert client.post("/api/checkout/missing",headers={"X-Forwarded-For":f"203.0.113.{index}"},json={"price_rub":100}).status_code==404
    restarted=TestClient(server.app)
    try:assert restarted.post("/api/checkout/missing",json={"price_rub":100}).status_code==429
    finally:restarted.close()


def test_visitor_cookie_is_persistent_and_spoofed_forwarded_ip_is_ignored(client):
    client.get("/")
    token=client.cookies.get("catalog_visitor")
    assert token
    client.get("/")
    assert client.cookies.get("catalog_visitor")==token
    from types import SimpleNamespace as NS
    request=NS(cookies={"catalog_visitor":token},client=NS(host="127.0.0.1"),url=NS(hostname="localhost"),headers={"x-forwarded-for":"203.0.113.1"})
    first,_=server.checkout_visitor(request,"")
    request.headers["x-forwarded-for"]="203.0.113.2"
    second,new_token=server.checkout_visitor(request,"")
    assert first==second and new_token is None


def test_sale_prices_follow_main_dashboard_settings_without_rebuilding_snapshot(client,monkeypatch):
    settings={"ton_rate_mode":"manual","manual_ton_rub":"100","stars_rub":"1.26","catalog_profit_type":"percent","catalog_profit_value":"10","catalog_premium_rub":"13","catalog_fee_profile_id":"default"}
    class Main:
        def settings(self):return settings
        def get(self,kind,id="default"):
            return {"configured":True,"fees":[{"type":"percent","value":10,"basis":"sale"}]} if kind=="fees" else {}
    monkeypatch.setattr(server.collector,"main",Main())
    first=client.get("/api/gifts").json()["items"][0]
    quoted=client.get("/api/price",params={"ton":first["price_ton"]})
    assert quoted.status_code==200 and quoted.json()["sale_price"]==first["price_rub"]
    assert "profit_rub" not in quoted.json() and "purchase_rub" not in quoted.json()
    settings["catalog_profit_type"]="rub";settings["catalog_profit_value"]="100"
    second=client.get("/api/gifts").json()["items"][0]
    assert second["id"]==first["id"] and second["price_rub"]>first["price_rub"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path)
    server.initialize_storage()
    worker = server.Prefetcher()
    monkeypatch.setattr(server, "prefetcher", worker)
    def immediate(params, snapshot=None, offset=0, limit=20, priority=0):
        if priority != 0:
            return
        signature = server.query_signature(params)
        try:
            server.fill_browse(**params, snapshot=snapshot, offset=offset, limit=limit)
            worker.errors.pop(signature, None)
        except Exception:
            worker.errors[signature] = (time.time(), "MRKT временно недоступен")
    monkeypatch.setattr(worker, "enqueue", immediate)
    now = time.time()
    with server.connect() as conn:
        for id, collection, model, number, price in [
            ("gift-a", "Toy Bear", "Honey", 12, 5_000_000_000),
            ("gift-b", "Toy Bear", "Snow", 98, 8_000_000_000),
            ("gift-c", "Lol Pop", "Honey", 100, 3_000_000_000),
        ]:
            gift = {"id": id, "collection": collection, "model": model, "number": number,
                    "price_nano": price, "price_ton": str(price / 10**9), "checked_at": now}
            conn.execute("INSERT INTO gifts VALUES (?,?,?,?,?,?,?,?,?)",
                         (id, collection, model, "Mint", "Star", number, price, now, json.dumps(gift)))
    return TestClient(server.app)


def test_filters_sort_and_pagination(client):
    data = client.get("/api/gifts?limit=1&sort=price_asc").json()
    assert data["total"] == 3
    assert data["items"][0]["id"] == "gift-c"
    assert data["next_offset"] == 1
    assert client.get("/api/gifts?collection=Toy%20Bear&min_price=6").json()["items"][0]["id"] == "gift-b"
    assert client.get("/api/gifts?sort=price_desc&max_price=5").json()["items"][0]["id"] == "gift-a"


def test_search_is_literal_and_favorites_are_bounded(client):
    assert client.get("/api/gifts?q=%25").json()["total"] == 0
    assert client.get("/api/gifts?q=' OR 1=1 --").json()["total"] == 0
    assert client.get("/api/gifts?q=12").json()["items"][0]["id"] == "gift-a"


def test_paid_choice_cookie_and_topup_cannot_create_real_checkout(client,tmp_path,monkeypatch):
    from backend.app.db import Database,Base
    from backend.app.config import Config
    from backend.app import catalog_credit as testing
    from backend.app.catalog_pricing import price
    db=Database(Config(tmp_path/"parent"));Base.metadata.create_all(db.engine);db.initialize()
    db.configure(emergency_stop=False,purchases_enabled=True,transfers_enabled=True,delivery_tested=True,ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1.26")
    db.put("fees","default",{"configured":True,"fees":[]})
    db.put("lot","lot",{"price":100})
    payment,code=testing.create(db,"100","buyer","lot")
    monkeypatch.setattr(server,"Database",lambda _:db)
    assert client.get("/api/paid-choice").status_code==401
    activated=client.post("/api/paid-choice/activate",json={"code":code})
    assert activated.status_code==200 and activated.json()["paid_rub"]=="100"
    assert "HttpOnly" in activated.headers["set-cookie"]
    assert "code_encrypted" not in activated.text
    assert client.post("/api/checkout/gift-a",json={"price_rub":1000}).status_code==400
    shown=price(db,"5")["sale_price"]
    assert client.post("/api/paid-choice/select/gift-a",json={"price_rub":shown}).status_code==422
    with server.connect() as conn:
        gift=json.loads(conn.execute("SELECT payload FROM gifts WHERE id='gift-a'").fetchone()[0]);gift['slug']='ToyBear-12'
        conn.execute("UPDATE gifts SET payload=? WHERE id='gift-a'",(json.dumps(gift),))
    selected=client.post("/api/paid-choice/select/gift-a",json={"price_rub":shown,"recipient":"new_buyer"})
    assert selected.status_code==200 and selected.json()["topup_rub"]==str(shown-100)
    assert selected.json()["recipient"]=="new_buyer"
    assert client.post("/api/paid-choice/select/gift-c",json={"price_rub":500,"recipient":"new_buyer"}).status_code==400
    assert selected.json()["state"]=="PREPARING"
    assert client.post("/api/paid-choice/test-topup").status_code==410
    current=db.get("catalog_credit_payment",payment["id"])
    checkout=db.get("catalog_checkout",current["checkout_id"])
    db.put("catalog_checkout",checkout["id"],{"state":"READY","url":"https://playerok.com/products/topup","expires_at":time.time()+900,"lot_id":"topup-lot"})
    db.put("lot","topup-lot",{"catalog_credit_id":payment["id"],"catalog_checkout_id":checkout["id"],"funding_revision":1})
    testing.checkout_result(db,db.get("catalog_checkout",checkout["id"]))
    assert client.get("/api/paid-choice").json()["payment_url"]=="https://playerok.com/products/topup"
    from types import SimpleNamespace
    from backend.app.service import Service
    testing.handle_payment(SimpleNamespace(db=db),{"id":"deal","status":"PAID","lot_id":"topup-lot","buyer_id":"payer","sale_rub":str(shown-100)})
    testing.fulfill(Service(db),payment["id"])
    oid=db.get("catalog_credit_payment",payment["id"])["fulfillment_order_id"]
    testing.delivered(db,{**db.get("order",oid),"gift_slug":"ToyBear-12"})
    assert client.get("/api/paid-choice").json()["state"]=="DELETED"
    from backend.app import catalog_checkout
    calls=[]
    def fake_checkout(parent,gift,shown_price,**kwargs):
        calls.append((gift["id"],shown_price));return "new-ordinary-checkout"
    monkeypatch.setattr(catalog_checkout,"request_checkout",fake_checkout)
    ordinary=client.post("/api/checkout/gift-b",json={"price_rub":1000})
    assert ordinary.status_code==200 and ordinary.json()["id"]=="new-ordinary-checkout"
    assert calls==[("gift-b",1000)]
    assert client.post("/api/paid-choice/test-topup").status_code==410
    assert client.get("/api/paid-choice").json()["selected"]["id"]=="gift-a"
    assert len(db.list("catalog_checkout"))==1
    old_token=client.cookies.get("catalog_credit_session")
    assert client.delete("/api/paid-choice").status_code==200
    assert client.get("/api/paid-choice").status_code==401
    assert client.get("/api/paid-choice",headers={"Cookie":"catalog_credit_session="+old_token}).status_code==401
    db.engine.dispose()
    assert client.get("/api/gifts?ids=").json()["total"] == 0
    assert client.get("/api/gifts?ids=gift-b,gift-c").json()["total"] == 2


def test_filter_options_scope_to_collection(client):
    data = client.get("/api/filters?collection=Lol%20Pop").json()
    assert len(data["model"]) == 1
    assert data["model"][0]["name"] == "Honey"
    assert data["model"][0]["count"] == 1
    assert data["model"][0]["price_ton"] == "3"
    assert len(data["collection"]) == 2


def test_invalid_numeric_parameters(client):
    for query in ["min_price=-1", "max_price=NaN", "min_price=Infinity", "limit=1000", "offset=-1"]:
        assert client.get("/api/gifts?" + query).status_code == 422


def test_unavailable_gift_is_removed(client, monkeypatch):
    with server.connect() as conn:
        payload = conn.execute("SELECT payload FROM gifts WHERE id='gift-a'").fetchone()[0]
        conn.execute("INSERT INTO browse_entries VALUES (?,?,?,?)", ("cached-snapshot", "gift-a", 0, payload))
    monkeypatch.setattr(server.collector, "request", lambda *args: {"gifts": []})
    assert client.post("/api/check/gift-a").json()["available"] is False
    assert client.get("/api/gifts?ids=gift-a").json()["total"] == 0
    with server.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM browse_entries WHERE id='gift-a'").fetchone()[0] == 0


def test_check_keeps_exact_id_and_updates_price(client, monkeypatch):
    future = "2000-01-01T00:00:00Z"
    gift = {"id": "gift-a", "name": "ToyBear-12", "collectionName": "Toy Bear", "modelName": "Honey",
            "number": 12, "salePrice": 6_000_000_000, "isOnSale": True, "isOnPlatform": True,
            "nextTransferDate": future, "nextResaleDate": future, "unlockDate": future}
    def request(method, path, payload):
        assert path == "/gifts/saling"
        assert payload["number"] == 12
        return {"gifts": [{**gift, "id": "different-id"}, gift]}
    monkeypatch.setattr(server.collector, "request", request)
    data = client.post("/api/check/gift-a").json()
    assert data["available"] is True
    assert data["changed"] is True
    assert data["gift"]["id"] == "gift-a"
    assert client.get("/api/gifts?ids=gift-a").json()["items"][0]["price_ton"] == "6"


def test_upstream_error_does_not_delete_snapshot_or_leak_secrets(client, monkeypatch):
    def request(*args):
        raise RuntimeError("Authorization: SECRET-TOKEN")
    monkeypatch.setattr(server.collector, "request", request)
    response = client.post("/api/check/gift-a")
    assert response.status_code == 503
    assert "SECRET" not in response.text
    assert client.get("/api/gifts?ids=gift-a").json()["total"] == 1


def test_cross_origin_and_arbitrary_media_are_rejected(client):
    assert client.post("/api/check/gift-a", headers={"Origin": "https://example.com"}).status_code == 403
    assert client.get("/api/gifts", headers={"Host": "example.com"}).status_code == 403
    assert client.get("/media/" + "a" * 64).status_code == 404
    assert client.get("/api/gifts").headers["Cache-Control"] == "no-store"


def upstream_gift(index, collection="Liberty Figure"):
    return {"id": f"live-{index}", "collectionName": collection, "modelName": "Freedom",
            "number": index, "salePrice": (index + 1) * 1_000_000_000, "isOnSale": True,
            "isOnPlatform": True, "nextTransferDate": "2000-01-01T00:00:00Z"}


def test_upstream_cursor_pagination_continues_beyond_demo_limit(client, monkeypatch):
    server.save_meta("collections", [{"name": "Liberty Figure", "image": None}])
    calls = []
    def request(method, path, payload):
        assert payload["query"] is None
        assert payload["collectionNames"] == ["Liberty Figure"]
        offset = int(payload["cursor"] or 0)
        calls.append(offset)
        end = min(offset + 20, 340)
        return {"gifts": [upstream_gift(i) for i in range(offset, end)],
                "cursor": str(end) if end < 340 else "", "total": 340}
    monkeypatch.setattr(server.collector, "request", request)
    response = client.get("/api/browse?q=liberty%20figure").json()
    snapshot = response["snapshot"]
    ids = [g["id"] for g in response["items"]]
    while response["next_offset"] is not None:
        response = client.get("/api/browse", params={"q": "liberty figure", "snapshot": snapshot,
                                                     "offset": response["next_offset"]}).json()
        ids.extend(g["id"] for g in response["items"])
    assert ids == [f"live-{i}" for i in range(340)]
    assert calls == list(range(0, 340, 20))
    before = len(calls)
    assert client.get("/api/browse?q=liberty%20figure").json()["items"][0]["id"] == "live-0"
    assert len(calls) == before  # repeat visits use SQLite
    assert client.get("/api/browse", params={"q": "other", "snapshot": snapshot, "offset": 30}).status_code == 409


def test_failed_upstream_page_preserves_cursor_for_retry(client, monkeypatch):
    def first(method, path, payload):
        return {"gifts": [upstream_gift(i) for i in range(20)], "cursor": "20", "total": 40}
    monkeypatch.setattr(server.collector, "request", first)
    data = client.get("/api/browse?limit=20").json()
    def failure(*args):
        raise RuntimeError("private upstream response")
    monkeypatch.setattr(server.collector, "request", failure)
    query = {"snapshot": data["snapshot"], "offset": 20, "limit": 20}
    assert client.get("/api/browse", params=query).status_code == 503
    def retry(method, path, payload):
        assert payload["cursor"] == "20"
        return {"gifts": [upstream_gift(i) for i in range(20, 40)], "cursor": "", "total": 40}
    monkeypatch.setattr(server.collector, "request", retry)
    result = client.get("/api/browse", params=query).json()
    assert len(result["items"]) == 20
    assert result["next_offset"] is None


def test_unknown_search_never_falls_back_to_whole_market(client, monkeypatch):
    def unexpected(*args):
        pytest.fail("Unknown query must not fetch unrelated offers")
    monkeypatch.setattr(server.collector, "request", unexpected)
    result = client.get("/api/browse?q=does-not-exist-zzzz").json()
    assert result["items"] == []
    assert result["next_offset"] is None


def test_expired_cache_is_immediate_while_upstream_is_locked(client, monkeypatch):
    monkeypatch.setattr(server.collector, "request", lambda *args: {
        "gifts": [upstream_gift(i) for i in range(20)], "cursor": "20", "total": 100})
    first = client.get("/api/browse").json()
    with server.connect() as conn:
        conn.execute("UPDATE browse_queries SET updated=?", (time.time()-3600,))
    jobs = []
    monkeypatch.setattr(server.prefetcher, "enqueue", lambda *args, **kwargs: jobs.append(kwargs))
    with server.browse_lock:  # Represents an unrelated slow MRKT request.
        start = time.perf_counter()
        result = client.get("/api/browse").json()
        elapsed = time.perf_counter()-start
    assert elapsed < 0.25
    assert result["snapshot"] == first["snapshot"]
    assert len(result["items"]) == 20
    assert result["stale"] is True
    assert any(job["priority"] == 2 for job in jobs)


def test_cold_cache_schedules_background_work_without_waiting(client, monkeypatch):
    jobs = []
    monkeypatch.setattr(server.prefetcher, "enqueue", lambda *args, **kwargs: jobs.append(kwargs))
    with server.browse_lock:
        start = time.perf_counter()
        result = client.get("/api/browse").json()
        elapsed = time.perf_counter()-start
    assert elapsed < 0.25
    assert result["pending"] is True
    assert result["next_offset"] == 0
    assert result["items"] == []
    assert jobs[0]["priority"] == 0


def test_user_request_moves_a_collection_ahead_of_background_warming():
    worker = server.Prefetcher()
    target = server.default_query(collection="Toy Bear")
    worker.enqueue(server.default_query(collection="Other"), priority=20)
    worker.enqueue(target, priority=20)
    worker.enqueue(target, priority=0)
    priority, _, _, params = worker.tasks.get_nowait()
    assert priority == 0
    assert params["collection"] == "Toy Bear"
    assert len(worker.pending) == 2


def test_regular_numeric_collections_are_hidden(client):
    server.save_meta("collections", [{"name": "5776227780391864916", "title": "Coffin", "image": None}, {"name": "Toy Bear", "title": "Toy Bear", "image": None}])
    options = client.get("/api/filters").json()["collection"]
    assert [item["name"] for item in options] == ["Toy Bear"]


def test_filter_previews_and_model_dictionary_preserve_cached_details(client):
    with server.connect() as conn:
        row = conn.execute("SELECT payload FROM gifts WHERE id='gift-a'").fetchone()
        gift = json.loads(row["payload"])
        gift.update(image="/media/honey", pattern="/media/star", rarity=13, center="#abc123", edge="#123abc")
        conn.execute("UPDATE gifts SET payload=? WHERE id='gift-a'", (json.dumps(gift),))
    server.save_meta("models:Toy Bear", {"items": ["Honey", "Snow", "Unknown"]})
    data = client.get("/api/filters?collection=Toy%20Bear").json()
    honey, snow, unknown = data["model"]
    assert honey["name"] == "Honey"
    assert honey["price_ton"] == "5"  # Excludes the cheaper Lol Pop with the same model name.
    assert honey["image"] == "/media/honey"
    assert honey["rarity"] == 13
    assert unknown == {"name": "Unknown"}
    assert data["symbol"][0]["image"] == "/media/star"
    assert "rarity" not in data["backdrop"][0]


def test_dropdown_floor_uses_exact_market_query_not_partial_sample(client):
    data = client.get("/api/filters?collection=Toy%20Bear").json()
    assert "floor_price_ton" not in data["backdrop"][0]
    params = server.default_query(collection="Toy Bear", backdrop="Mint")
    with server.connect() as conn:
        conn.execute("INSERT INTO browse_queries VALUES (?,?,?,?,?,?)", ("floor-test", server.query_signature(params), "", 20, time.time(), 0))
        conn.execute("INSERT INTO browse_entries VALUES (?,?,?,?)", ("floor-test", "cheaper", 0, json.dumps({"price_ton": "2"})))
    data = client.get("/api/filters?collection=Toy%20Bear").json()
    assert data["backdrop"][0]["price_ton"] == "5"
    assert data["backdrop"][0]["floor_price_ton"] == "2"
    with server.connect() as conn:
        conn.execute("UPDATE browse_queries SET updated=? WHERE id='floor-test'", (time.time()-server.INTERVAL-1,))
    assert "floor_price_ton" not in client.get("/api/filters?collection=Toy%20Bear").json()["backdrop"][0]


def test_unsupported_floor_metadata_does_not_trigger_listing_queries(client, monkeypatch):
    monkeypatch.setattr(server.prefetcher, "enqueue", lambda *a, **kw: pytest.fail("No fallback searches"))
    for field in ("symbol", "backdrop"):
        data = client.get("/api/filter-floors", params={"field": field, "names": "Black"}).json()
        assert data == {"floors": {}, "pending": False, "source": "unsupported"}
    assert client.get("/api/filter-floors?field=invalid&names=Black").status_code == 422


def test_direct_floor_metadata_is_used_without_listing_queries(client, monkeypatch):
    server.save_meta(server.direct_floor_key("backdrop", "Toy Bear"), {"at": time.time(), "floors": {"Black": "20"}, "options": [{"name": "Black", "center": "#363638", "edge": "#0e0e0f"}]})
    monkeypatch.setattr(server.prefetcher, "enqueue", lambda *a, **kw: pytest.fail("No listing query needed"))
    data = client.get("/api/filter-floors", params={"field": "backdrop", "collection": "Toy Bear", "names": "Black"}).json()
    assert data["floors"] == {"Black": "20"}
    assert data["pending"] is False
    assert data["source"] == "Каталог"


def test_catalog_requests_collectibles_only():
    payload = server.search_payload("5776227780391864916", 1)
    assert payload["giftType"] == "Upgraded"
    assert payload["collectionNames"] == ["5776227780391864916"]


def test_newest_sort_uses_mrkt_listing_order_and_excludes_locked(client, monkeypatch):
    def request(method, path, payload):
        assert payload["ordering"] == "None"
        assert payload["lowToHigh"] is False
        assert payload["giftType"] == "Upgraded"
        assert payload["isTransferable"] is True
        good = upstream_gift(990)
        locked = {**upstream_gift(991), "nextTransferDate": "2099-01-01T00:00:00Z"}
        regular = {**upstream_gift(992), "giftType": "Regular"}
        return {"gifts": [locked, regular, good], "cursor": "", "total": 3}
    monkeypatch.setattr(server.collector, "request", request)
    data = client.get("/api/browse?sort=newest").json()
    assert [gift["id"] for gift in data["items"]] == ["live-990"]


def test_multi_filter_values_are_passed_to_mrkt_as_arrays(client, monkeypatch):
    collections = json.dumps(["Toy Bear", "Lol Pop"])
    def request(method, path, payload):
        assert set(payload["collectionNames"]) == {"Toy Bear", "Lol Pop"}
        assert set(payload["modelNames"]) == {"Honey", "Snow"}
        assert payload["backdropNames"] == ["Black", "Mint"]
        assert payload["symbolNames"] == ["Moon", "Star"]
        assert payload["giftType"] == "Upgraded"
        assert payload["isTransferable"] is True
        return {"gifts": [upstream_gift(123)], "cursor": "", "total": 1}
    monkeypatch.setattr(server.collector, "request", request)
    data = client.get("/api/browse", params={"collection": collections, "model": json.dumps(["Snow", "Honey"]), "backdrop": json.dumps(["Mint", "Black"]), "symbol": json.dumps(["Star", "Moon"])}).json()
    assert len(data["items"]) == 1


def test_local_multiple_filters_use_or_within_and_across_groups(client):
    response = client.get("/api/gifts", params={"collection": json.dumps(["Toy Bear", "Lol Pop"]), "model": "Honey"})
    assert response.json()["total"] == 2
    options = client.get("/api/filters", params={"collection": json.dumps(["Toy Bear", "Lol Pop"])}).json()
    assert {(item["collection"], item["title"]) for item in options["model"]} == {("Toy Bear", "Honey"), ("Toy Bear", "Snow"), ("Lol Pop", "Honey")}
    assert client.get("/api/browse", params={"collection": json.dumps(["Toy Bear"]*31)}).status_code == 422
    assert client.get("/api/browse", params={"backdrop": "[broken"}).status_code == 422


@pytest.mark.parametrize("with_dictionary",[False,True])
def test_models_only_constrain_their_own_collection(client, monkeypatch,with_dictionary):
    collections = json.dumps(["Algorithm Cup", "Artisan Brick"])
    model = "Artisan Brick::model::Prism"
    if with_dictionary:
        server.save_meta("models:Algorithm Cup",{"items":["Classic","Prism",*[f"Model {i}" for i in range(120)]]})
    def request(method, path, payload):
        assert payload["modelNames"] == []  # Cup is unrestricted.
        gifts = [
            {**upstream_gift(801), "collectionName": "Algorithm Cup", "modelName": "Classic"},
            {**upstream_gift(802), "collectionName": "Algorithm Cup", "modelName": "Prism"},
            {**upstream_gift(803), "collectionName": "Artisan Brick", "modelName": "Prism"},
            {**upstream_gift(804), "collectionName": "Artisan Brick", "modelName": "Classic"},
        ]
        return {"gifts": gifts, "cursor": "", "total": 4}
    monkeypatch.setattr(server.collector, "request", request)
    data = client.get("/api/browse", params={"collection": collections, "model": model}).json()
    assert [gift["id"] for gift in data["items"]] == ["live-801", "live-802", "live-803"]
    assert data["total_is_upper_bound"] is True
    local = client.get("/api/gifts", params={"collection": collections, "model": model}).json()
    assert local["total"] == 3


def test_partially_scoped_models_keep_unrestricted_collection_and_continue_cursor(client,monkeypatch):
    server.save_meta("models:Artisan Brick",{"items":[f"Model {i}" for i in range(150)]})
    calls=[]
    def request(method,path,payload):
        assert payload["modelNames"]==[]
        calls.append(payload.get("cursor"))
        if not payload.get("cursor"):
            return {"gifts":[{**upstream_gift(901),"collectionName":"Algorithm Cup","modelName":"Silver"}],"cursor":"page-2","total":4}
        return {"gifts":[
            {**upstream_gift(902),"collectionName":"Algorithm Cup","modelName":"Bronze"},
            {**upstream_gift(903),"collectionName":"Artisan Brick","modelName":"Prism"},
            {**upstream_gift(904),"collectionName":"Artisan Brick","modelName":"Classic"},
        ],"cursor":"","total":4}
    monkeypatch.setattr(server.collector,"request",request)
    response=client.get("/api/browse",params={"collection":json.dumps(["Algorithm Cup","Artisan Brick"]),"model":"Algorithm Cup::model::Bronze"})
    assert response.status_code==200
    assert [g["id"] for g in response.json()["items"]]==["live-902","live-903","live-904"]
    assert calls==["","page-2"]


def test_catalog_refreshes_its_own_exchange_rate(client, monkeypatch):
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"the-open-network": {"rub": 93.83, "last_updated_at": time.time()}}
    monkeypatch.setattr(server.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(server.collector, "main", type("Settings", (), {
        "get": lambda *args: {"rate": "94.36", "fetched_at": time.time() - 3600}})())
    server.refresh_catalog_fx()
    assert client.get("/api/status").json()["fx"]["rate"] == "93.83"
    class StaleResponse(Response):
        def json(self):
            return {"the-open-network": {"rub": 100, "last_updated_at": time.time() - 3600}}
    monkeypatch.setattr(server.requests, "get", lambda *args, **kwargs: StaleResponse())
    with pytest.raises(ValueError):
        server.refresh_catalog_fx()
    assert client.get("/api/status").json()["fx"]["rate"] == "93.83"


def test_internal_errors_are_not_returned_to_buyers(client,monkeypatch):
    from public_messages import public_data,GENERIC
    def failed(*args):raise ValueError("MRKT access_token=private ожидаемая прибыль -10 ₽")
    monkeypatch.setattr(server,"catalog_sale_price",failed)
    response=client.get("/api/price",params={"ton":"4"})
    assert response.status_code==400 and response.json()["detail"]==GENERIC
    result=public_data({"notice":"Заказ убыточен: прибыль -10 ₽","delivery_reason":"API key private","selected":{"profit_rub":20,"slug":"Gift-1"}})
    assert result["notice"]==GENERIC and result["delivery_reason"]==GENERIC
    assert result["selected"]=={"slug":"Gift-1"}
    assert public_data({"detail":"Цена изменилась. Обновите карточку"})["detail"]=="Цена изменилась. Обновите карточку"
