from io import BytesIO
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

from PIL import Image
import pytest

from backend.app import catalog_credit as credit, catalog_checkout
from backend.app.catalog_pricing import price
from backend.app.service import Service
from backend.app.worker import Worker


@pytest.fixture
def real(db,monkeypatch):
    db.configure(emergency_stop=False,purchases_enabled=True,transfers_enabled=True,delivery_tested=True,delivery_transport="mtproto",ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1.26",description_template="Gift {gift_link}")
    db.put("fees","default",{"configured":True,"fees":[]})
    service=Service(db);mrkt=Mock();service.mrkt=mrkt
    gift={"id":"gift","collection":"Chill Flame","model":"Ghost Light","number":5,"slug":"ChillFlame-5","price_ton":"4"}
    raw={"id":"gift","name":"ChillFlame-5","salePrice":4000000000,"isOnSale":True,"nextTransferDate":None}
    mrkt.request.return_value=raw;mrkt.ton.return_value="4";mrkt.telegram.return_value=123
    mrkt.balances.return_value={"mrkt_ton":"100","stars":1000}
    mrkt.buy.return_value={"id":"owned-gift"}
    mrkt.saved.return_value={"msg_id":1234,"can_transfer_at":0,"transfer_stars":25}
    mrkt.owner.return_value=123;mrkt.transfer.return_value=123
    client=Mock();client.create.return_value={"item_id":"topup-lot","slug":"pay-extra","status":"APPROVED"}
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:client)
    monkeypatch.setattr("backend.app.integrations.clients.Playerok",lambda _:client)
    monkeypatch.setattr(catalog_checkout,"Playerok",lambda _:client)
    picture=BytesIO();Image.new("RGB",(100,100)).save(picture,"PNG")
    monkeypatch.setattr(catalog_checkout.requests,"get",Mock(return_value=NS(content=picture.getvalue(),raise_for_status=lambda:None)))
    return service,gift,raw,client


def drive(service,oid):
    for step in range(12):
        order=service.db.get("order",oid)
        if order["stage"]=="COMPLETE":return
        service.process_order(oid)
        for op in service.db.list("operation",100000):
            if op.get("order_id")==oid and op["state"]=="QUEUED":service.execute(op["id"])
    raise AssertionError(service.db.get("order",oid))


@pytest.mark.parametrize("with_payments,fail_topup",[(False,False),(True,False),(True,True)])
def test_manual_delivery_waits_then_settles_once_without_telegram_transfer(db,real,with_payments,fail_topup):
    from backend.app.manual_delivery import allowed,confirm
    service,gift,raw,client=real
    db.configure(manual_gift_delivery=True,transfers_enabled=False,delivery_tested=False)
    account,code=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    for _ in range(5):
        service.process_order(oid)
        for op in db.list("operation"):
            if op.get("order_id")==oid and op["state"]=="QUEUED":service.execute(op["id"])
    assert db.get("order",oid)["stage"]=="AWAITING_MANUAL_DELIVERY"
    service.mrkt.transfer.assert_not_called()
    assert allowed(db,db.get("order",oid))
    assert credit.funds(db.get("catalog_credit_payment",account["id"]))==1000
    remote={}
    if with_payments:
        for key,amount,kind in [("original",900,"ORIGINAL"),("topup",100,"TOPUP")]:
            db.put("catalog_credit_receipt",key,{"credit_id":account["id"],"accepted":True,"amount_rub":str(amount),"buyer_id":"buyer-id","chat_id":"chat","kind":kind,"revision":1,"status":"PAID"})
            remote[key]={"id":key,"buyer_id":"buyer-id","sale_rub":str(amount),"status":"PAID"}
        client.orders.side_effect=lambda deal_id: [dict(remote[deal_id])]
        def set_status(key,status):
            if fail_topup and key=="topup":raise TimeoutError("unknown result")
            remote[key].update(status=status)
        client.set_deal_status.side_effect=set_status
    worker=Worker(db);worker.service=service
    worker.dispatch("manual_delivery_confirm",{"order_id":oid})
    if fail_topup:
        service.execute(oid+":mark_sent")
        assert db.get("order",oid)["stage"]=="MANUAL"
        calls=client.set_deal_status.call_count
        credit.tick(service);credit.tick(service)
        assert client.set_deal_status.call_count==calls
        assert db.get("grant_action","topup:SENT")["state"]=="UNKNOWN"
        assert not db.get("order",oid).get("catalog_settled")
        assert len(credit.purchases(db.get("catalog_credit_payment",account["id"])))==1
        service.mrkt.transfer.assert_not_called()
        return
    drive(service,oid)
    confirm(service,oid)
    credit.tick(service)
    assert db.get("order",oid)["stage"]=="COMPLETE"
    assert db.get("catalog_credit_payment",account["id"])["closed_after_delivery"] is True
    assert len(credit.purchases(db.get("catalog_credit_payment",account["id"])))==1
    service.mrkt.transfer.assert_not_called()
    service.mrkt.owner.assert_not_called()
    assert len([e for e in db.list("ledger") if e["type"]=="transfer"])==1
    assert len([e for e in db.list("ledger") if e["type"]=="revenue"])==1
    if with_payments:
        assert client.set_deal_status.call_count==2
        assert all(p["status"]=="SENT" for p in remote.values())
        assert all(p["status"]=="SENT" for p in credit.receipts(db,account["id"]))
        assert len([m for m in db.list("catalog_message") if m["event"].startswith("delivered:")])==1


