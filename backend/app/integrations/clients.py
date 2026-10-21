from __future__ import annotations
import asyncio
import time
from dataclasses import asdict
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit, unquote, urlparse
import requests
from curl_cffi import requests as curl_requests
from telethon import TelegramClient, functions, types, connection
from telethon.sessions import StringSession
from .legacy_playerok import PlayerokListingClient, PlayerokListingInput, PLAYEROK_ACTIVE_PROFILE_ITEM_STATUSES, PLAYEROK_COMPLETED_PROFILE_ITEM_STATUSES
from ..db import Database
from ..pricing import D
from ..safety import IntegrationError,ManualRequired,WriteBlocked,gate

class GuardSession(requests.Session):
    def __init__(self, db): super().__init__(); self.db = db
    def request(self, method, url, **kwargs):
        if method.upper() not in {"GET", "HEAD"}:
            gate(self.db)
        # This adapter is only used by the single network worker.
        delay = (self.db.get("throttle", "playerok") or {}).get("next", 0)-time.time()
        if delay > 2: raise IntegrationError(f"Playerok: пауза по ограничению запросов ещё {delay:.0f} секунд")
        if delay > 0: time.sleep(delay)
        self.db.put("throttle", "playerok", {"next": time.time()+0.5})
        if method.upper() not in {"GET","HEAD"}:gate(self.db)
        response = super().request(method, url, **kwargs)
        if response.status_code == 429:
            try: delay = max(1, min(3600, float(response.headers.get("Retry-After", 60))))
            except ValueError: delay = 60
            self.db.put("throttle", "playerok", {"next": time.time()+delay})
            raise IntegrationError(f"Playerok ограничил частоту запросов; повтор через {delay:g} секунд")
        return response

def normalized(item):
    price, raw = item.get("price"), item.get("rawPrice")
    return {"title": item.get("name", "Без названия"), "slug": item.get("slug"),
            "description": item.get("description", ""), "status": item.get("status", "UNKNOWN"),
            "price": price, "raw_price": raw, "premium": item.get("priority") == "PREMIUM",
            "premium_price": item.get("priorityPrice"), "image": next((a.get("url") for a in item.get("attachments", []) or [] if a.get("url")), None),
            "category": (item.get("attributes") or {}).get("type"), "synced_at": time.time(),
            "url": "https://playerok.com/products/"+str(item.get("slug", ""))}

