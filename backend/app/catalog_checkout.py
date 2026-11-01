"""Worker-owned, single-attempt creation of short-lived exact-NFT listings."""
from io import BytesIO
from pathlib import Path
import re
import time

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .db import DEFAULTS, Entity, uid
from .catalog_pricing import price
from .integrations.clients import Playerok
from .integrations.mrkt_availability import gift_block_reason
from .pricing import D
from .safety import gate, ManualRequired,RateLimited

ACTIVE={"QUEUED","CREATING","UNKNOWN","READY","EXPIRING"}

def notify_result(db,record):
    from .funded_orders import checkout_result
    checkout_result(db,record)
    from .catalog_credit import checkout_result as credit_result
    credit_result(db,record)


def exact_quote(service, lot):
    gift=service.mrkt.request("GET","/gifts/gift/"+lot["catalog_gift_id"])
    slug=gift.get("slug") or gift.get("giftSlug") or gift.get("name") if isinstance(gift,dict) else None
    if not isinstance(gift,dict) or str(gift.get("id"))!=lot["catalog_gift_id"] or slug!=lot["catalog_gift_slug"] or not gift.get("isOnSale") or gift.get("isSold") or gift_block_reason(gift):
        raise ManualRequired("Выбранный NFT больше недоступен; подмена другим подарком запрещена")
    return {"available":True,"gift":gift,"ton":service.mrkt.ton(gift["salePrice"]),"fetched_at":time.time()}


