from typing import Literal
from pydantic import BaseModel,Field,ConfigDict,model_validator
from decimal import Decimal

Money=Decimal
class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)

class Password(Strict): password:str=Field(min_length=10,max_length=128)
class PasswordChange(Strict):
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=10,max_length=128)
class Cookies(Strict): cookies:str=Field(min_length=10,max_length=20000)
class Settings(Strict):
    delivery_transport:Literal["mtproto","kurigram","chrome_extension"]|None=None
    emergency_stop:bool|None=None
    repricing_enabled:bool|None=None
    purchases_enabled:bool|None=None
    transfers_enabled:bool|None=None
    promotions_enabled:bool|None=None
    notifications_enabled:bool|None=None
    delivery_tested:bool|None=None
    stars_rub:Decimal|None=Field(default=None,ge=0)
    transfer_stars_reserve:int|None=Field(default=None,ge=0,le=100000)
    purchase_limit_rub:Decimal|None=Field(default=None,ge=0)
    daily_purchase_limit_rub:Decimal|None=Field(default=None,ge=0)
    daily_promotion_limit_rub:Decimal|None=Field(default=None,ge=0)
    quote_interval:int|None=Field(default=None,ge=60,le=86400)
    competitor_interval:int|None=Field(default=None,ge=60,le=86400)
    quote_max_age:int|None=Field(default=None,ge=60,le=86400)
    description_template:str|None=Field(default=None,max_length=20000)
    buyer_paid_enabled:bool|None=None
    buyer_delivered_enabled:bool|None=None
    buyer_paid_template:str|None=Field(default=None,max_length=4000)
    buyer_delivered_template:str|None=Field(default=None,max_length=4000)

    @model_validator(mode="after")
    def valid_buyer_templates(self):
        from .buyer_messages import validate_template
        for template in (self.buyer_paid_template,self.buyer_delivered_template):
            if template is not None:validate_template(template)
        return self

class Fee(Strict):
    name:str=Field(min_length=1,max_length=100)
    type:Literal["fixed","percent"]
    value:Decimal=Field(ge=0)
    basis:Literal["sale","remaining","purchase"]="remaining"
    include_fixed:bool=False
    enabled:bool=True
    @model_validator(mode="after")
    def valid(self):
        if self.type=="percent" and self.value>=100:raise ValueError("Процент должен быть меньше 100")
        return self
class FeeProfile(Strict):
    name:str=Field(min_length=1,max_length=100)
    configured:bool=False
    fees:list[Fee]=Field(default_factory=list,max_length=30)

class Strategy(Strict):
    enabled:bool=False
    premium_mode:Literal["auto","include","exclude"]="auto"
    premium_purchases_per_sale:int=Field(default=1,ge=1,le=100)
    profit_type:Literal["percent","rub"]="percent"
    profit_value:Decimal=Field(default=Decimal(5),ge=0)
    profit_basis:Literal["purchase","sale"]="purchase"
    min_price:int=Field(default=0,ge=0)
    max_price:int|None=Field(default=None,ge=1)
    round_step:int=Field(default=1,ge=1)
    change_threshold:int=Field(default=1,ge=1)
    use_competitor:bool=False
    competitor_id:str|None=None
    competitor_mode:Literal["cheaper","higher","equal"]="cheaper"
    competitor_offset:Decimal=Field(default=Decimal(1),ge=0)
    competitor_offset_type:Literal["rub","percent"]="rub"

class Pair(Strict):
    collection:str=Field(min_length=1,max_length=120)
    model:str=Field(min_length=1,max_length=120)
    collection_id:str|None=None
    model_id:str|None=None
    enabled:bool=True

class Binding(Strict):
    pair_id:str|None=None
    delivery_mode:Literal["manual","telegram_nft"]="telegram_nft"
    fee_profile_id:str="default"
    strategy:Strategy|None=None

class Competitor(Strict):
    url:str=Field(max_length=700)
    lot_id:str
    pair_id:str
    confirmed:bool=False
    enabled:bool=True
    note:str=Field(default="",max_length=1000)

class PromotionSchedule(Strict):
    enabled:bool=False
    interval:int=Field(default=3600,ge=60,le=604800)
    max_cost_rub:Decimal=Field(default=Decimal(0),ge=0)

class BulkPromotionSchedule(PromotionSchedule):
    item_ids:list[str]=Field(min_length=1,max_length=100)

class LotChange(Strict):
    name:str|None=Field(default=None,min_length=1,max_length=1000)
    description:str|None=Field(default=None,max_length=20000)
    price:int|None=Field(default=None,ge=1,le=10000000)

class BulkChange(Strict):
    item_ids:list[str]=Field(min_length=1,max_length=100)
    changes:LotChange

class ResolveOrder(Strict):
    stage:Literal["MANUAL","CANCELLED"]="MANUAL"
    reason:str=Field(min_length=1,max_length=1000)

class QuoteInput(Strict):pair_id:str
class ModelsInput(Strict):collection:str=Field(min_length=1,max_length=120)
class CheckInput(Strict):name:Literal["all","playerok","mrkt","telegram"]="all"
class ExecuteInput(Strict):operation_id:str
class OperationResolution(Strict):
    resolution:Literal["CONFIRMED","NOT_EXECUTED"]
    evidence:str=Field(min_length=10,max_length=1000)
