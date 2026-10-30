from __future__ import annotations
import base64
import json
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import requests
from .db import Database, uid
from .pricing import D, calculate, calculate_with_premium, required_profit, net_coefficients, effective_fx, PricingError, PremiumQuoteRequired, FxQuoteRequired
from .integrations.clients import Playerok, Mrkt, Bot, normalized, gate, IntegrationError, ManualRequired
from .integrations.mrkt_availability import gift_block_reason
from .safety import is_nft_lot
from .operations import new_operation

def day_start():
    local=datetime.now(timezone(timedelta(hours=3))).replace(hour=0,minute=0,second=0,microsecond=0)
    return local.timestamp()

class Service:
    def __init__(self, db): self.db=db;self.mrkt=Mrkt(db)

    def notice(self, action, title, detail="", **refs):
        last=self.db.get("notice",action)
        if last and time.time()-last["updated_at"]<300 and last.get("detail")==detail: return
        self.db.put("notice",action,{"detail":self.db.redact(detail)})
        self.db.log(action,title,detail,**refs)
        if self.db.settings()["notifications_enabled"]:
            self.db.put("notification",uid(),{"title":title,"detail":self.db.redact(detail),"state":"PENDING"})

    def connection(self, name, operation):
        self.db.put("connection",name,{"status":"CHECKING","last_error":None})
        try:
            data=operation()
            self.db.put("connection",name,{"status":"CONNECTED","last_success":time.time(),"last_error":None,**(data or {})})
        except Exception as exc:
            error=self.db.redact(exc)
            self.db.put("connection",name,{"status":"ERROR","last_error":error})
            self.notice("connection:"+name,"Ошибка подключения "+name,error)

    def check(self, name="all"):
        from .runtime_config import has_user_session
        if name in {"all","playerok"}:
            def playerok():
                viewer=Playerok(self.db).viewer()
                self.db.configure(playerok_account_id=viewer.get("id") or self.db.settings().get("playerok_account_id"))
                return {"balance":viewer.get("balance")}
            if self.db.secret("playerok_cookies"):self.connection("playerok",playerok)
            else:self.db.put("connection","playerok",{"status":"NOT_CONFIGURED","last_error":None},merge=False)
        if name in {"all","telegram"}:
            if self.db.config.get("TELEGRAM_BOT_TOKEN"):self.connection("telegram",lambda:{"username":Bot(self.db).request("getMe").get("username")})
            else:self.db.put("connection","telegram",{"status":"NOT_CONFIGURED","last_error":None},merge=False)
        if name in {"all","mrkt"}:
            if has_user_session(self.db) and self.db.config.get("MRKT_TELEGRAM_API_ID") and self.db.config.get("MRKT_TELEGRAM_API_HASH"):self.connection("mrkt",self.mrkt.balances)
            else:self.db.put("connection","mrkt",{"status":"NOT_CONFIGURED","last_error":None},merge=False)

    def sync_lots(self):
        client=Playerok(self.db)
        records=client.lots()
        ids=set()
        for position,item in enumerate(records):
            id=str(item["id"]);ids.add(id)
            data=normalized(item)
            if "description" not in item: data.pop("description",None)
            self.db.put("lot",id,{**data,"missing":False,"source_order":position})
        for item in self.db.list("lot"):
            if item["id"] not in ids: self.db.put("lot",item["id"],{"missing":True})
        self.db.put("connection","playerok",{"status":"CONNECTED","last_success":time.time(),"last_error":None})
        self.db.log("lots_synced","Лоты синхронизированы",str(len(records)))
        self.db.enqueue("reprice_preview")

    def refresh_lot(self, id):
        lot=self.db.get("lot",id)
        if not lot or not lot.get("slug"): raise ValueError("Лот не найден")
        data=Playerok(self.db).item(lot["slug"])
        if str(data.get("id"))!=id: raise ManualRequired("ID карточки не совпадает")
        self.db.put("lot",id,normalized(data))

    def refresh_fx(self):
        if self.db.settings().get("ton_rate_mode","auto")=="manual":return
        r=requests.get("https://api.coingecko.com/api/v3/simple/price",params={"ids":"the-open-network","vs_currencies":"rub","include_last_updated_at":"true"},timeout=15)
        r.raise_for_status();p=r.json()["the-open-network"]
        if D(p["rub"])<=0: raise IntegrationError("Некорректный курс TON")
        old=self.db.get("fx","ton-rub")
        self.db.put("fx","ton-rub",{"rate":str(p["rub"]),"source_at":p["last_updated_at"],"fetched_at":time.time(),"source":"CoinGecko"})
        if old and old.get("rate")!=str(p["rub"]):
            self.db.enqueue("reprice",key="reprice")

    def catalog(self):
        result=self.mrkt.request("GET","/gifts/collections")
        if isinstance(result,dict): result=result.get("collections",result.get("gifts",[]))
        if not isinstance(result,list): raise IntegrationError("MRKT вернул неизвестную структуру каталога")
        for item in result:
            if not isinstance(item,dict): continue
            name=item.get("name") or item.get("collectionName")
            if name: self.db.put("catalog",str(item.get("id") or name),{"name":name,"raw":item})
        self.db.log("catalog","Каталог MRKT обновлён",str(len(result)))

    def models(self, collection):
        result=self.mrkt.request("POST","/gifts/models",{"collections":[collection]})
        if not isinstance(result,list):raise IntegrationError("MRKT не вернул список моделей")
        for item in result:
            if item.get("collectionName")!=collection:continue
            name=item.get("modelName")
            if name:
                self.db.put("model",collection+"::"+name,{"collection":collection,"name":name,"raw":item})
        return {"count":len(result)}

    def quote(self, pair_id, force=False):
        pair=self.db.get("pair",pair_id)
        if not pair: raise ValueError("Пара не найдена")
        old=self.db.get("quote",pair_id)
        if (not force and old and old.get("available")
                and gift_block_reason(old.get("gift") or {}) is None
                and time.time()-old.get("fetched_at",0)<self.db.settings()["quote_interval"]): return old
        gifts=self.mrkt.search(pair["collection"],pair["model"])
        if not gifts:
            self.db.put("quote",pair_id,{"available":False,"fetched_at":time.time(),"reason":"Нет предложений точной модели, доступных для немедленной передачи"})
            self.notice("no-model:"+pair_id,"Нет доступных подарков нужной модели",pair["collection"]+" — "+pair["model"])
            return None
        gift=gifts[0]
        result={"available":True,"fetched_at":time.time(),"ton":self.mrkt.ton(gift["salePrice"]),"gift":gift,"offers_in_page":len(gifts)}
        self.db.put("quote",pair_id,result)
        self.db.put("quote_history",uid(),{"pair_id":pair_id,**result})
        if old and old.get("ton")!=result["ton"]: self.notice("quote:"+pair_id,"Изменилась закупочная цена",pair["collection"]+" — "+pair["model"]+": "+result["ton"]+" TON")
        return result

    def quotes(self):
        active={l.get("pair_id") for l in self.db.list("lot") if is_nft_lot(l) and l.get("status")=="APPROVED" and not l.get("missing")}
        for pair_id in active:
            if pair_id:
                try: self.quote(pair_id)
                except Exception as exc:
                    self.db.put("quote",pair_id,{"last_error":self.db.redact(exc)})
                    self.notice("quote-error:"+pair_id,"Ошибка получения цены MRKT",self.db.redact(exc))
        self.db.enqueue("reprice")

    def competitor(self, id):
        record=self.db.get("competitor",id)
        if not record: raise ValueError("Конкурент не найден")
        item=Playerok(self.db).item(record["slug"])
        pair=self.db.get("pair",record.get("pair_id"))
        text=(str(item.get("name",""))+" "+str(item.get("description",""))).casefold()
        matched=bool(pair and pair["collection"].casefold() in text and pair["model"].casefold() in text)
        data={"price":item.get("price"),"status":item.get("status"),"title":item.get("name"),"fetched_at":time.time(),"auto_matched":matched,"last_error":None}
        self.db.put("competitor",id,data)

    def competitors(self):
        for record in self.db.list("competitor"):
            if not record.get("enabled",True): continue
            try: self.competitor(record["id"])
            except Exception as exc: self.db.put("competitor",record["id"],{"last_error":self.db.redact(exc)})

    def premium_quote(self, lot_id, price, allow_network=True):
        price=D(price)
        if price<1 or price!=price.to_integral_value():raise PricingError("Для тарифа премиума нужна целая цена продажи")
        key=str(int(price));record=self.db.get("premium_quotes",lot_id) or {};prices=record.get("prices",{})
        saved=prices.get(key)
        if saved and time.time()-saved.get("checked_at",0)<900:return saved
        if not allow_network:raise PremiumQuoteRequired("Нужна проверка актуального тарифа премиума")
        try:
            tariff=Playerok(self.db).premium(lot_id,int(price))
            if not tariff.get("id") or tariff.get("price") is None or D(tariff["price"])<0:raise ValueError("Missing premium tariff")
            saved={"cost_rub":str(D(tariff["price"])),"price":int(price),"tariff_id":str(tariff["id"]),"checked_at":time.time()}
        except Exception:raise PricingError("Не удалось получить тариф премиума Playerok. Расчёт прибыли без него запрещён.")
        prices={k:v for k,v in prices.items() if time.time()-v.get("checked_at",0)<900}
        prices[key]=saved;prices=dict(sorted(prices.items(),key=lambda row:row[1]["checked_at"],reverse=True)[:32])
        self.db.put("premium_quotes",lot_id,{"prices":prices},merge=False)
        return saved

    def estimate(self, lot_id, sale=None, quote=None, allow_network=True, refresh_premium=False, allow_stale_fx=False):
        lot=self.db.get("lot",lot_id)
        if not is_nft_lot(lot): raise PricingError("Расчёт MRKT доступен только для Telegram NFT с привязкой коллекции и модели")
        settings=self.db.settings()
        quote=quote or self.db.get("quote",lot["pair_id"])
        fx=effective_fx(self.db)
        if not quote or not quote.get("available"): raise PricingError("Нет подходящих предложений MRKT")
        if time.time()-quote.get("fetched_at",0)>settings["quote_max_age"]: raise PricingError("Цена MRKT устарела")
        fx_stale=bool(fx and not fx.get("manual") and time.time()-fx.get("source_at",0)>900)
        if not fx or (fx_stale and not allow_stale_fx): raise FxQuoteRequired("Курс TON устарел")
        if settings["stars_rub"] is None: raise PricingError("Задайте себестоимость Stars")
        profile=self.db.get("fees",lot.get("fee_profile_id","default")) or {}
        strategy={**(self.db.get("strategy") or {}),**(lot.get("strategy") or {})}
        rivals=[]
        for c in self.db.list("competitor"):
            if c.get("lot_id")!=lot_id or c.get("pair_id")!=lot["pair_id"] or not c.get("enabled",True): continue
            if strategy.get("competitor_id") and c["id"]!=strategy["competitor_id"]: continue
            if not (c.get("confirmed") or c.get("auto_matched")) or c.get("status")!="APPROVED" or c.get("last_error"): continue
            if time.time()-c.get("fetched_at",0)>settings["competitor_interval"]*2: continue
            if c.get("price") is not None: rivals.append(c["price"])
        competitor=min(rivals) if rivals and strategy.get("use_competitor",False) else None
        purchase=D(quote["ton"])*D(fx["rate"])
        transfer=D(settings["transfer_stars_reserve"])*D(settings["stars_rub"])
        # A paid order cannot be repriced: listing profit targets/price ceilings must
        # not prevent evaluating its actual margin at the already paid price.
        calculation_strategy={**strategy,"profit_value":0,"max_price":None} if sale is not None else strategy
        result=calculate(purchase,transfer,profile,calculation_strategy,competitor,ton_rub=fx["rate"])
        mode=strategy.get("premium_mode","auto")
        schedule=self.db.get("promotion_schedule",lot_id) or {}
        include=bool(lot.get("premium_planned")) or mode=="include" or (mode=="auto" and (lot.get("premium",False) or schedule.get("enabled",False)))
        count=int(strategy.get("premium_purchases_per_sale",1)) if include else 0
        if include:
            if refresh_premium and allow_network:self.db.put("premium_quotes",lot_id,{"prices":{}},merge=False)
            result=calculate_with_premium(purchase,transfer,profile,calculation_strategy,lambda price:D(self.premium_quote(lot_id,price,allow_network)["cost_rub"])*count,competitor,ton_rub=fx["rate"])
            tariff=self.premium_quote(lot_id,result["sale_price"],allow_network)
            result.update(premium_unit_rub=tariff["cost_rub"],premium_quote_at=tariff["checked_at"],premium_quote_price=tariff["price"])
        result.update(premium_included=include,premium_count=count,premium_planned=bool(lot.get("premium_planned")))
        paid=sale if sale is not None else lot.get("price")
        if paid is not None:
            a,b=net_coefficients(profile.get("fees",[]),purchase,fx["rate"])
            net=a*D(paid)+b
            paid_premium=D(self.premium_quote(lot_id,paid,allow_network)["cost_rub"])*count if include else D(0)
            profit=net-purchase-transfer-paid_premium
            result.update(current_price_rub=str(paid),current_profit_rub=str(profit),current_promotion_rub=str(paid_premium))
            if sale is not None:
                result.update({"paid_sale_rub":str(sale),"actual_net_rub":str(net),"actual_profit_rub":str(profit),"paid_promotion_rub":str(paid_premium),"profitable":profit>=required_profit(purchase,sale,strategy)})
        return {**result,"lot_id":lot_id,"pair_id":lot["pair_id"],"ton_rub":fx["rate"],"quote_ton":quote["ton"],"fx_stale":fx_stale,
                "fee_profile":profile,"strategy":strategy,"quote_at":quote["fetched_at"],"fx_at":fx["source_at"],"estimated_at":time.time()}

    def preview_price(self, lot_id, allow_network=True):
        try:
            result=self.estimate(lot_id) if allow_network else self.estimate(lot_id,allow_network=False)
        except FxQuoteRequired:
            if not allow_network:raise
            warning=""
            try:
                self.refresh_fx()
            except Exception:
                warning="Не удалось обновить курс TON. Расчёт выполнен по последнему сохранённому курсу; он устарел. Для применения цены обновите курс или задайте свой в настройках."
            try:
                result=self.estimate(lot_id)
            except FxQuoteRequired:
                fx=effective_fx(self.db)
                if not fx or D(fx.get("rate",0))<=0:
                    raise PricingError("Нет сохранённого курса TON. Повторите расчёт позже или задайте свой курс в настройках.") from None
                result=self.estimate(lot_id,allow_stale_fx=True)
                result["fx_warning"]=warning or "Источник вернул устаревший курс TON. Показан расчёт по последнему сохранённому курсу. Для применения цены обновите курс или задайте свой в настройках."
        self.db.put("estimate",lot_id,{**result,"status":"READY"},merge=False)
        return new_operation(self.db,"price",{"lot_id":lot_id,"price":result["sale_price"]},expected=result)

    def reprice(self, apply=True):
        settings=self.db.settings()
        for lot in self.db.list("lot"):
            if not is_nft_lot(lot) or lot.get("status")!="APPROVED" or lot.get("missing"): continue
            try:
                estimate=self.estimate(lot["id"])
                self.db.put("estimate",lot["id"],{**estimate,"status":"READY"},merge=False)
                strategy=estimate["strategy"]
                if not apply or not settings["repricing_enabled"] or not strategy.get("enabled",False): continue
                if abs(D(estimate["sale_price"])-D(lot.get("price") or 0))<D(strategy.get("change_threshold",1)): continue
                op=new_operation(self.db,"price",{"lot_id":lot["id"],"price":estimate["sale_price"],"automatic":True},expected=estimate)
                from .operations import submit
                submit(self.db,op["id"])
            except Exception as exc: self.db.put("estimate",lot["id"],{"status":"BLOCKED","error":self.db.redact(exc)},merge=False)

    def preview_promotion(self, lot_id):
        self.refresh_lot(lot_id)
        lot=self.db.get("lot",lot_id)
        if lot.get("status")!="APPROVED": raise ValueError("Поднимать можно только активное объявление")
        client=Playerok(self.db)
        status=client.premium(lot_id,lot["price"])
        if status.get("price") is None or D(status["price"])<=0: raise ValueError("Нет достоверной стоимости премиума")
        viewer=client.viewer();balance=(viewer.get("balance") or {}).get("available")
        expected={"status_id":status["id"],"cost_rub":str(status["price"]),"lot_price":lot["price"],"balance":balance}
        return new_operation(self.db,"promotion",{"lot_id":lot_id},expected=expected)

    def daily_spent(self, kind, exclude_operation=None):
        spent=sum((D(e["amount_rub"]) for e in self.db.list("ledger",100000) if e.get("type")==kind and e["updated_at"]>=day_start()),D(0))
        action="promotion" if kind=="promotion" else "order.buy" if kind=="purchase" else None
        if action:
            for op in self.db.list("operation",100000):
                if op["id"]==exclude_operation or op.get("action")!=action or op.get("state") not in {"SUBMITTED","UNKNOWN"}:continue
                if not self.db.get("ledger",op["id"]+":"+kind):
                    spent+=D(op.get("expected",{}).get("cost_rub" if kind=="promotion" else "purchase_rub",0))
        return spent

    def execute(self, op_id):
        op=self.db.get("operation",op_id)
        if not op or op.get("state")!="QUEUED": return
        action,payload,expected=op["action"],op["payload"],op.get("expected",{})
        try:
            gate(self.db)
            if not action.startswith("order.") and op.get("expires",0)<time.time():raise ValueError("Предварительный расчёт истёк до исполнения")
            client=None
            if action in {"price","promotion","update","create"}: client=Playerok(self.db)
            if action=="price":
                if payload.get("automatic"): gate(self.db,"repricing")
                current=self.estimate(payload["lot_id"],refresh_premium=True)
                if current["sale_price"]!=payload["price"] or current["quote_at"]!=expected.get("quote_at") or current["fx_at"]!=expected.get("fx_at") or D(current.get("promotion_rub",0))!=D(expected.get("promotion_rub",0)):
                    raise ValueError("Расчёт изменился; получите новый предварительный расчёт")
            elif action=="promotion":
                gate(self.db,"promotions")
                self.refresh_lot(payload["lot_id"])
                lot=self.db.get("lot",payload["lot_id"])
                if lot.get("status")!="APPROVED" or lot.get("price")!=expected.get("lot_price"): raise ValueError("Лот изменился; получите новый расчёт")
                tariff=client.premium(lot["id"],lot["price"])
                if tariff["id"]!=expected["status_id"] or D(tariff["price"])!=D(expected["cost_rub"]): raise ValueError("Стоимость премиума изменилась")
                limit=D(self.db.settings()["daily_promotion_limit_rub"])
                if limit!=0 and self.daily_spent("promotion",op_id)+D(expected["cost_rub"])>limit: raise ValueError("Дневной бюджет премиума исчерпан с учётом неопределённых списаний")
                if any(o["id"]!=op_id and o.get("action")=="promotion" and o.get("state") in {"SUBMITTED","UNKNOWN"} and o.get("payload",{}).get("lot_id")==payload["lot_id"] for o in self.db.list("operation",100000)):
                    raise ManualRequired("Результат предыдущего поднятия этого лота ещё не проверен")
                schedule=self.db.get("promotion_schedule",lot["id"])
                if payload.get("automatic") and (not schedule or not schedule.get("enabled")): raise ValueError("Расписание отключено")
                if schedule and D(schedule.get("max_cost_rub",0))!=0 and D(expected["cost_rub"])>D(schedule["max_cost_rub"]): raise ValueError("Премиум превышает лимит одной покупки")
                balance=(client.viewer().get("balance") or {}).get("available")
                if balance is None or D(balance)<D(expected["cost_rub"]): raise ValueError("Недостаточно средств Playerok")
            elif action.startswith("order."):
                self.order_preflight(op)
            elif action=="update":
                self.refresh_lot(payload["lot_id"])
                if self.db.get("lot",payload["lot_id"]).get("status")!="APPROVED":raise ValueError("Объявление больше не активно")
            gate(self.db)
            if action=="order.transfer" and self.db.settings().get("delivery_transport")=="chrome_extension":
                from .browser_delivery import enqueue
                return enqueue(self.db,op)
            self.db.put("operation",op_id,{"state":"SUBMITTED","submitted_at":time.time()})
            if action=="price":
                result=client.update(payload["lot_id"],{"price":payload["price"]})
                self.db.put("lot",payload["lot_id"],normalized(result))
            elif action=="promotion":
                result=client.promote(payload["lot_id"],expected["status_id"])
                self.db.put("lot",payload["lot_id"],normalized(result))
                self.ledger(op_id,"promotion",expected["cost_rub"],payload["lot_id"])
                self.db.patch_existing("promotion_schedule",payload["lot_id"],{"last_at":time.time()})
            elif action=="update":
                result=client.update(payload["lot_id"],payload["changes"])
                self.db.put("lot",payload["lot_id"],normalized(result))
            elif action=="create":
                result=client.create(payload["fields"],base64.b64decode(payload["image"]))
                fields=payload["fields"]
                self.db.put("lot",result["item_id"],{"title":result.get("name") or fields["title"],"slug":result["slug"],
                    "price":result.get("price") or fields["price"],"description":fields.get("description",""),"status":result.get("status") or "PENDING_APPROVAL",
                    "pair_id":fields["pair_id"],"delivery_mode":"telegram_nft","fee_profile_id":fields.get("fee_profile_id","default"),"synced_at":time.time(),"url":"https://playerok.com/products/"+result["slug"]})
            elif action.startswith("order."): result=self.execute_order_operation(op)
            else: raise ValueError("Неизвестная операция")
            self.db.put("operation",op_id,{"state":"COMPLETE","completed_at":time.time(),"result":result})
            if action in {"price","promotion","update"}:self.db.enqueue("reprice_preview")
            self.notice("operation:"+action,"Операция выполнена",action,operation_id=op_id,lot_id=payload.get("lot_id"))
            if op.get("order_id"): self.db.enqueue("process_order",{"order_id":op["order_id"]},key="order:"+op["order_id"])
        except Exception as exc:
            submitted=(self.db.get("operation",op_id) or {}).get("state")=="SUBMITTED"
            self.db.put("operation",op_id,{"state":"UNKNOWN" if submitted else "FAILED","error":self.db.redact(exc)})
            if op.get("order_id"): self.db.put("order",op["order_id"],{"stage":"MANUAL","reason":self.db.redact(exc)})
            self.notice("operation-error:"+op_id,"Операция требует проверки",self.db.redact(exc),operation_id=op_id)

    def ledger(self, op_id, kind, amount, lot_id=None, order_id=None, **extra):
        if self.db.get("ledger",op_id+":"+kind):return
        pair_id=(self.db.get("lot",lot_id) or {}).get("pair_id") if lot_id else None
        pair=self.db.get("pair",pair_id) if pair_id else None
        self.db.put("ledger",op_id+":"+kind,{"operation_id":op_id,"type":kind,"amount_rub":str(amount),"lot_id":lot_id,"order_id":order_id,
            "pair_id":pair_id,"collection":pair.get("collection") if pair else None,"model":pair.get("model") if pair else None,**extra},merge=False)

    def orders(self):
        baseline=self.db.settings()["orders_baselined"]
        for order in Playerok(self.db).orders():
            old=self.db.get("order",order["id"])
            if old:
                metadata={"playerok_status":order["status"]}
                if not old.get("created_at") and order.get("created_at"):metadata["created_at"]=order["created_at"]
                self.db.put("order",order["id"],metadata)
                # Repair only a system classification made before a sold-item
                # snapshot could be linked to the original listing. Never resume
                # human holds or orders that have already started spending.
                if (old.get("stage")=="MANUAL" and not old.get("manual_hold")
                        and not old.get("cancel_requested") and old.get("source")!="legacy"
                        and old.get("reason")=="Обычный товар: закупка MRKT и автоматическая выдача не применяются"
                        and not old.get("gift_slug") and not old.get("mrkt_gift_id")
                        and order["status"]=="PAID" and is_nft_lot(self.db.get("lot",order.get("lot_id")))
                        and not self.db.get("processed",order["id"])
                        and not any(self.db.get("operation",order["id"]+":"+action) for action in ("buy","withdraw","transfer","mark_sent"))):
                    self.db.put("order",order["id"],{**order,"stage":"DISCOVERED","reason":"Привязка исходного лота восстановлена по точному slug"})
                    self.db.enqueue("process_order",{"order_id":order["id"]},key="order:"+order["id"])
                    continue
                if not is_nft_lot(self.db.get("lot",old.get("lot_id"))):
                    if old.get("stage") not in {"COMPLETE","HISTORICAL","CANCELLED","MANUAL"}:
                        self.db.put("order",order["id"],{"stage":"MANUAL","reason":"Обычный товар: закупка MRKT и автоматическая выдача не применяются"})
                    continue
                if old.get("source")!="legacy" and old.get("stage") in {"DISCOVERED","AWAITING_ENABLE","WAITING_DATA","CHECK_PRICE","PURCHASED","WITHDRAWN","TRANSFERRED"}:
                    if old["stage"] in {"DISCOVERED","AWAITING_ENABLE","WAITING_DATA","CHECK_PRICE"}:
                        self.db.put("order",order["id"],{"sale_rub":order["sale_rub"],"recipient":order.get("recipient") or old.get("recipient"),
                            "stage":"DISCOVERED" if old["stage"]=="WAITING_DATA" and order.get("recipient") else old["stage"]})
                    self.db.enqueue("process_order",{"order_id":order["id"]},key="order:"+order["id"])
                continue
            processed=self.db.get("processed",order["id"])
            historical=bool(processed) or not baseline
            data={**order,"playerok_status":order["status"],"stage":"HISTORICAL" if historical else "DISCOVERED","discovered_at":time.time()}
            if order["status"]!="PAID": data["stage"]="HISTORICAL"
            if data["stage"]=="DISCOVERED" and not is_nft_lot(self.db.get("lot",order.get("lot_id"))):
                data.update(stage="MANUAL",reason="Обычный товар: закупка MRKT и автоматическая выдача не применяются")
            self.db.put("order",order["id"],data)
            if not historical and order["status"]=="PAID":
                from .buyer_messages import queue
                queue(self.db,self.db.get("order",order["id"]),"paid")
            if data["stage"]=="DISCOVERED":
                self.notice("order:"+order["id"],"Новый оплаченный заказ",order["title"])
                self.db.enqueue("process_order",{"order_id":order["id"]},key="order:"+order["id"])
        self.db.configure(orders_baselined=True)
        self.db.log("orders_synced","Заказы Playerok синхронизированы")

    def process_order(self, id):
        order=self.db.get("order",id)
        if not order or order.get("stage") in {"COMPLETE","MANUAL","HISTORICAL","CANCELLED"}: return
        if order.get("manual_hold") or order.get("cancel_requested"):
            self.db.put("order",id,{"stage":"MANUAL","reason":"Исполнение остановлено владельцем; проверьте результат уже отправленной операции"});return
        if not is_nft_lot(self.db.get("lot",order.get("lot_id"))):
            self.db.put("order",id,{"stage":"MANUAL","reason":"Обычный товар: закупка MRKT и автоматическая выдача не применяются"});return
        settings=self.db.settings()
        if not settings["purchases_enabled"] or not settings["transfers_enabled"] or not settings["delivery_tested"]:
            self.db.put("order",id,{"stage":"AWAITING_ENABLE","reason":"Реальная закупка и выдача выключены"});return
        gate(self.db)
        stage=order["stage"]
        if stage in {"DISCOVERED","AWAITING_ENABLE","WAITING_DATA","CHECK_PRICE"}:
            if settings.get("delivery_transport")=="chrome_extension":
                from .browser_delivery import status
                if not status(self.db)["ready"]:
                    self.db.put("order",id,{"stage":"AWAITING_ENABLE","reason":"Ожидание Chrome: откройте Chrome и войдите в Telegram Web A"})
                    return
            fresh=next((o for o in Playerok(self.db).orders() if o["id"]==id),None)
            if not fresh or fresh["status"]!="PAID": raise ManualRequired("Оплата не подтверждена свежими данными Playerok")
            lot=self.db.get("lot",order.get("lot_id"))
            if not lot or not lot.get("pair_id"): raise ManualRequired("Лот не связан с моделью")
            recipient=fresh.get("recipient") or order.get("recipient")
            if not recipient: self.db.put("order",id,{"stage":"WAITING_DATA","reason":"Не указан Telegram получатель"});return
            async def resolve(c):
                p=await c.get_entity(recipient)
                if not isinstance(p, __import__('telethon').types.User) or getattr(p,"bot",False) or getattr(p,"deleted",False): raise ManualRequired("Получатель не является активным пользователем")
                return p.id
            recipient_id=self.mrkt.telegram(resolve)
            self.db.put("order",id,{"stage":"CHECK_PRICE","recipient":recipient,"recipient_id":recipient_id,"sale_rub":fresh["sale_rub"]})
            quote=self.quote(lot["pair_id"],force=True)
            estimate=self.estimate(lot["id"],sale=fresh["sale_rub"],quote=quote)
            self.db.put("order",id,{"snapshot":estimate})
            if D(estimate["actual_profit_rub"])<0:
                raise ManualRequired("Заказ убыточен: ожидаемая прибыль "+str(D(estimate["actual_profit_rub"]))+" ₽. Закупка остановлена")
            gift=quote["gift"]
            slug=gift.get("slug") or gift.get("giftSlug") or gift.get("name")
            if not slug or not re.fullmatch(r"[A-Za-z0-9]+-\d+",slug): raise ManualRequired("MRKT не вернул уникальный slug; закупка остановлена")
            op=self.make_order_operation(id,"buy",{"gift":gift,"slug":slug},estimate)
            self.db.put("order",id,{"stage":"PURCHASING","snapshot":estimate,"gift_slug":slug})
            return op
        elif stage=="PURCHASED":
            self.make_order_operation(id,"withdraw",{"gift_id":order["mrkt_gift_id"],"slug":order["gift_slug"]},{})
        elif stage=="WITHDRAWN":
            saved=self.mrkt.saved(order["gift_slug"])
            if not saved:
                if time.time()-order.get("withdraw_at",0)>300: raise ManualRequired("NFT не появился в Telegram; повторный вывод запрещён")
                self.db.enqueue("process_order",{"order_id":id},key="order-wait:"+id+":"+str(int(time.time()//15)+1),due=time.time()+15);return
            if saved["can_transfer_at"]>time.time(): raise ManualRequired("Telegram ограничил время передачи NFT")
            self.make_order_operation(id,"transfer",{"saved":saved,"recipient":order["recipient"],"recipient_id":order["recipient_id"]},order["snapshot"])
        elif stage=="TRANSFERRED":
            if not self.delivery_confirmed(order): raise ManualRequired("Владелец NFT не подтверждён; повторная передача запрещена")
            from .buyer_messages import queue
            queue(self.db,order,"delivered")
            self.make_order_operation(id,"mark_sent",{},order["snapshot"])

    def delivery_confirmed(self, order):
        owner=self.mrkt.owner(order["gift_slug"])
        if owner==order["recipient_id"]:return True
        if owner is not None:return False
        transfer=self.db.get("operation",order["id"]+":transfer") or {}
        if not transfer.get("submitted_at"):return False
        saved=transfer.get("payload",{}).get("saved",{})
        receipt=self.mrkt.transfer_receipt(order["gift_slug"],order["recipient"],order["recipient_id"],
                                          transfer["submitted_at"],saved.get("msg_id",0))
        if not receipt:return False
        self.db.put("order",order["id"],{"transfer_verification":"telegram_service_message","transfer_receipt":receipt})
        return True

    def make_order_operation(self, order_id, action, payload, expected):
        op_id=order_id+":"+action
        existing=self.db.get("operation",op_id)
        if existing:
            # Readiness is checked before SUBMITTED. This specific failure never
            # reached a purchase request and may be rebuilt from a fresh quote.
            if (existing["state"]=="RETRY_PENDING" or
                    (action=="buy" and existing["state"]=="FAILED" and not existing.get("submitted_at")
                     and existing.get("error")=="Расширение Chrome не готово: откройте Chrome и войдите в Telegram Web A")):
                op=self.db.put("operation",op_id,{"state":"QUEUED","payload":payload,"expected":expected,
                    "previous_preflight_error":existing.get("previous_preflight_error") or existing.get("error"),"error":None})
                self.db.enqueue("execute",{"operation_id":op_id},key="execute:"+op_id)
                return op
            if existing["state"] in {"UNKNOWN","FAILED"}: raise ManualRequired("Предыдущая операция требует сверки")
            return existing
        op=self.db.put("operation",op_id,{"order_id":order_id,"action":"order."+action,"state":"QUEUED","payload":payload,"expected":expected})
        self.db.enqueue("execute",{"operation_id":op_id},key="execute:"+op_id)
        return op

    def order_preflight(self, op):
        order=self.db.get("order",op["order_id"])
        s=self.db.settings()
        if not order or not is_nft_lot(self.db.get("lot",order.get("lot_id"))):
            raise ManualRequired("Обычный товар: закупка MRKT и автоматическая выдача запрещены")
        if not s["delivery_tested"]: raise ManualRequired("Сначала подтвердите отдельное тестирование выдачи")
        if not order or order.get("manual_hold") or order.get("cancel_requested") or order.get("stage") in {"MANUAL","CANCELLED","COMPLETE"}: raise ManualRequired("Заказ остановлен")
        if op["action"]=="order.buy":
            gate(self.db,"purchases")
            if s.get("delivery_transport")=="kurigram":
                from .integrations.kurigram_delivery import require_kurigram
                require_kurigram()
            if s.get("delivery_transport")=="chrome_extension":
                from .browser_delivery import require_ready
                require_ready(self.db)
            fresh=next((o for o in Playerok(self.db).orders() if o["id"]==order["id"]),None)
            if not fresh or fresh["status"]!="PAID": raise ManualRequired("Оплата больше не подтверждена")
            lot=self.db.get("lot",order["lot_id"])
            quote=self.quote(lot["pair_id"],force=True)
            if not quote or not quote.get("gift"):raise ManualRequired("Нет подходящих предложений MRKT")
            snapshot=self.estimate(order["lot_id"],sale=fresh["sale_rub"],quote=quote,refresh_premium=True)
            self.db.put("order",order["id"],{"snapshot":snapshot,"sale_rub":fresh["sale_rub"]})
            if D(snapshot["actual_profit_rub"])<0:
                raise ManualRequired("Заказ убыточен: ожидаемая прибыль "+str(D(snapshot["actual_profit_rub"]))+" ₽. Закупка остановлена")
            cost=D(snapshot["purchase_rub"])
            per_order_limit,daily_limit=D(s["purchase_limit_rub"]),D(s["daily_purchase_limit_rub"])
            if ((per_order_limit!=0 and cost>per_order_limit)
                    or (daily_limit!=0 and self.daily_spent("purchase")+cost>daily_limit)):
                raise ManualRequired("Лимит закупки превышен")
            balance=self.mrkt.balances()
            if D(balance["mrkt_ton"])<D(snapshot["quote_ton"]): raise ManualRequired("Недостаточно TON на MRKT")
            if balance["stars"]<s["transfer_stars_reserve"]: raise ManualRequired("Недостаточно Stars")
            gift=quote["gift"]
            slug=gift.get("slug") or gift.get("giftSlug") or gift.get("name")
            if not slug or not re.fullmatch(r"[A-Za-z0-9]+-\d+",slug):raise ManualRequired("MRKT не вернул уникальный slug; закупка остановлена")
            # The worker has not submitted a purchase yet: use the freshly checked offer and costs.
            op["payload"]={**op["payload"],"gift":gift,"slug":slug}
            op["expected"]=snapshot
            if op.get("id"):self.db.put("operation",op["id"],{"payload":op["payload"],"expected":snapshot})
            self.db.put("order",order["id"],{"gift_slug":slug})
        elif op["action"]=="order.transfer":
            gate(self.db,"transfers")
            saved=self.mrkt.saved(order["gift_slug"])
            if not saved or saved["msg_id"]!=op["payload"]["saved"]["msg_id"]: raise ManualRequired("Конкретный NFT не найден")
            reserve=D(op["expected"]["transfer_rub"])
            if D(saved["transfer_stars"])*D(s["stars_rub"])>reserve: raise ManualRequired("Передача дороже резерва в расчёте")
            if saved["can_transfer_at"]>time.time(): raise ManualRequired("Передача пока недоступна")
        elif op["action"]=="order.mark_sent":
            # Confirmation is a separate guarded step, including after restart.
            if not self.delivery_confirmed(order):raise ManualRequired("Владелец конкретного NFT не подтверждён")

    def execute_order_operation(self, op):
        order=self.db.get("order",op["order_id"]);p=op["payload"];action=op["action"]
        if not order or not is_nft_lot(self.db.get("lot",order.get("lot_id"))):
            raise ManualRequired("Обычный товар: закупка MRKT и автоматическая выдача запрещены")
        if action=="order.buy":
            bought=self.mrkt.buy(p["gift"])
            self.db.put("order",order["id"],{"stage":"PURCHASED","mrkt_gift_id":str(bought["id"])})
            self.ledger(op["id"],"purchase",op["expected"]["purchase_rub"],order["lot_id"],order["id"],snapshot=op["expected"])
            return {"gift_id":str(bought["id"])}
        if action=="order.withdraw":
            self.mrkt.withdraw(p["gift_id"])
            self.db.put("order",order["id"],{"stage":"WITHDRAWN","withdraw_at":time.time()});return {"requested":True}
        if action=="order.transfer":
            recipient_id=self.mrkt.transfer(p["saved"],p["recipient"],p["recipient_id"])
            if recipient_id!=p["recipient_id"]: raise ManualRequired("Изменился ID Telegram получателя")
            self.db.put("order",order["id"],{"stage":"TRANSFERRED"})
            self.ledger(op["id"],"transfer",D(p["saved"]["transfer_stars"])*D(self.db.settings()["stars_rub"]),order["lot_id"],order["id"])
            return {"recipient_id":recipient_id}
        if action=="order.mark_sent":
            from playerokapi.enums import ItemDealStatuses
            account=Playerok(self.db).account()
            gate(self.db,"transfers")
            account.update_deal(order["id"],ItemDealStatuses.SENT)
            self.db.put("order",order["id"],{"stage":"COMPLETE","completed_at":time.time()})
            self.db.put("processed",order["id"],{"source":"playerok2"})
            self.ledger(op["id"],"revenue",order["sale_rub"],order["lot_id"],order["id"])
            self.ledger(op["id"],"fees",D(order["sale_rub"])-D(order["snapshot"]["actual_net_rub"]),order["lot_id"],order["id"])
            return {"sent":True}
        raise ValueError("Неизвестный этап выдачи")

    def reconcile(self, op_id):
        op=self.db.get("operation",op_id)
        if not op or op.get("state")!="UNKNOWN": raise ValueError("Нет неопределённой операции")
        browser_job=self.db.get("browser_job",op_id)
        if browser_job:
            from .browser_delivery import tick
            self.db.put("browser_job",op_id,{"state":"VERIFYING","submitted_at":time.time()-121})
            tick(self)
            return
        if op["action"]=="order.buy":
            owned=self.mrkt.owned_gift(op["payload"]["gift"])
            if owned:
                order=self.db.get("order",op["order_id"])
                self.ledger(op_id,"purchase",op["expected"]["purchase_rub"],order["lot_id"],order["id"],snapshot=op["expected"])
                stopped=order.get("manual_hold") or order.get("cancel_requested")
                self.db.put("order",order["id"],{"stage":"MANUAL" if stopped else "PURCHASED",
                    "mrkt_gift_id":str(owned["id"]),"reason":"Покупка подтверждена по инвентарю MRKT"})
                self.db.put("operation",op_id,{"state":"COMPLETE","reconciled":True,"verification":"mrkt_inventory",
                    "result":{"gift_id":str(owned["id"])},"error":None})
                if not stopped:self.db.enqueue("process_order",{"order_id":order["id"]},key="order:"+order["id"])
                return
        if op["action"]=="order.transfer":
            order=self.db.get("order",op["order_id"])
            if self.delivery_confirmed(order):
                self.db.put("operation",op_id,{"state":"COMPLETE","reconciled":True})
                self.db.put("order",order["id"],{"stage":"TRANSFERRED"})
                return
        self.notice("reconcile:"+op_id,"Автоматическая сверка не подтвердила результат","Проверьте историю площадки вручную; операция не повторена",operation_id=op_id)

    def resolve_operation(self, op_id, resolution, evidence):
        op=self.db.get("operation",op_id)
        if not op or op.get("state")!="UNKNOWN":raise ValueError("Операция не требует ручной сверки")
        if len(evidence.strip())<10:raise ValueError("Опишите результат проверки истории площадки")
        if resolution=="CONFIRMED":
            expected=op.get("expected",{});payload=op.get("payload",{})
            if op["action"]=="promotion":self.ledger(op_id,"promotion",expected["cost_rub"],payload.get("lot_id"))
            elif op["action"]=="order.buy":
                order=self.db.get("order",op.get("order_id")) or {}
                self.ledger(op_id,"purchase",expected["purchase_rub"],order.get("lot_id"),op.get("order_id"),snapshot=expected)
            elif op["action"]!="price" and op["action"]!="update":raise ManualRequired("Этот этап требует проверки конкретного NFT; используйте автоматическую сверку или ручную обработку заказа")
        elif resolution!="NOT_EXECUTED":raise ValueError("Неизвестный результат проверки")
        self.db.put("operation",op_id,{"state":"COMPLETE" if resolution=="CONFIRMED" else "NOT_EXECUTED","manual_resolution":True,"evidence":evidence})
        self.db.log("manual_resolution","Результат операции подтверждён владельцем",evidence,operation_id=op_id)
        return {"ok":True}

    def schedules(self):
        s=self.db.settings()
        if s["emergency_stop"] or not s["promotions_enabled"]: return
        for schedule in self.db.list("promotion_schedule"):
            if not schedule.get("enabled") or schedule.get("next_at",0)>time.time(): continue
            try:
                op=self.preview_promotion(schedule["id"])
                self.db.put("operation",op["id"],{"payload":{"lot_id":schedule["id"],"automatic":True}})
                from .operations import submit
                submit(self.db,op["id"])
            finally: self.db.patch_existing("promotion_schedule",schedule["id"],{"next_at":time.time()+schedule.get("interval",3600)})

    def bot_poll(self):
        settings=self.db.settings()
        if not settings["notifications_enabled"]: return
        bot=Bot(self.db)
        updates=bot.request("getUpdates",{"offset":settings.get("bot_offset"),"timeout":0,"allowed_updates":["message"]})
        owner=self.db.config.get("OWNER_TELEGRAM_USERNAME").lstrip("@").lower()
        for update in updates:
            message=update.get("message") or {};sender=message.get("from") or {};chat=message.get("chat") or {}
            command=str(message.get("text","")).split("@",1)[0].strip()
            allowed=(sender.get("id")==settings.get("owner_chat_id")) if settings.get("owner_chat_id") else bool(owner) and sender.get("username","").lower()==owner
            if chat.get("type")=="private" and allowed and command=="/start":
                self.db.configure(owner_chat_id=chat["id"])
                bot.request("sendMessage",{"chat_id":chat["id"],"text":"Playerok Manager 2: уведомления подключены. Управление — в локальной панели."})
            elif chat.get("type")=="private" and allowed and command=="/stop":
                self.db.configure(emergency_stop=True)
                bot.request("sendMessage",{"chat_id":chat["id"],"text":"Новые торговые действия остановлены. Уже отправленные операции требуют сверки."})
            elif chat.get("type")=="private" and allowed and command=="/status":
                s=self.db.settings()
                bot.request("sendMessage",{"chat_id":chat["id"],"text":f"Аварийная остановка: {'да' if s['emergency_stop'] else 'нет'}. Подробности — в панели."})
            self.db.configure(bot_offset=update["update_id"]+1)
        pending=[n for n in self.db.list("notification",1000) if n.get("state")=="PENDING"]
        if pending and self.db.settings().get("owner_chat_id"):
            batch=pending[:15]
            text="\n\n".join(n["title"]+"\n"+n.get("detail","")[:180] for n in batch)[:3900]
            bot.request("sendMessage",{"chat_id":self.db.settings()["owner_chat_id"],"text":text})
            for n in batch:self.db.put("notification",n["id"],{"state":"SENT"})

    def backup(self):
        folder=self.db.config.data/"backups";folder.mkdir(exist_ok=True)
        path=folder/(datetime.now(timezone.utc).strftime("%Y-%m-%d")+".sqlite3")
        with sqlite3.connect(self.db.config.db_path) as src,sqlite3.connect(path) as dst:src.backup(dst)
        for old in sorted(folder.glob("*.sqlite3"))[:-7]: old.unlink()
        self.db.log("backup","Резервная копия создана",path.name)
