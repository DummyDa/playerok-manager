"""Owner-only management of real catalog balances."""
import time

from fastapi import APIRouter, HTTPException, Query, Response

from . import catalog_credit as credit, schemas
from .catalog_messages import catalog_base as base_url
from .safety import is_nft_lot


def admin_view(db,payment):
    raw=db.cipher.decrypt(payment["code_encrypted"].encode()).decode()
    code=raw[:4]+"-"+raw[4:]
    return {"id":payment["id"],"code":code,"valid":credit.valid(payment),"state":payment["state"],"recipient":payment["recipient"],"paid_rub":payment["paid_rub"],"balance_rub":str(credit.balance(payment)),"adjustment_rub":payment.get("adjustment_rub","0"),"collection":payment.get("collection",""),"created_at":payment.get("created_at"),"test":False,"notice":payment.get("notice"),"purchases":credit.purchases(payment),"locked":payment["state"] in credit.LOCKED,"reserved_rub":str(payment.get("reservation_rub",0)),"url":credit.catalog_link(payment,code,base_url(db) or "http://127.0.0.1:3180")}


def router(db):
    routes=APIRouter(prefix="/api/v1/catalog-codes",tags=["Catalog codes"])

    def get_payment(id):
        value=db.get("catalog_credit_payment",id)
        if not value:raise HTTPException(404,"Код не найден")
        return value

    @routes.get("")
    def listing(response:Response,status:str=Query("all",pattern="^(all|valid|invalid)$"))->dict:
        response.headers["Cache-Control"]="no-store"
        rows=sorted(db.list("catalog_credit_payment",100000),key=lambda p:p.get("created_at",0),reverse=True)
        counts={"all":len(rows),"valid":sum(credit.valid(p) for p in rows)}
        counts["invalid"]=counts["all"]-counts["valid"]
        rows=[p for p in rows if status=="all" or credit.valid(p)==(status=="valid")]
        return {"items":[admin_view(db,p) for p in rows],"counts":counts}

    @routes.post("")
    def create(payload:schemas.CatalogCodeCreate,response:Response)->dict:
        response.headers["Cache-Control"]="no-store"
        if payload.lot_id and not is_nft_lot(db.get("lot",payload.lot_id)):raise ValueError("Выберите NFT-лот с привязанной коллекцией")
        payment,_=credit.create(db,payload.amount_rub,payload.recipient,payload.lot_id)
        return admin_view(db,payment)

    @routes.patch("/{id}/balance")
    def change(id:str,payload:schemas.CatalogCodeBalance,response:Response)->dict:
        response.headers["Cache-Control"]="no-store"
        payment=credit.change_balance(db,get_payment(id),payload.balance_rub,payload.note)
        return admin_view(db,payment)

    @routes.delete("/{id}")
    def remove(id:str,response:Response)->dict:
        response.headers["Cache-Control"]="no-store"
        return admin_view(db,credit.remove(db,get_payment(id)))

    @routes.get("/{id}/events")
    def events(id:str,response:Response)->dict:
        response.headers["Cache-Control"]="no-store"
        payment=get_payment(id)
        rows=[e for e in db.list("catalog_credit_event",100000) if e.get("payment_id")==id]
        if not any(e["action"]=="CREATED" for e in rows):
            rows.append({"id":"creation","at":payment.get("created_at"),"action":"CREATED","after_rub":payment.get("initial_rub",payment["paid_rub"])})
        return {"items":sorted(rows,key=lambda e:e.get("at") or 0,reverse=True)}

    return routes