@pytest.mark.parametrize("state",["UNKNOWN","SUBMITTED","BROWSER_WAIT","QUEUED"])
def test_manual_delivery_cannot_bypass_unknown_or_running_operations(db,real,state):
    from backend.app.manual_delivery import allowed,confirm
    service,*_=real
    db.configure(manual_gift_delivery=True)
    db.put("lot","lot",{"pair_id":"pair","delivery_mode":"telegram_nft"})
    order=db.put("order","order",{"stage":"AWAITING_MANUAL_DELIVERY","lot_id":"lot","gift_slug":"Gift-1","mrkt_gift_id":"gift","recipient":"buyer","recipient_id":123,"snapshot":{"transfer_rub":"31.5"}})
    db.put("operation","transfer",{"order_id":"order","state":state,"action":"order.transfer"})
    assert not allowed(db,order)
    with pytest.raises(ValueError):confirm(service,"order")
    assert not db.get("order","order").get("manual_delivery_receipt")


def test_real_selection_reserves_then_runs_existing_buy_withdraw_transfer_pipeline(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"1000","buyer")
    sale=price(db,"4")["sale_price"]
    result=credit.choose(db,account,gift,sale,"new_buyer")
    assert result["test"] is False and result["state"]=="EXECUTING"
    assert credit.purchases(db.get("catalog_credit_payment",account["id"]))==[]
    assert credit.funds(db.get("catalog_credit_payment",account["id"]))==1000
    credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    credit.fulfill(service,account["id"])
    assert len([o for o in db.list("order") if o.get("catalog_credit_id")==account["id"]])==1
    drive(service,oid)
    service.mrkt.buy.assert_called_once_with(raw)
    service.mrkt.withdraw.assert_called_once_with("owned-gift")
    service.mrkt.transfer.assert_called_once()
    current=db.get("catalog_credit_payment",account["id"])
    assert len(credit.purchases(current))==1 and credit.balance(current)==0 and current["closed_after_delivery"] is True
    credit.delivered(db,db.get("order",oid))
    assert len(credit.purchases(db.get("catalog_credit_payment",account["id"])))==1
    assert db.get("order",oid)["stage"]=="COMPLETE"
    assert {l["type"] for l in db.list("ledger")}=={"purchase","transfer","fees","revenue"}
    client.account.assert_not_called()