class Playerok:
    def __init__(self, db):
        self.db, self.config = db, db.config
        cookies = db.secret("playerok_cookies")
        if not cookies: raise IntegrationError("Не сохранены cookies Playerok")
        self.cookies = cookies
        self.client = PlayerokListingClient(self.config, cookies, session=GuardSession(db))

    def viewer(self): return self.client.get_viewer_local_balance()
    def item(self, slug): return self.client.get_item(slug)
    def lots(self):
        account = self.db.settings().get("playerok_account_id")
        if not account:
            account = self.viewer().get("id")
            if not account: raise IntegrationError("Не получен ID продавца")
            self.db.configure(playerok_account_id=account)
        result = {}
        for statuses, completed in [(PLAYEROK_ACTIVE_PROFILE_ITEM_STATUSES, False), (PLAYEROK_COMPLETED_PROFILE_ITEM_STATUSES, True)]:
            for item in self.client.list_profile_items(account, statuses, completed=completed): result[item["id"]] = item
        return list(result.values())

    def premium(self, item_id, price): return self.client.get_priority_status(item_id, int(price), premium=True)
    def promote(self, item_id, status_id):
        gate(self.db, "promotions")
        query = """mutation increaseItemPriorityStatus($input: PublishItemInput!) {
          increaseItemPriorityStatus(input: $input) { id slug name price rawPrice status priority priorityPrice }
        }"""
        payload = {"operationName": "increaseItemPriorityStatus", "variables": {"input": {
            "itemId": item_id, "priorityStatuses": [status_id], "transactionProviderId": "LOCAL",
            "transactionProviderData": {"paymentMethodId": None}, "keepInSale": True}}, "query": query}
        result = self.client._graphql_post_json(payload, "increaseItemPriorityStatus", "/products/[slug]").get("increaseItemPriorityStatus")
        if not result or result.get("id") != item_id or result.get("priority") != "PREMIUM": raise ManualRequired("Площадка не подтвердила покупку премиума")
        return result

    def update(self, id, data):
        gate(self.db)
        return self.client.update_item(id, **{k:v for k,v in data.items() if k in {"price","name","description"}})

    def create(self, data, image):
        gate(self.db)
        listing = PlayerokListingInput(title=data["title"], collection=data["collection"], price=int(data["price"]),
            description=data.get("description", ""), image_bytes=image, image_filename=data.get("filename", "gift.png"), premium=False)
        return asdict(self.client.create_and_publish(listing))

    def account(self):
        # Upstream's wheel omits its bundled CA file. Restore it in this
        # project's environment only, using the installed certifi trust store.
        import importlib.util, shutil, certifi
        from pathlib import Path
        spec=importlib.util.find_spec("playerokapi")
        if spec and spec.submodule_search_locations:
            cert=Path(next(iter(spec.submodule_search_locations)))/"cacert.pem"
            if not cert.exists():shutil.copyfile(certifi.where(),cert)
        try:from playerokapi import Account
        except ImportError:from playerokapi.account import Account
        kwargs = {"cookies": self.cookies, "user_agent": self.config.playerok_user_agent, "requests_timeout": self.config.playerok_request_timeout}
        if self.config.playerok_proxy: kwargs["proxy"] = self.config.playerok_proxy
        return Account(**kwargs).get()

    def orders(self):
        account = self.account()
        try: deals = account.get_deals(count=100).deals
        except Exception:
            deals = []
            for chat in account.get_chats(count=100).chats:
                deals += list(getattr(chat, "deals", []) or [])
        def enum(x): return getattr(x,"name",str(x))
        result = []
        for d in deals:
            if enum(getattr(d,"direction",None)) != "OUT": continue
            item = getattr(d, "item", None)
            fields = getattr(d,"obtaining_fields",[]) or []
            recipient = None
            import re
            for f in fields:
                label, value = str(getattr(f,"label","")), str(getattr(f,"value",""))
                match = re.fullmatch(r"@?([A-Za-z0-9_]{5,32})", value.strip())
                if match and ("@" in value or "username" in label.lower()): recipient = match.group(1)
            result.append({"id":str(d.id),"status":enum(getattr(d,"status",None)),"lot_id":str(getattr(item,"id","") or ""),
                           "title":str(getattr(item,"name","") or ""),"sale_rub":str(getattr(d,"price",0) or 0),"recipient":recipient})
        return result

def telegram_options(config):
    proxy=config.get("MRKT_MTPROTO_PROXY")
    if not proxy:return {}
    p=urlparse(proxy)
    if proxy.startswith("tg://"):
        q=parse_qs(p.query);parts=(q["server"][0],int(q["port"][0]),q["secret"][0])
    else:parts=(p.hostname,p.port,p.username or p.password)
    return {"connection":connection.ConnectionTcpMTProxyRandomizedIntermediate,"proxy":parts}