def cover(gift, image_bytes):
    canvas=Image.new("RGB",(1280,960),"#101724");draw=ImageDraw.Draw(canvas)
    font_path=Path(__file__).resolve().parents[2]/"frontend/public/fonts/nunito-sans-3.ttf"
    def font(size):return ImageFont.truetype(str(font_path),size)
    draw.rounded_rectangle((40,40,1240,920),radius=36,fill="#172947",outline="#315285",width=3)
    image=Image.open(BytesIO(image_bytes));image.load()
    image=ImageOps.contain(image.convert("RGBA"),(660,660))
    canvas.paste(image,(70+(660-image.width)//2,155+(660-image.height)//2),image)
    draw.text((75,72),"NFT GIFTS",font=font(32),fill="#8db5ff")
    draw.text((770,250),"#"+str(gift.get("number","")),font=font(46),fill="#8db5ff")
    def fit(text,size,width):
        while size>18 and draw.textlength(text,font=font(size))>width:size-=1
        return font(size)
    for text,y,size in [(gift.get("collection_label") or gift["collection"],330,42),(gift.get("model") or "",405,29),(gift.get("backdrop") or "",470,24),(gift.get("symbol") or "",515,24)]:
        draw.text((770,y),str(text),font=fit(str(text),size,410),fill="#f1f5ff")
    draw.text((770,690),"PLAYEROK",font=font(30),fill="#8db5ff")
    result=BytesIO();canvas.save(result,"PNG");return result.getvalue()


def request_checkout(db, gift, shown_price, owner_keys=()):
    gate(db)
    computed=price(db,gift["price_ton"])
    if not db.settings().get("description_template","").strip():raise ValueError("Заполните шаблон описания в основной панели")
    key=str(gift["id"])
    with db.engine.connect() as conn:
        from sqlalchemy import select,insert,update
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        row=conn.execute(select(Entity.data).where(Entity.kind=="catalog_checkout",Entity.id==key)).scalar_one_or_none()
        if row and row.get("state") in ACTIVE:
            if row.get("state")=="READY" and (row.get("expires_at",0)<=time.time() or row.get("price_rub")!=shown_price):
                conn.rollback();raise ValueError("Для этого NFT уже есть лот с другим сроком или ценой. Дождитесь его снятия и обновите каталог")
            conn.commit();return key
        # Bound anonymous listing publication, even across concurrent clients.
        pending=list(conn.execute(select(Entity.data).where(Entity.kind=="catalog_checkout")).scalars())
        now=time.time()
        for owner in owner_keys:
            occupied=sum(owner in item.get("owner_keys",[]) and (item.get("state") in ACTIVE or (item.get("submitted_at") and item.get("expires_at",0)>now)) for item in pending)
            if occupied>=5:
                conn.rollback();raise ValueError("Вы создали слишком много лотов, повторите попытку позже")
        if sum(item.get("state") in ACTIVE for item in pending)>=20:
            conn.rollback();raise ValueError("Сейчас создаётся много лотов. Попробуйте позже")
        data={"state":"QUEUED","gift":gift,"shown_price":shown_price,"created_at":time.time(),"owner_keys":list(owner_keys)}
        if computed["sale_price"]!=shown_price:
            updated={**gift,"price_rub":computed["sale_price"]}
            data.update(state="PRICE_CHANGED",price_rub=computed["sale_price"],updated_gift=updated,error="Цена обновлена. Подтвердите новую сумму повторным нажатием «Купить»")
        if row:conn.execute(update(Entity).where(Entity.kind=="catalog_checkout",Entity.id==key).values(data=data,updated=time.time()))
        else:conn.execute(insert(Entity).values(kind="catalog_checkout",id=key,data=data,updated=time.time()))
        conn.commit()
    if data["state"]=="QUEUED":db.enqueue("catalog_create",{"checkout_id":key},key="catalog_create:"+key)
    return key


def schedule_create(db,record):
    attempt=record.get("rate_limit_retries",0)
    suffix=":wait:"+str(attempt) if attempt else ""
    db.enqueue("catalog_create",{"checkout_id":record["id"]},key="catalog_create:"+record["id"]+suffix,due=max(time.time(),record.get("retry_at",0)))


def create(service, key):
    db=service.db;record=db.get("catalog_checkout",key)
    if not record or record["state"]!="QUEUED":return
    if record.get("retry_at",0)>time.time():
        schedule_create(db,record);return
    try:
        gate(db)
        cached=record["gift"];slug=cached.get("slug","")
        if not re.fullmatch(r"[A-Za-z0-9]+-\d+",slug):raise ValueError("Некорректный NFT")
        lot_ref={"catalog_gift_id":str(cached["id"]),"catalog_gift_slug":slug}
        quote=exact_quote(service,lot_ref)
        estimate=price(db,quote["ton"])
        if estimate["sale_price"]!=record["shown_price"]:
            updated={**cached,"price_ton":quote["ton"],"price_nano":int(D(quote["ton"])*10**9),"price_rub":estimate["sale_price"],"checked_at":quote["fetched_at"]}
            db.put("catalog_checkout",key,{"state":"PRICE_CHANGED","price_rub":estimate["sale_price"],"updated_gift":updated,"error":"Цена обновлена. Подтвердите новую сумму повторным нажатием «Купить»"})
            notify_result(db,db.get("catalog_checkout",key))
            return
        if record.get("funding_grant_id"):
            grant=db.get("grant",record["funding_grant_id"])
            if not grant or grant["state"]!="PREPARING" or grant.get("checkout_id")!=key:raise ValueError("Выбор по коду остановлен")
        if record.get("catalog_credit_id"):
            credit=db.get("catalog_credit_payment",record["catalog_credit_id"])
            if not credit or credit["state"]!="PREPARING" or credit.get("checkout_id")!=key:raise ValueError("Выбор по коду остановлен")
        settings=db.settings()
        fields={k:str(cached.get(k) or "") for k in ("collection","model","number","backdrop","symbol")}
        fields.update(slug=slug,gift_link="https://t.me/nft/"+slug)
        title=settings.get("catalog_title_template",DEFAULTS["catalog_title_template"]).format_map(fields)[:200]
        if record.get("funding_grant_id"):title="Доплата по заказу "+record["funding_grant_id"][-8:].upper()+" — не покупайте отдельно"
        if record.get("catalog_credit_id"):title="💳 Доплата по заказу "+record["catalog_credit_id"][-8:].upper()+" | 🎁 NFT"
        description=settings["description_template"]
        for field,value in fields.items():description=description.replace("{"+field+"}",value)
        from .manual_delivery import buyer_copy
        title=buyer_copy(db,title)[:200]
        description=buyer_copy(db,description)
        response=requests.get("https://nft.fragment.com/gift/"+slug.lower()+".webp",timeout=20)
        response.raise_for_status();picture=cover(cached,response.content)
        pair_id=next((p["id"] for p in db.list("pair",100000) if p.get("collection")==cached["collection"] and p.get("model")==cached["model"]),uid());db.put("pair",pair_id,{"collection":cached["collection"],"model":cached["model"],"enabled":True})
        binding={**lot_ref,"lot_type":"TEMPORARY","pair_id":pair_id,"delivery_mode":"telegram_nft","fee_profile_id":settings["catalog_fee_profile_id"],
            "strategy":{"enabled":False,"premium_mode":"exclude"},"catalog_checkout_id":key}
        if record.get("funding_grant_id"):
            binding.update(funding_grant_id=record["funding_grant_id"],funding_purpose="TOPUP",funding_revision=record["funding_revision"])
            estimate["sale_price"]=int(D(record["topup_rub"]))
            description="Доплата по уже оплаченному заказу. Отдельный подарок не выдаётся.\n\n"+description
        if record.get("catalog_credit_id"):
            binding.update(catalog_credit_id=record["catalog_credit_id"],funding_purpose="TOPUP",funding_revision=record["funding_revision"])
            estimate["sale_price"]=int(D(record["topup_rub"]))
            description="Доплата по заказу из каталога. После оплаты будет выдан ранее выбранный подарок. Не покупайте этот лот отдельно.\n\n"+description
        # Persist intent before the first external write; unknown outcomes never auto-repeat.
        db.put("catalog_checkout",key,{"state":"CREATING","submitted_at":time.time(),"binding":binding})
        result=Playerok(db).create({"title":title,"collection":cached["collection"],"price":estimate["sale_price"],"description":description},picture)
        if not result.get("item_id") or not result.get("slug"):raise ValueError("Playerok не подтвердил созданный лот")
        expires=time.time()+900
        db.put("lot",result["item_id"],{**binding,"title":title,"description":description,"price":estimate["sale_price"],"slug":result["slug"],"status":result.get("status") or "PENDING_APPROVAL","url":"https://playerok.com/products/"+result["slug"],"expires_at":expires})
        db.put("catalog_checkout",key,{"state":"READY","lot_id":result["item_id"],"url":"https://playerok.com/products/"+result["slug"],"price_rub":estimate["sale_price"],"expires_at":expires})
        db.enqueue("catalog_expire",key="catalog_expire:"+key,due=expires)
        notify_result(db,db.get("catalog_checkout",key))
    except Exception as exc:
        current=db.get("catalog_checkout",key) or {}
        if isinstance(exc,RateLimited) and not current.get("submitted_at"):
            db.put("catalog_checkout",key,{"state":"QUEUED","retry_at":max(time.time()+2,exc.retry_at+0.5),"rate_limit_retries":current.get("rate_limit_retries",0)+1,"error":None,"notice":"MRKT занят. Ожидаем очередь проверки, повторно нажимать не нужно."})
            schedule_create(db,db.get("catalog_checkout",key))
            return
        db.put("catalog_checkout",key,{"state":"UNKNOWN" if current.get("submitted_at") else "FAILED","error":db.redact(exc)})
        notify_result(db,db.get("catalog_checkout",key))


def expire(service):
    db=service.db
    for record in db.list("catalog_checkout",100000):
        if record.get("state")=="QUEUED":
            schedule_create(db,record)
            continue
        if record.get("state")!="READY" or record.get("expires_at",0)>time.time():continue
        lot=db.get("lot",record["lot_id"])
        if not lot:continue
        client=Playerok(db)
        # A paid deal or SOLD snapshot always wins over expiration. Never refund/cancel deals.
        orders=client.orders()
        if any(o.get("lot_id")==lot["id"] and o.get("status") in {"PAID","SENT","CONFIRMED"} for o in orders):
            db.put("catalog_checkout",record["id"],{"state":"PAID"});continue
        fresh=client.item(lot["slug"])
        if fresh.get("status")=="SOLD" or fresh.get("buyer"):
            db.put("catalog_checkout",record["id"],{"state":"PAID"});continue
        if str(fresh.get("id"))!=lot["id"]:raise ValueError("Не совпадает ID лота при снятии")
        gate(db)
        db.put("catalog_checkout",record["id"],{"state":"EXPIRING"})
        try:
            query="mutation removeItem($id:UUID!){removeItem(id:$id){id}}"
            removed=client.client._graphql_post_json({"operationName":"removeItem","variables":{"id":lot["id"]},"query":query},"removeItem","/products/"+lot["slug"])
            if str((removed.get("removeItem") or {}).get("id"))!=lot["id"]:raise ValueError("Playerok не подтвердил снятие лота")
        except Exception as exc:
            db.put("catalog_checkout",record["id"],{"state":"UNKNOWN","error":db.redact(exc)});continue
        db.put("catalog_checkout",record["id"],{"state":"EXPIRED"})
        db.put("lot",lot["id"],{"missing":True,"status":"EXPIRED"})
        notify_result(db,db.get("catalog_checkout",record["id"]))