def test_minimum_topup_is_90_and_entire_payment_is_consumed_once(db,real):
    service,gift,raw,client=real
    sale=price(db,"4")["sale_price"]
    account,code=credit.create(db,str(sale-29),"buyer")
    token=credit.activate(db,code,"local")
    pending=credit.choose(db,account,gift,sale,"buyer")
    assert pending["topup_rub"]=="90"
    checkout=db.get("catalog_checkout",pending["checkout_id"])
    assert checkout["shown_price"]==sale
    catalog_checkout.create(service,checkout["id"])
    assert client.create.call_args.args[0]["price"]==90
    payment={"id":"payment","status":"PAID","sale_rub":"90","lot_id":"topup-lot","buyer_id":"buyer","chat_id":"chat"}
    client.orders.side_effect=lambda deal_id=None:[payment]
    client.set_deal_status.side_effect=lambda id,status:payment.update(status=status)
    credit.handle_payment(service,payment)
    credit.fulfill(service,account["id"])
    wallet=db.get("catalog_credit_payment",account["id"])
    drive(service,wallet["fulfillment_order_id"])
    closed=db.get("catalog_credit_payment",account["id"])
    assert closed["state"]=="DELETED" and credit.balance(closed)==0 and not credit.valid(closed)
    assert credit.session(db,token)["closed_after_delivery"]
    with pytest.raises(ValueError):credit.activate(db,code,"new-device")
    revenue=next(e for e in db.list("ledger") if e["type"]=="revenue")
    assert credit.D(revenue["amount_rub"])==sale-29+90
    credit.delivered(db,db.get("order",wallet["fulfillment_order_id"]))
    assert len(credit.purchases(db.get("catalog_credit_payment",account["id"])))==1


def test_real_topup_publishes_exact_difference_and_only_real_payment_unblocks_delivery(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"100","buyer")
    sale=price(db,"4")["sale_price"]
    pending=credit.choose(db,account,gift,sale,"buyer")
    assert pending["state"]=="PREPARING"
    catalog_checkout.create(service,pending["checkout_id"])
    client.create.assert_called_once()
    assert client.create.call_args.args[0]["price"]==sale-100
    current=db.get("catalog_credit_payment",account["id"])
    assert current["state"]=="AWAITING_TOPUP"
    assert credit.view(current,db)["payment_url"]=="https://playerok.com/products/pay-extra"
    service.mrkt.buy.assert_not_called()
    order={"id":"deal","status":"PAID","lot_id":"topup-lot","sale_rub":str(sale-100),"buyer_id":"payer","chat_id":"chat"}
    client.orders.side_effect=lambda deal_id=None:[order]
    assert credit.handle_payment(service,order)
    assert credit.handle_payment(service,order)
    current=db.get("catalog_credit_payment",account["id"])
    assert current["paid_rub"]==str(sale) and current["state"]=="EXECUTING"
    credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    def mark_sent(deal_id,status):order["status"]=status
    client.set_deal_status.side_effect=mark_sent
    drive(service,oid)
    assert db.get("catalog_credit_payment",account["id"])["state"]=="DELETED"
    assert credit.balance(db.get("catalog_credit_payment",account["id"]))==0
    client.set_deal_status.assert_called_once_with("deal","SENT")


