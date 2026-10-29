import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.service import Service
from backend.app import browser_delivery as bridge
from backend.app.operations import recover

ORIGIN="chrome-extension://"+"a"*32

@pytest.fixture
def browser(db):
    db.configure(delivery_transport="chrome_extension",delivery_tested=True,purchases_enabled=True,transfers_enabled=True,stars_rub="2")
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"lot_id":"l","stage":"WITHDRAWN","gift_slug":"LibertyFigure-123","recipient":"buyer_name","recipient_id":123,"snapshot":{}})
    op=db.put("operation","o:transfer",{"state":"QUEUED","action":"order.transfer","order_id":"o","payload":{"saved":{"msg_id":5,"transfer_stars":25}},"expected":{"transfer_rub":"50"}})
    with TestClient(create_app(db)) as api:
        login=api.post("/api/v1/auth/setup",json={"password":"browser-test-password"}).json()
        admin={"X-CSRF-Token":login["csrf"]}
        code=api.post("/api/v1/browser-delivery/pairing",headers=admin).json()["code"]
        paired=api.post(bridge.PREFIX+"pair",headers={"Origin":ORIGIN},json={"code":code})
        assert paired.status_code==200,paired.text
        headers={"Origin":ORIGIN,"Authorization":"Bearer "+paired.json()["token"]}
        api.post(bridge.PREFIX+"poll",headers=headers,json={"ready":True})
        yield api,admin,headers,op

def event(api,headers,job,name,**extra):
    return api.post(bridge.PREFIX+"event",headers=headers,json={"job_id":job["id"],"lease":job["lease"],"event":name,**extra})

def claimed(db,browser):
    api,admin,headers,op=browser
    bridge.enqueue(db,op)
    result=api.post(bridge.PREFIX+"poll",headers=headers,json={"ready":True})
    assert result.status_code==200,result.text
    return result.json()["job"]

def test_pairing_is_local_single_use_and_secret_is_not_in_status(db,browser):
    api,admin,headers,_=browser
    status=api.get("/api/v1/browser-delivery").json()
    assert status["paired"] and status["ready"]
    assert headers["Authorization"][7:] not in str(status)
    assert api.post(bridge.PREFIX+"poll",json={"ready":True}).status_code==403
    assert api.post(bridge.PREFIX+"poll",headers={"Origin":ORIGIN},json={"ready":True}).status_code==401
    assert api.post(bridge.PREFIX+"pair",headers={"Origin":ORIGIN},json={"code":"x"*24}).status_code==403
    assert api.post("/api/v1/browser-delivery/pairing").status_code==403
    api.post("/api/v1/browser-delivery/revoke",headers=admin)
    assert api.post(bridge.PREFIX+"poll",headers=headers,json={"ready":True}).status_code==401

def test_one_job_one_permit_and_no_success_from_browser_claim(db,browser):
    api,_,headers,_=browser;job=claimed(db,browser)
    assert api.post(bridge.PREFIX+"poll",headers=headers,json={"ready":True}).json()["job"] is None
    assert event(api,headers,job,"submit").status_code==409
    assert event(api,headers,job,"prepared",slug="Other-123",recipient_id=123,stars=25).status_code==409
    assert event(api,headers,job,"prepared",slug=job["slug"],recipient_id=123,stars=25).status_code==200
    assert db.get("browser_job",job["id"])["state"]=="READY"
    db.put("browser_job",job["id"],{"state":"AUTHORIZED","permit_until":time.time()+20})
    assert event(api,headers,job,"submit").json()["permit"]
    assert event(api,headers,job,"submit").status_code==409
    event(api,headers,job,"done")
    assert db.get("order","o")["stage"]=="WITHDRAWN"
    assert db.list("ledger")==[]
    svc=Service(db);svc.mrkt=SimpleNamespace(owner=lambda slug:999)
    bridge.tick(svc)
    assert db.get("order","o")["stage"]=="WITHDRAWN"
    svc.mrkt=SimpleNamespace(owner=lambda slug:123)
    bridge.tick(svc);bridge.tick(svc)
    assert db.get("order","o")["stage"]=="TRANSFERRED"
    assert db.get("operation",job["id"])["state"]=="COMPLETE"
    assert len(db.list("ledger"))==1 and db.list("ledger")[0]["amount_rub"]=="50"

def test_restart_lost_callback_verifies_without_repeating_click(db,browser):
    job=claimed(db,browser)
    db.put("browser_job",job["id"],{"state":"SUBMITTED","submitted_at":time.time()})
    recover(db)
    svc=Service(db);svc.mrkt=SimpleNamespace(owner=lambda slug:123)
    bridge.tick(svc)
    assert db.get("order","o")["stage"]=="TRANSFERRED"
    assert db.get("browser_job",job["id"])["state"]=="COMPLETE"

