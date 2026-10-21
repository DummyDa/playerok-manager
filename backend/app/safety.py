class IntegrationError(RuntimeError): pass
class ManualRequired(IntegrationError): pass
class WriteBlocked(IntegrationError): pass

def is_nft_lot(lot):
    if not lot:return False
    # Existing explicit collection/model bindings keep their NFT meaning.
    mode=lot.get("delivery_mode") or ("telegram_nft" if lot.get("pair_id") else "manual")
    return mode=="telegram_nft" and bool(lot.get("pair_id"))

def gate(db, feature=None):
    s=db.settings()
    if s["emergency_stop"]:raise WriteBlocked("Автоматизация аварийно остановлена")
    if feature and not s.get(feature+"_enabled"):raise WriteBlocked("Операция выключена в настройках")