class Mrkt:
    def __init__(self, db):
        self.db, self.config = db, db.config
        self.token = None
        self.http = curl_requests.Session(impersonate="chrome")
        self.headers = {"Content-Type":"application/json", "Origin":"https://cdn.tgmrkt.io", "Referer":"https://cdn.tgmrkt.io/"}

    def telegram(self, operation):
        async def run():
            from ..runtime_config import has_user_session
            if not has_user_session(self.db): raise IntegrationError("Войдите в Telegram в настройках панели")
            stored=self.config.get("TELEGRAM_USER_SESSION")
            session=StringSession(stored) if stored else str(self.config.session_path)
            client = TelegramClient(session, int(self.config.get("MRKT_TELEGRAM_API_ID") or 0),
                self.config.get("MRKT_TELEGRAM_API_HASH"), timeout=10, connection_retries=1, request_retries=0, **telegram_options(self.config))
            try:
                async with asyncio.timeout(30):
                    await client.connect()
                    if not await client.is_user_authorized(): raise IntegrationError("Telegram-сессия больше не авторизована")
                    return await operation(client)
            except TimeoutError: raise IntegrationError("Telegram не ответил за 30 секунд")
            finally: await client.disconnect()
        return asyncio.run(run())

    def authenticate(self):
        async def auth(client):
            peer = await client.get_input_entity("mrkt")
            web = await client(functions.messages.RequestAppWebViewRequest(peer=peer, app=types.InputBotAppShortName(bot_id=peer,short_name="app"),platform="android"))
            return parse_qs(urlsplit(web.url).fragment or urlsplit(web.url).query).get("tgWebAppData", [None])[0] or unquote(web.url.split("tgWebAppData=",1)[1].split("&",1)[0])
        init_data = self.telegram(auth)
        response = self.http.post(self.config.get("MRKT_API_URL", "https://api.tgmrkt.io/api/v1")+"/auth",json={"data":init_data},headers=self.headers,timeout=20)
        if response.status_code != 200: raise IntegrationError(f"MRKT авторизация: HTTP {response.status_code}")
        self.token = response.json().get("token")
        if not self.token: raise IntegrationError("MRKT не вернул токен")

    def request(self, method, path, payload=None, write=False):
        if write: gate(self.db, "purchases")
        if not self.token: self.authenticate()
        throttle = self.db.get("throttle", "mrkt") or {}
        delay = throttle.get("next",0)-time.time()
        if delay > 2: raise IntegrationError(f"MRKT: пауза по ограничению запросов ещё {delay:.0f} секунд")
        if delay > 0: time.sleep(delay)
        self.db.put("throttle","mrkt",{"next":time.time()+0.5})
        if write:gate(self.db,"purchases")
        response = self.http.request(method,self.config.get("MRKT_API_URL","https://api.tgmrkt.io/api/v1")+path,
            headers={**self.headers,"Authorization":self.token},json=payload,timeout=20)
        if response.status_code == 401:
            self.token = None
            raise IntegrationError("MRKT: сессия истекла, запрос будет повторён после авторизации")
        if response.status_code == 429:
            try: retry = max(1,min(float(response.headers.get("Retry-After",60)),3600))
            except ValueError: retry = 60
            self.db.put("throttle","mrkt",{"next":time.time()+retry})
            raise IntegrationError("MRKT ограничил частоту запросов")
        if response.status_code >= 400: raise IntegrationError(f"MRKT: HTTP {response.status_code}")
        return response.json()

    def search(self, collection, model):
        result = self.request("POST","/gifts/saling",{"collectionNames":[collection],"modelNames":[model],"backdropNames":[],"symbolNames":[],
            "ordering":"Price","lowToHigh":True,"maxPrice":None,"minPrice":None,"mintable":None,"number":None,"count":100,"cursor":"","query":None,"promotedFirst":False})
        gifts = result.get("gifts",[]) if isinstance(result,dict) else []
        def name(g, key):
            v = g.get(key+"Name") or g.get(key)
            return str(v.get("name","") if isinstance(v,dict) else v or "").strip().casefold()
        matches = [g for g in gifts if name(g,"collection")==collection.strip().casefold() and name(g,"model")==model.strip().casefold()
                   and g.get("salePrice") is not None and D(g["salePrice"])>0 and not g.get("isSold",False)]
        matches.sort(key=lambda g:D(g["salePrice"]))
        return matches

    @staticmethod
    def ton(amount): return str(D(amount)/Decimal("1000000000"))

    def balances(self):
        result = self.request("GET","/balance")
        async def stars(c):
            result = await c(functions.payments.GetStarsStatusRequest(peer=types.InputPeerSelf()))
            return int(getattr(result.balance,"amount",result.balance))
        return {"mrkt_ton":self.ton(result["hard"]),"stars":self.telegram(stars)}

    def buy(self, gift):
        result = self.request("POST","/gifts/buy",{"ids":[gift["id"]],"prices":{gift["id"]:gift["salePrice"]}},write=True)
        gifts = result if isinstance(result,list) else result.get("gifts",result.get("purchasedGifts",[]))
        found = next((g for g in gifts if str(g.get("id"))==str(gift["id"])),None)
        if not found: raise ManualRequired("MRKT не подтвердил ID купленного подарка; требуется сверка")
        return found

    def withdraw(self, gift_id): return self.request("POST","/gifts/return",{"ids":[gift_id]},write=True)

    def saved(self, slug):
        async def read(c):
            offset = ""
            while True:
                page = await c(functions.payments.GetSavedStarGiftsRequest(peer=types.InputPeerSelf(),offset=offset,limit=100))
                for s in page.gifts:
                    if getattr(s.gift,"slug",None)==slug:
                        at = getattr(s,"can_transfer_at",None)
                        return {"msg_id":s.msg_id,"slug":slug,"transfer_stars":getattr(s,"transfer_stars",0) or 0,
                                "can_transfer_at":at.timestamp() if hasattr(at,"timestamp") else at or 0}
                offset = getattr(page,"next_offset",None)
                if not offset: return None
        return self.telegram(read)

    def transfer_form(self, saved, recipient):
        async def read(c):
            peer = await c.get_input_entity(await c.get_entity(recipient))
            if not isinstance(peer,types.InputPeerUser): raise IntegrationError("Получатель должен быть пользователем Telegram")
            invoice = types.InputInvoiceStarGiftTransfer(stargift=types.InputSavedStarGiftUser(msg_id=saved["msg_id"]),to_id=peer)
            form = await c(functions.payments.GetPaymentFormRequest(invoice=invoice))
            prices = getattr(getattr(form,"invoice",None),"prices",[]) or []
            return {"form_id":form.form_id,"recipient_id":peer.user_id,"access_hash":peer.access_hash,
                    "stars":sum(p.amount for p in prices),"currency":getattr(getattr(form,"invoice",None),"currency",None)}
        return self.telegram(read)

    def transfer(self, saved, recipient, expected_recipient_id):
        gate(self.db,"transfers")
        async def send(c):
            peer = await c.get_input_entity(await c.get_entity(recipient))
            if not isinstance(peer,types.InputPeerUser): raise IntegrationError("Неверный получатель")
            if peer.user_id != expected_recipient_id: raise ManualRequired("Изменился ID Telegram получателя")
            ref = types.InputSavedStarGiftUser(msg_id=saved["msg_id"])
            if saved["transfer_stars"]:
                invoice = types.InputInvoiceStarGiftTransfer(stargift=ref,to_id=peer)
                form = await c(functions.payments.GetPaymentFormRequest(invoice=invoice))
                cost = sum(p.amount for p in form.invoice.prices)
                if cost != saved["transfer_stars"]: raise ManualRequired("Стоимость передачи изменилась")
                gate(self.db,"transfers")
                await c(functions.payments.SendStarsFormRequest(form_id=form.form_id,invoice=invoice))
            else:
                gate(self.db,"transfers")
                await c(functions.payments.TransferStarGiftRequest(stargift=ref,to_id=peer))
            return peer.user_id
        return self.telegram(send)

    def owner(self, slug):
        async def read(c):
            result = await c(functions.payments.GetUniqueStarGiftRequest(slug=slug))
            return getattr(getattr(result.gift,"owner_id",None),"user_id",None)
        return self.telegram(read)

class Bot:
    def __init__(self, db):
        self.db = db
        self.http = requests.Session()
        proxy = db.config.get("TELEGRAM_PROXY")
        if proxy: self.http.proxies.update({"http":proxy,"https":proxy})
    def request(self, method, data=None):
        r = self.http.post("https://api.telegram.org/bot"+self.db.config.get("TELEGRAM_BOT_TOKEN")+"/"+method,json=data or {},timeout=15)
        if r.status_code != 200: raise IntegrationError(f"Telegram Bot API: HTTP {r.status_code}")
        p = r.json()
        if not p.get("ok"): raise IntegrationError("Telegram Bot API отклонил запрос")
        return p["result"]