def test_unknown_delivery_never_reissues_job(db,browser):
    job=claimed(db,browser)
    db.put("browser_job",job["id"],{"state":"SUBMITTED","submitted_at":time.time()-121})
    svc=Service(db);svc.mrkt=SimpleNamespace(owner=lambda slug:999)
    bridge.tick(svc)
    assert db.get("operation",job["id"])["state"]=="UNKNOWN"
    assert db.get("order","o")["stage"]=="MANUAL"
    assert bridge.claim(db) is None

def test_stop_and_wrong_recipient_or_cost_prevent_click(db,browser):
    api,_,headers,_=browser;job=claimed(db,browser)
    assert event(api,headers,job,"prepared",slug=job["slug"],recipient_id=999,stars=25).status_code==409
    assert event(api,headers,job,"prepared",slug=job["slug"],recipient_id=123,stars=26).status_code==409
    db.put("browser_job",job["id"],{"state":"AUTHORIZED","permit_until":time.time()+20})
    db.configure(emergency_stop=True)
    assert event(api,headers,job,"submit").status_code==400
    assert db.get("browser_job",job["id"])["state"]=="AUTHORIZED"

def test_execute_routes_to_browser_and_never_calls_mtproto_transfer(db,browser,monkeypatch):
    svc=Service(db);svc.mrkt=Mock()
    monkeypatch.setattr(svc,"order_preflight",lambda op:None)
    svc.execute("o:transfer")
    assert db.get("operation","o:transfer")["state"]=="BROWSER_WAIT"
    assert db.get("browser_job","o:transfer")["state"]=="QUEUED"
    svc.mrkt.transfer.assert_not_called()
    assert db.list("ledger")==[]

def test_manual_goods_offline_and_stale_permissions_block(db,browser):
    _,_,_,op=browser
    db.put("browser","chrome",{"heartbeat":time.time()-100})
    with pytest.raises(bridge.ManualRequired):bridge.enqueue(db,op)
    db.put("browser","chrome",{"heartbeat":time.time()})
    db.put("lot","l",{"delivery_mode":"manual"})
    with pytest.raises(bridge.ManualRequired):bridge.enqueue(db,op)

def test_mark_sent_requires_verified_exact_owner(db,browser):
    svc=Service(db);svc.mrkt=SimpleNamespace(owner=lambda slug:999)
    with pytest.raises(bridge.ManualRequired,match="Владелец"):
        svc.order_preflight({"action":"order.mark_sent","order_id":"o"})

def test_manual_preview_is_possible_on_pause_but_never_receives_payment_permission(db,browser):
    api,_,headers,_=browser
    db.configure(emergency_stop=True,transfers_enabled=False,delivery_tested=False)
    before=db.get("order","o")
    db.put("browser_job","preview",{"preview_only":True,"state":"QUEUED","slug":"LibertyFigure-123","recipient":"buyer_name","recipient_id":123,"stars":25,"deadline":time.time()+300})
    job=api.post(bridge.PREFIX+"poll",headers=headers,json={"ready":True}).json()["job"]
    assert job["id"]=="preview"
    assert event(api,headers,job,"prepared",slug=job["slug"],recipient_id=123,stars=25).status_code==200
    svc=Service(db);svc.mrkt=SimpleNamespace(owner=lambda slug:999)
    bridge.tick(svc)
    assert db.get("browser_job","preview")["state"]=="READY"
    assert event(api,headers,job,"submit").status_code==409
    event(api,headers,job,"error",error="Old adapter timeout")
    assert db.get("browser_job","preview")["state"]=="READY"
    svc.mrkt=SimpleNamespace(owner=lambda slug:123)
    bridge.tick(svc)
    assert db.get("browser_job","preview")["state"]=="COMPLETE"
    assert db.get("order","o")==before and db.list("ledger")==[]

def test_expired_permit_and_manual_hold_block_even_an_authorized_browser(db,browser):
    api,_,headers,_=browser;job=claimed(db,browser)
    db.put("browser_job",job["id"],{"state":"AUTHORIZED","permit_until":time.time()-1})
    assert event(api,headers,job,"submit").status_code==409
    db.put("browser_job",job["id"],{"permit_until":time.time()+20})
    db.put("order","o",{"manual_hold":True})
    assert event(api,headers,job,"submit").status_code==409

