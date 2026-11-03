"""Price a whole collection without permitting collection-wide NFT delivery."""
import hashlib

from .safety import is_nft_lot


def collection_lot(lot):
    return bool(lot and lot.get("delivery_mode") == "catalog" and lot.get("lot_type") == "PROMOTIONAL"
                and str(lot.get("catalog_collection") or "").strip() and not lot.get("pair_id"))


def can_price(lot):
    return is_nft_lot(lot) or collection_lot(lot)


def quote_key(lot):
    if collection_lot(lot):
        return "collection:" + hashlib.sha256(lot["catalog_collection"].strip().casefold().encode()).hexdigest()[:24]
    return lot.get("pair_id")


def collection_scopes(db):
    return {quote_key(lot): {"id": quote_key(lot), "collection": lot["catalog_collection"].strip(), "model": "", "enabled": True}
            for lot in db.list("lot", 100000) if collection_lot(lot) and not lot.get("missing")}


def scope(db, key):
    return db.get("pair", key) or collection_scopes(db).get(key)