def test_preflight_loss_releases_reservation_without_spending_or_buying(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    credit.fulfill(service,account["id"])
    current=db.get("catalog_credit_payment",account["id"]);oid=current["fulfillment_order_id"]
    service.mrkt.ton.return_value="40"
    with pytest.raises(Exception,match="убыточен"):service.process_order(oid)
    credit.failed_order(db,oid,"Заказ убыточен")
    current=db.get("catalog_credit_payment",account["id"])
    assert credit.balance(current)==1000 and credit.purchases(current)==[]
    service.mrkt.buy.assert_not_called()


def test_rate_limit_waits_without_releasing_paid_selection(db,real,monkeypatch):
    from backend.app.safety import RateLimited
    from backend.app.db import Task
    service,gift,raw,client=real
    account,_=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    worker=Worker(db)
    monkeypatch.setattr(worker,"dispatch",Mock(side_effect=RateLimited("mrkt: пауза",time.time()+3)))
    monkeypatch.setattr(worker,"claim",lambda:(db.enqueue("process_order",{"order_id":oid},key="test-wait"),"process_order",{"order_id":oid}))
    worker.run_one()
    wallet=db.get("catalog_credit_payment",account["id"])
    assert wallet["state"]=="EXECUTING" and credit.D(wallet["reservation_rub"])>0
    assert db.get("order",oid)["stage"]!="MANUAL"
    assert db.list("catalog_message")==[]


def test_rate_limit_in_purchase_preflight_retries_but_submitted_write_does_not(db,real):
    from backend.app.safety import RateLimited
    service,gift,raw,client=real
    account,_=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    service.process_order(oid)
    service.mrkt.ton.side_effect=RateLimited("wait",time.time()+3)
    service.execute(oid+":buy")
    assert db.get("operation",oid+":buy")["state"]=="QUEUED"
    service.mrkt.buy.assert_not_called()
    service.mrkt.ton.side_effect=None
    service.mrkt.buy.side_effect=RateLimited("write result unknown",time.time()+3)
    service.execute(oid+":buy")
    assert db.get("operation",oid+":buy")["state"]=="UNKNOWN"
    service.execute(oid+":buy")
    service.mrkt.buy.assert_called_once()


def test_owner_can_enable_loss_sales_and_disable_before_spending(db,real):
    service,gift,raw,client=real
    account,_=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    service.mrkt.ton.return_value="40"
    db.configure(allow_loss_sales=True)
    service.process_order(oid)
    op=db.get("operation",oid+":buy")
    assert op["state"]=="QUEUED"
    db.configure(allow_loss_sales=False)
    service.execute(op["id"])
    service.mrkt.buy.assert_not_called()
    assert db.get("operation",op["id"])["state"]=="FAILED"


def test_unknown_buy_keeps_reservation_and_never_repeats_purchase(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"1000","buyer")
    sale=price(db,"4")["sale_price"]
    credit.choose(db,account,gift,sale,"buyer");credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    service.process_order(oid);service.mrkt.buy.side_effect=TimeoutError("unknown result")
    service.execute(oid+":buy");service.execute(oid+":buy")
    assert db.get("operation",oid+":buy")["state"]=="UNKNOWN"
    assert db.get("catalog_credit_payment",account["id"])["state"]=="REVIEW"
    assert credit.funds(db.get("catalog_credit_payment",account["id"]))==1000
    service.mrkt.buy.assert_called_once()


def test_unconfirmed_owner_never_spends_reserved_balance(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer");credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    db.put("order",oid,{"stage":"TRANSFERRED","gift_slug":gift["slug"],"recipient_id":123})
    service.mrkt.owner.return_value=999
    with pytest.raises(Exception,match="не подтверждён"):service.process_order(oid)
    assert credit.purchases(db.get("catalog_credit_payment",account["id"]))==[]


def test_publish_unknown_is_not_recreated_and_owner_can_revoke_code(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"100","buyer")
    pending=credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    client.create.side_effect=TimeoutError()
    catalog_checkout.create(service,pending["checkout_id"]);catalog_checkout.create(service,pending["checkout_id"])
    current=db.get("catalog_credit_payment",account["id"])
    assert current["state"]=="REVIEW" and credit.funds(current)==100
    removed=credit.remove(db,current)
    assert removed["state"]=="DELETED" and removed["paid_rub"]=="100"
    client.create.assert_called_once()


def test_worker_recovers_durable_choice_without_duplicate_local_orders(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    credit.tick(service);credit.fulfill(service,account["id"]);credit.tick(service)
    assert len([o for o in db.list("order") if o.get("catalog_credit_id")==account["id"]])==1


def test_disabled_purchase_cannot_reserve_credit(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"1000","buyer")
    db.configure(purchases_enabled=False)
    with pytest.raises(Exception,match="выключена"):credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    assert credit.balance(db.get("catalog_credit_payment",account["id"]))==1000
    assert db.list("order")==[] and db.list("catalog_checkout")==[]


def test_wrong_topup_buyer_is_not_credited_or_fulfilled(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"100","buyer")
    db.put("catalog_credit_payment",account["id"],{"buyer_id":"expected-buyer"})
    pending=credit.choose(db,db.get("catalog_credit_payment",account["id"]),gift,price(db,"4")["sale_price"],"buyer")
    catalog_checkout.create(service,pending["checkout_id"])
    credit.handle_payment(service,{"id":"foreign","status":"PAID","lot_id":"topup-lot","sale_rub":pending["topup_rub"],"buyer_id":"someone-else"})
    current=db.get("catalog_credit_payment",account["id"])
    assert current["state"]=="REVIEW" and current["paid_rub"]=="100"
    assert not db.get("catalog_credit_receipt","foreign")["accepted"]
    service.mrkt.buy.assert_not_called()


def test_expired_listing_then_late_payment_adds_credit_but_does_not_buy_old_nft(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"100","buyer")
    pending=credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    catalog_checkout.create(service,pending["checkout_id"])
    db.put("catalog_checkout",pending["checkout_id"],{"state":"EXPIRED","expires_at":time.time()-10})
    credit.checkout_result(db,db.get("catalog_checkout",pending["checkout_id"]))
    credit.handle_payment(service,{"id":"late","status":"PAID","lot_id":"topup-lot","sale_rub":pending["topup_rub"],"buyer_id":"payer"})
    current=db.get("catalog_credit_payment",account["id"])
    assert current["state"]=="AWAITING_SELECTION"
    assert credit.balance(current)==price(db,"4")["sale_price"]
    service.mrkt.buy.assert_not_called()


def test_fixed_ton_and_transfer_are_charged_once_with_split_payments(db,real):
    service,gift,raw,client=real
    fees={"configured":True,"fees":[{"type":"percent","value":10,"basis":"sale"},{"type":"percent","value":6,"basis":"remaining"},{"type":"ton","value":"0.3"}]}
    db.put("fees","default",fees)
    account,code=credit.create(db,"100","buyer")
    sale=price(db,"4")["sale_price"]
    pending=credit.choose(db,account,gift,sale,"buyer")
    db.put("catalog_credit_receipt","p",{"credit_id":account["id"],"accepted":True,"amount_rub":str(sale-100),"revision":1,"status":"PAID"})
    result=credit.adjust_snapshot(db,account["id"],{"ton_rub":"100","purchase_rub":"400","transfer_rub":"31.5"},fees)
    assert abs(float(result["actual_profit_rub"])-(sale*.9*.94-30-400-31.5))<1e-8


def test_crash_after_submission_moves_credit_to_review_without_unreserving(db,real):
    from backend.app.operations import recover
    service,gift,raw,client=real
    account,code=credit.create(db,"1000","buyer")
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer");credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    service.process_order(oid)
    db.put("operation",oid+":buy",{"state":"SUBMITTED","submitted_at":time.time()})
    recover(db);credit.tick(service)
    current=db.get("catalog_credit_payment",account["id"])
    assert current["state"]=="REVIEW" and credit.funds(current)==1000
    assert current["reservation_rub"]!="0"
    service.mrkt.buy.assert_not_called()


def test_sent_response_lost_is_reconciled_without_second_transfer_or_sent_write(db,real):
    service,gift,raw,client=real
    account,code=credit.create(db,"100","buyer")
    pending=credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    catalog_checkout.create(service,pending["checkout_id"])
    source={"id":"deal","status":"PAID","lot_id":"topup-lot","sale_rub":pending["topup_rub"],"buyer_id":"payer"}
    credit.handle_payment(service,source);client.orders.side_effect=lambda deal_id=None:[source]
    def lost_response(id,status):source["status"]=status;raise TimeoutError()
    client.set_deal_status.side_effect=lost_response
    credit.fulfill(service,account["id"]);oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    for _ in range(4):
        service.process_order(oid)
        for op in db.list("operation"):
            if op.get("order_id")==oid and op["state"]=="QUEUED":service.execute(op["id"])
    assert db.get("order",oid)["stage"]=="MANUAL"
    assert len(credit.purchases(db.get("catalog_credit_payment",account["id"])))==1
    credit.tick(service)
    assert db.get("order",oid)["stage"]=="COMPLETE"
    service.mrkt.transfer.assert_called_once();client.set_deal_status.assert_called_once()


def test_old_simulated_code_cannot_activate_real_credit(db,real):
    from backend.app import catalog_test_payment as fake
    account,code=fake.create(db,"1000","buyer")
    with pytest.raises(ValueError):credit.activate(db,code,"local")
    assert db.list("catalog_credit_payment")==[]


def test_published_topup_keeps_original_price_after_market_increase(db,real):
    service,gift,raw,client=real
    account,_=credit.create(db,"100","buyer")
    sale=price(db,"4")["sale_price"]
    pending=credit.choose(db,account,gift,sale,"buyer")
    catalog_checkout.create(service,pending["checkout_id"])
    raw["salePrice"]=4050000000
    service.mrkt.ton.return_value="4.05"
    source={"id":"paid-increase","status":"PAID","lot_id":"topup-lot","sale_rub":str(sale-100),"buyer_id":"payer","chat_id":"chat"}
    client.orders.side_effect=lambda deal_id=None:[source]
    client.set_deal_status.side_effect=lambda id,status:source.update(status=status)
    credit.handle_payment(service,source)
    current=db.get("catalog_credit_payment",account["id"])
    assert int(current["selected_price"])==sale
    assert client.create.call_args.args[0]["price"]==sale-100
    credit.fulfill(service,account["id"])
    drive(service,db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"])
    service.mrkt.buy.assert_called_once_with(raw)
    assert len(db.list("catalog_checkout"))==1


def test_missing_exact_gift_releases_credit_and_offers_replacement_or_refund(db,real):
    service,gift,raw,client=real
    db.configure(catalog_public_url="http://127.0.0.1:3180")
    account,_=credit.create(db,"1000","buyer")
    db.put("catalog_credit_receipt","original",{"credit_id":account["id"],"accepted":True,"kind":"ORIGINAL","amount_rub":"1000","buyer_id":"payer","chat_id":"chat"})
    credit.choose(db,account,gift,price(db,"4")["sale_price"],"buyer")
    credit.fulfill(service,account["id"])
    oid=db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"]
    raw["isOnSale"]=False
    with pytest.raises(Exception,match="недоступен"):service.order_quote(db.get("lot",oid))
    credit.failed_order(db,oid,"Выбранный NFT больше недоступен")
    credit.failed_order(db,oid,"Выбранный NFT больше недоступен")
    assert credit.balance(db.get("catalog_credit_payment",account["id"]))==1000
    messages=[m for m in db.list("catalog_message") if m["event"].startswith("unavailable:") and not m.get("after_message_id")]
    assert len(messages)==1
    text=db.cipher.decrypt(messages[0]["encrypted_text"].encode()).decode()
    assert "другой подарок" in text and "вернуть деньги" in text and "!продавец" in text
    service.mrkt.buy.assert_not_called()


def test_paid_promotion_select_deliver_and_close_original_deal_with_buyer_messages(db,real):
    from backend.app import catalog_messages
    service,gift,raw,client=real
    db.configure(catalog_promo_started_at=time.time()-5,orders_baselined=True,catalog_public_url="http://127.0.0.1:3180")
    db.put("pair","ad-pair",{"collection":gift["collection"],"model":gift["model"]})
    db.put("lot","ad",{"pair_id":"ad-pair","delivery_mode":"telegram_nft","lot_type":"PROMOTIONAL"})
    original={"id":"original-paid","status":"PAID","sale_rub":"600","buyer_id":"buyer-id","chat_id":"buyer-chat","recipient":"buyer","created_at":time.time(),"lot_id":"ad"}
    client.orders.side_effect=lambda deal_id=None:[original]
    client.set_deal_status.side_effect=lambda id,status:original.update(status=status)
    assert credit.handle_payment(service,original)
    wallet=db.list("catalog_credit_payment")[0]
    service.mrkt.buy.assert_not_called()
    credit.choose(db,wallet,gift,price(db,"4")["sale_price"],"buyer");credit.fulfill(service,wallet["id"])
    oid=db.get("catalog_credit_payment",wallet["id"])["fulfillment_order_id"]
    drive(service,oid)
    client.set_deal_status.assert_called_once_with("original-paid","SENT")
    assert original["status"]=="SENT"
    assert {m["event"] for m in db.list("catalog_message")}=={"promo_paid","promo_paid:catalog_link","selected:1","delivered:1"}
    client.send_chat_message.return_value={"id":"remote-message"}
    for message in db.list("catalog_message"):catalog_messages.send(service,message["id"])
    assert client.send_chat_message.call_count==1
    assert {m["state"] for m in db.list("catalog_message")}=={"SENT","SKIPPED"}


@pytest.mark.parametrize("shortage",[10,20,21])
def test_small_shortage_is_real_discount_not_fake_revenue(db,real,shortage):
    service,gift,raw,client=real
    sale=price(db,"4")["sale_price"]
    account,_=credit.create(db,str(sale-shortage),"buyer")
    result=credit.choose(db,account,gift,sale,"buyer")
    wallet=db.get("catalog_credit_payment",account["id"])
    if shortage<=20:
        assert result["state"]=="EXECUTING"
        assert credit.D(wallet["selected_price"])==sale-shortage
        credit.fulfill(service,account["id"])
        drive(service,db.get("catalog_credit_payment",account["id"])["fulfillment_order_id"])
        assert credit.balance(db.get("catalog_credit_payment",account["id"]))==0
        revenue=[r for r in db.list("ledger") if r["type"]=="revenue"]
        assert credit.D(revenue[0]["amount_rub"])==sale-shortage
    else:
        assert result["state"]=="PREPARING" and wallet["topup_rub"]=="90"


def test_repeated_promo_payments_reuse_code_and_preserve_balance(db,real):
    service,gift,raw,client=real
    db.configure(catalog_promo_started_at=time.time()-5,orders_baselined=True,catalog_public_url="http://127.0.0.1:3180")
    db.put("pair","ad-pair",{"collection":gift["collection"],"model":gift["model"]})
    db.put("lot","ad",{"pair_id":"ad-pair","delivery_mode":"telegram_nft"})
    first={"id":"one","status":"PAID","sale_rub":"500","buyer_id":"buyer-id","buyer_username":"buyer1","chat_id":"chat","recipient":"buyer","created_at":time.time(),"lot_id":"ad"}
    credit.handle_payment(service,first)
    wallet=db.list("catalog_credit_payment")[0]
    db.put("catalog_credit_payment",wallet["id"],{"purchases":[{"price_rub":"450"}]})
    second={**first,"id":"two","buyer_username":"renamed"}
    credit.handle_payment(service,second);credit.handle_payment(service,second)
    current=db.get("catalog_credit_payment",wallet["id"])
    assert len(db.list("catalog_credit_payment"))==1
    assert current["code_encrypted"]==wallet["code_encrypted"]
    assert credit.balance(current)==550 and current["buyer_username"]=="renamed"
    assert len(db.list("catalog_credit_receipt"))==2
    assert len(db.list("catalog_message"))==4
    db.put("catalog_credit_payment",wallet["id"],{"state":"REVIEW"})
    credit.handle_payment(service,{**first,"id":"three"})
    assert len(db.list("catalog_credit_payment"))==2