def test_crash_during_recording_delivery_recovers_ledger_once(db,browser,monkeypatch):
    job=claimed(db,browser)
    db.put("browser_job",job["id"],{"state":"SUBMITTED","submitted_at":time.time(),"stars_rub":"2"})
    svc=Service(db);svc.mrkt=SimpleNamespace(owner=lambda slug:123)
    original=svc.ledger
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError("Crash after ledger commit")
    monkeypatch.setattr(svc,"ledger",crash)
    with pytest.raises(RuntimeError):bridge.tick(svc)
    assert db.get("browser_job",job["id"])["state"]=="VERIFYING"
    db.configure(stars_rub="3")  # Historic unit cost must remain unchanged.
    monkeypatch.setattr(svc,"ledger",original)
    bridge.tick(svc)
    assert db.get("browser_job",job["id"])["state"]=="COMPLETE"
    assert len(db.list("ledger"))==1 and db.list("ledger")[0]["amount_rub"]=="50"

@pytest.mark.parametrize("existing_active",[False,True])
def test_chrome_background_uses_existing_window_inactive_tab_and_no_focus(tmp_path,existing_active):
    import json,subprocess,shutil
    from pathlib import Path
    node=shutil.which("node")
    if not node:pytest.skip("Node unavailable")
    source=Path(__file__).resolve().parents[1]/"extension/background.js"
    script=tmp_path/"background-test.cjs"
    script.write_text(r'''
const vm=require('node:vm'),fs=require('node:fs');
const existing=process.argv[3]==='active';
const calls=[],storage={token:'test-token',tabId:existing?42:undefined},job={id:'o:transfer',lease:'lease',slug:'LibertyFigure-123'};
const chrome={storage:{local:{get:async()=>storage,set:async v=>Object.assign(storage,v),remove:async()=>{}}},
windows:{getAll:async()=>[{id:7}]},tabs:{get:async()=>{if(existing)return {id:42,active:true,url:'https://web.telegram.org/a/'};throw Error('No tab');},create:async v=>{calls.push(['create',v]);return {id:42,active:false};},
update:async(id,v)=>calls.push(['update',v]),sendMessage:async(id,m)=>{if(m.type==='probe')return {ready:true};calls.push(['prepare',{slug:m.job.slug}]);return {started:true};}},
runtime:{onMessage:{addListener(){}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},
alarms:{onAlarm:{addListener(){}},create:async()=>{}},action:{onClicked:{addListener(){}}}};
const context=vm.createContext({chrome,AbortSignal,setTimeout:fn=>{fn();return 0;},fetch:async()=>({ok:true,json:async()=>({job})})});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInContext('cycle()',context).then(()=>process.stdout.write(JSON.stringify(calls)));
''',encoding="utf-8")
    result=subprocess.run([node,str(script),str(source),"active" if existing_active else "new"],capture_output=True,text=True,encoding="utf-8",timeout=10,check=True)
    calls=json.loads(result.stdout)
    if existing_active:assert not any(name=="create" for name,_ in calls)
    else:assert calls[0]==["create",{"windowId":7,"url":"https://web.telegram.org/a/","active":False}]
    assert all(values.get("active") is not True for _,values in calls)
    assert ["prepare",{"slug":"LibertyFigure-123"}] in calls

def test_extension_options_tab_pairs_without_an_active_order(tmp_path):
    import json,subprocess,shutil
    from pathlib import Path
    node=shutil.which("node")
    if not node:pytest.skip("Node unavailable")
    source=Path(__file__).resolve().parents[1]/"extension/background.js"
    script=tmp_path/"pairing-test.cjs"
    script.write_text(r'''
const vm=require('node:vm'),fs=require('node:fs');
let listener;const calls=[],storage={},id='a'.repeat(32),options='chrome-extension://'+id+'/options.html';
const chrome={storage:{local:{get:async()=>storage,set:async v=>Object.assign(storage,v),remove:async()=>{}}},
windows:{getAll:async()=>[{id:7}]},tabs:{get:async()=>({id:42,url:'https://web.telegram.org/a/',active:false}),create:async()=>({id:42,active:false}),update:async()=>{},sendMessage:async()=>({ready:true})},
runtime:{id,getURL:p=>'chrome-extension://'+id+'/'+p,onMessage:{addListener:fn=>{listener=fn;}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},
alarms:{onAlarm:{addListener(){}},create:async()=>{}},action:{onClicked:{addListener(){}}}};
const context=vm.createContext({chrome,AbortSignal,setTimeout,fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url.endsWith('/pair')?{token:'new-test-token'}:{job:null}};}});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
(async()=>{
const results=[];
for(const tab of [{id:5},undefined])results.push(await new Promise(resolve=>listener({type:'pair',code:'example-code'},{id,url:options,tab},resolve)));
const blocked=await new Promise(resolve=>listener({type:'pair',code:'example-code'},{id,url:'https://web.telegram.org/a/',tab:{id:42}},resolve));
process.stdout.write(JSON.stringify({results,blocked,pairs:calls.filter(x=>x.endsWith('/pair')).length,token:storage.token}));
})();
''',encoding="utf-8")
    result=subprocess.run([node,str(script),str(source)],capture_output=True,text=True,encoding="utf-8",timeout=10,check=True)
    output=json.loads(result.stdout)
    assert output["results"]==[{"ok":True},{"ok":True}]
    assert output["pairs"]==2 and output["token"]=="new-test-token"
    assert output["blocked"].get("error")

@pytest.mark.parametrize("inflight",[False,True])
@pytest.mark.parametrize("signal",["disconnect","inactive"])
def test_extension_restores_lost_content_script_without_reloading_an_inflight_transfer(tmp_path,inflight,signal):
    import json,subprocess,shutil
    from pathlib import Path
    node=shutil.which("node")
    if not node:pytest.skip("Node unavailable")
    source=Path(__file__).resolve().parents[1]/"extension/background.js"
    script=tmp_path/"reconnect-test.cjs"
    script.write_text(r'''
const vm=require('node:vm'),fs=require('node:fs');
const inflight=process.argv[3]==='inflight',storage={activeJob:inflight?{id:'o:transfer'}:undefined};
let reloads=0,recovered=false;const inactive=process.argv[4]==='inactive';
const chrome={storage:{local:{get:async()=>storage,set:async v=>Object.assign(storage,v),remove:async()=>{}}},
tabs:{reload:async()=>{reloads++;recovered=true;},sendMessage:async()=>{if(!recovered){if(inactive)return {ready:false,needsReload:true};throw Error('Receiving end does not exist');}return {ready:true};}},
runtime:{onMessage:{addListener(){}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},
alarms:{onAlarm:{addListener(){}}},action:{onClicked:{addListener(){}}}};
const context=vm.createContext({chrome,AbortSignal,setTimeout:fn=>fn(),fetch:async()=>({ok:true,json:async()=>({state:'SUBMITTED'})})});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInContext('probeTab({id:42})',context).then(result=>process.stdout.write(JSON.stringify({ready:result.ready,reloads})));
''',encoding="utf-8")
    result=subprocess.run([node,str(script),str(source),"inflight" if inflight else "idle",signal],capture_output=True,text=True,encoding="utf-8",timeout=10,check=True)
    assert json.loads(result.stdout)=={"ready":not inflight,"reloads":0 if inflight else 1}

def test_extension_attaches_to_already_loaded_weba_without_reload_or_activation(tmp_path):
    import json,subprocess,shutil
    from pathlib import Path
    node=shutil.which("node")
    if not node:pytest.skip("Node unavailable")
    source=Path(__file__).resolve().parents[1]/"extension/background.js"
    script=tmp_path/"attach-test.cjs"
    script.write_text(r'''
const vm=require('node:vm'),fs=require('node:fs'),storage={tabId:42},calls=[];let injected=false;
const chrome={storage:{local:{get:async()=>storage,set:async v=>Object.assign(storage,v)}},
tabs:{get:async()=>({id:42,url:'https://web.telegram.org/a/'}),query:async()=>[{id:99,url:'https://web.telegram.org/a/'}],
sendMessage:async id=>{if(id===42)return {ready:false};if(!injected)throw Error('No receiver');return {ready:true};},update:async(id,values)=>calls.push({id,values})},
scripting:{executeScript:async spec=>{calls.push(spec);injected=true;}},runtime:{onMessage:{addListener(){}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},alarms:{onAlarm:{addListener(){}}},action:{onClicked:{addListener(){}}}};
const context=vm.createContext({chrome});vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInContext('managedTab()',context).then(tab=>process.stdout.write(JSON.stringify({id:tab.id,stored:storage.tabId,calls})));
''',encoding="utf-8")
    result=subprocess.run([node,str(script),str(source)],capture_output=True,text=True,encoding="utf-8",timeout=10,check=True)
    output=json.loads(result.stdout)
    assert output["id"]==99 and output["stored"]==99
    assert output["calls"]==[{"target":{"tabId":99},"files":["content.js"]},{"id":99,"values":{"autoDiscardable":False}}]
