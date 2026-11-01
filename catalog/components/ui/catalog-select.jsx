import React, { useEffect, useId, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Check, ChevronDown, Search, X, Layers, Shapes, ArrowUpDown } from 'lucide-react';
import './catalog-select.css';

const labels = {collection:'Коллекция',model:'Модель',backdrop:'Фон',symbol:'Узор'};
const placeholders = {collection:'Все коллекции',model:'Любая модель',backdrop:'Любой фон',symbol:'Любой узор'};
const number = value => Number(value).toLocaleString('ru-RU', {maximumFractionDigits:2});
const commit = (key,value) => window.dispatchEvent(new CustomEvent('catalog-filter-apply',{detail:{key,value}}));

function selections(value) {
  if(!value)return [];
  try{return value.startsWith('[')?JSON.parse(value):[value]}catch{return []}
}
function encoded(values) {
  const sorted=[...new Set(values)].sort();
  return sorted.length===1?sorted[0]:sorted.length?JSON.stringify(sorted):'';
}
function useFilters() {
  const [snapshot,setSnapshot] = useState(window.catalogFilters || {data:null,state:{},loading:true});
  useEffect(()=>{
    const update = event => setSnapshot(event.detail);
    window.addEventListener('catalog-filters-updated',update);
    // Catch metadata that arrived between rendering and subscribing.
    if (window.catalogFilters) setSnapshot(window.catalogFilters);
    return ()=>window.removeEventListener('catalog-filters-updated',update);
  },[]);
  return snapshot;
}

function useDropdownCoordinator(id,setOpen,outsideInteraction) {
  useEffect(()=>{
    const closeOther=event=>{
      if(event.detail!==id){outsideInteraction.current=true;setOpen(false)}
    };
    window.addEventListener('catalog-dropdown-open',closeOther);
    return()=>window.removeEventListener('catalog-dropdown-open',closeOther);
  },[id]);
}
const announceDropdown=id=>window.dispatchEvent(new CustomEvent('catalog-dropdown-open',{detail:id}));

function Preview({item,field}) {
  if (field==='backdrop' && /^#[0-9a-f]{6}$/i.test(item.center || '') && /^#[0-9a-f]{6}$/i.test(item.edge || '')) {
    return <span className="picker-preview picker-swatch" style={{background:`radial-gradient(circle,${item.center},${item.edge})`}}/>;
  }
  return <span className="picker-preview">{item.image
    ? <img src={item.image} alt="" loading="lazy" decoding="async" width="44" height="44"/>
    : field==='symbol'?<Shapes size={23}/>:<Layers size={23}/>}</span>;
}

export function CatalogSelect({field}) {
  const snapshot = useFilters();
  const [open,setOpen] = useState(false);
  const [narrow,setNarrow] = useState(matchMedia('(max-width:760px)').matches);
  useEffect(()=>{
    const media=matchMedia('(max-width:760px)');
    const update=()=>setNarrow(media.matches);
    media.addEventListener('change',update);
    return()=>media.removeEventListener('change',update);
  },[]);
  const [query,setQuery] = useState('');
  const [visibleCount,setVisibleCount] = useState(80);
  const [firstVisible,setFirstVisible] = useState(0);
  const [floors,setFloors] = useState({});
  const [directories,setDirectories] = useState({});
  const search = useRef(null);
  const trigger = useRef(null);
  const outsideInteraction = useRef(false);
  const disabled = field==='model' && !snapshot.state.collection;
  const hasMarketFloor = field==='collection' || (!!snapshot.state.collection && (field==='model' || field==='backdrop'));
  const id=useId();
  useDropdownCoordinator(id,setOpen,outsideInteraction);
  const scope=field+':'+snapshot.state.collection;
  const sourceOptions=directories[scope] || snapshot.data?.[field] || [];
  const options=field==='backdrop'
    ? [...sourceOptions.filter(item=>item.name==='Black'),...sourceOptions.filter(item=>item.name!=='Black')]
    : sourceOptions;
  const selected=selections(snapshot.state[field]);
  const selectedNames=selected.map(name=>options.find(item=>item.name===name)?.title || name);
  const normalized=query.trim().toLocaleLowerCase('ru');
  const filtered=options.filter(item=>((item.title || item.name)+' '+(item.collection_label || '')).toLocaleLowerCase('ru').includes(normalized));
  const floorNames=JSON.stringify(filtered.slice(firstVisible,firstVisible+7).map(item=>item.name));
  useEffect(()=>{
    if(!open || disabled || !hasMarketFloor)return;
    let stopped=false,timer;
    const controller=new AbortController();
    async function poll() {
      try {
        const params=new URLSearchParams({field,collection:snapshot.state.collection || ''});
        JSON.parse(floorNames).forEach(name=>params.append('names',name));
        const response=await fetch('/api/filter-floors?'+params,{signal:controller.signal});
        if(!response.ok)throw new Error();
        const data=await response.json();
        if(stopped)return;
        if(data.options) setDirectories(previous=>({...previous,[scope]:data.options}));
        setFloors(previous=>({...previous,[scope]:{...previous[scope],...data.floors}}));
        timer=setTimeout(poll,data.pending?1200:60000);
      } catch {if(!stopped)timer=setTimeout(poll,3000);}
    }
    poll();
    return()=>{stopped=true;clearTimeout(timer);controller.abort()};
  },[open,disabled,hasMarketFloor,scope,floorNames]);
  const floorFor=item=>!hasMarketFloor?undefined:floors[scope] && Object.hasOwn(floors[scope],item.name)?floors[scope][item.name]:item.floor_price_ton;
  const title=labels[field];
  const selectedLabel=disabled?'Сначала выбери коллекцию':selectedNames.join(', ') || placeholders[field];
  // Close dependent panels when their collection changes.
  useEffect(()=>{if(field!=='collection')setOpen(false)},[snapshot.state.collection]);
  function changeOpen(next) {
    if(next && disabled) return;
    if(next) {announceDropdown(id);outsideInteraction.current=false;setQuery('');setVisibleCount(80);setFirstVisible(0);setFloors({});}
    setOpen(next);
  }
  function choose(value) {
    if(!value){commit(field,'');return;}
    const next=selected.includes(value)?selected.filter(name=>name!==value):selected.length<30?[...selected,value]:selected;
    commit(field,encoded(next));
  }
  return <Popover.Root open={open} onOpenChange={changeOpen}>
    <Popover.Trigger asChild>
      <button ref={trigger} disabled={disabled} title={disabled?"Выбери коллекцию, чтобы увидеть её модели":undefined} type="button" className="catalog-picker-trigger" data-filter={field} data-active={!!snapshot.state[field]} aria-label={`${title}: ${selectedLabel}`}>
        <span className="picker-trigger-label">{title}</span>{selected.length>0 && <span className="picker-trigger-count">{selected.length}</span>}
        <span className="picker-trigger-value">{selectedLabel}</span>
        <ChevronDown size={16} aria-hidden="true"/>
      </button>
    </Popover.Trigger>
    <Popover.Portal>
      <Popover.Content className="catalog-picker-panel" side="bottom" align={narrow?"center":"start"} sideOffset={narrow?8:12} collisionPadding={12}
        aria-labelledby={`${id}-title`} onOpenAutoFocus={event=>{event.preventDefault();search.current?.focus({preventScroll:true})}}
        onInteractOutside={()=>{outsideInteraction.current=true}}
        onCloseAutoFocus={event=>{event.preventDefault();if(!outsideInteraction.current)trigger.current?.focus({preventScroll:true})}}>
        <div className="picker-panel-heading">
          <h3 id={`${id}-title`}>{title}<span>· {number(options.length)}</span></h3>
          <Popover.Close className="picker-close" aria-label={`Закрыть: ${title}`}><X size={18}/></Popover.Close>
        </div>
        <label className="picker-search"><Search size={19} aria-hidden="true"/>
          <input ref={search} value={query} onChange={event=>{setQuery(event.target.value);setVisibleCount(80);setFirstVisible(0)}} type="search" autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false} placeholder="Поиск" aria-label={`Поиск: ${title}`}
            onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();event.currentTarget.closest('.catalog-picker-panel').querySelector('.picker-option input')?.focus();}}}/>
        </label>
        <div className="picker-options" role="group" aria-label={title} aria-busy={snapshot.loading}
          onScroll={event=>{const el=event.currentTarget;setFirstVisible(Math.floor(el.scrollTop/76));if(el.scrollHeight-el.scrollTop-el.clientHeight<220)setVisibleCount(count=>Math.min(count+80,filtered.length));}}>
          {filtered.slice(0,visibleCount).map(item=><label className="picker-option" data-selected={selected.includes(item.name)} key={item.name}>
            <input type="checkbox" name={id} value={item.name} checked={selected.includes(item.name)} onChange={()=>choose(item.name)}/>
            <Preview item={item} field={field}/>
            <span className="picker-option-name">{item.title || item.name}{item.collection_label && <small className="picker-owner">{item.collection_label}</small>}</span>
            <span className="picker-option-meta">
              {field==='model' && snapshot.state.collection && item.rarity!=null && <span className="picker-rarity">{number(item.rarity/10)}%</span>}
              {floorFor(item)!=null && snapshot.fx?.rate>0 && <span className="picker-price">от {Math.round(Number(floorFor(item))*snapshot.fx.rate).toLocaleString("ru-RU")} <small>₽</small></span>}
              {floorFor(item)===null && <span className="picker-price">Нет предложений</span>}
            </span>
            <span className="picker-check" aria-hidden="true">{selected.includes(item.name) && <Check size={15}/>}</span>
          </label>)}
          {!filtered.length && <div className="picker-empty" role="status">{snapshot.loading?'Загружаем варианты…':snapshot.error || (query?'Ничего не найдено':'Пока нет вариантов для этой коллекции')}</div>}
        </div>
        <p className="picker-source">{hasMarketFloor && filtered.some(item=>floorFor(item)!=null)?'Минимумы из поиска MRKT · цены в ₽.':snapshot.loading?'Обновляем варианты…':`${number(filtered.length)} вариантов`}</p>
        <div className="picker-panel-actions">
          <button type="button" className="picker-clear" onClick={()=>choose('')}>Очистить</button>
        </div>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>;
}

const sorts=[['newest','Сначала новые'],['price_asc','Сначала дешевле'],['price_desc','Сначала дороже'],['number','Меньший номер'],['number_desc','Больший номер']];
export function CatalogSort() {
  const snapshot=useFilters();
  const [open,setOpen]=useState(false);
  const [active,setActive]=useState(0);
  const trigger=useRef(null),panel=useRef(null);
  const outsideInteraction=useRef(false);
  const id=useId();
  useDropdownCoordinator(id,setOpen,outsideInteraction);
  const value=snapshot.state.sort || 'price_asc';
  const selected=Math.max(0,sorts.findIndex(([key])=>key===value));
  function changeOpen(next) {if(next){announceDropdown(id);outsideInteraction.current=false;setActive(selected)}setOpen(next);}
  function choose(index) {commit('sort',sorts[index][0]);setOpen(false);}
  function keyDown(event) {
    if(event.key==='ArrowDown'||event.key==='ArrowUp') {
      event.preventDefault();setActive(index=>(index+(event.key==='ArrowDown'?1:sorts.length-1))%sorts.length);
    } else if(event.key==='Home'||event.key==='End') {
      event.preventDefault();setActive(event.key==='Home'?0:sorts.length-1);
    } else if(event.key==='Enter'||event.key===' ') {
      event.preventDefault();choose(active);
    }
  }
  return <Popover.Root open={open} onOpenChange={changeOpen}>
    <Popover.Trigger asChild>
      <button ref={trigger} type="button" className="catalog-sort-trigger" aria-label="Сортировка" aria-haspopup="listbox"
        onKeyDown={event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();changeOpen(true)}}}>
        <ArrowUpDown size={18} aria-hidden="true"/><span className="sr-only">{sorts[selected][1]}</span>
      </button>
    </Popover.Trigger>
    <Popover.Portal><Popover.Content ref={panel} className="catalog-sort-panel" sideOffset={8} collisionPadding={12}
      role="listbox" aria-label="Сортировка" tabIndex={0} aria-activedescendant={`${id}-${active}`}
      onKeyDown={keyDown}
      onOpenAutoFocus={event=>{event.preventDefault();panel.current?.focus({preventScroll:true})}}
      onInteractOutside={()=>{outsideInteraction.current=true}}
        onCloseAutoFocus={event=>{event.preventDefault();if(!outsideInteraction.current)trigger.current?.focus({preventScroll:true})}}>
      {sorts.map(([key,label],index)=><div className="catalog-sort-option" role="option" id={`${id}-${index}`} key={key}
        aria-selected={value===key} data-state={value===key?'checked':'unchecked'} data-highlighted={active===index?true:undefined}
        onPointerMove={()=>setActive(index)} onClick={()=>choose(index)}>
        <span>{label}</span>{value===key && <Check size={16} aria-hidden="true"/>}
      </div>)}
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}

export function CatalogBudget() {
  const snapshot=useFilters();
  const [open,setOpen]=useState(false);
  const [draft,setDraft]=useState({min:'',max:''});
  const trigger=useRef(null),first=useRef(null),outsideInteraction=useRef(false);
  const id=useId();useDropdownCoordinator(id,setOpen,outsideInteraction);
  const error=draft.min && draft.max && Number(draft.min)>Number(draft.max)?'Цена «От» должна быть меньше цены «До».':'';
  function changeOpen(next) {
    if(next){announceDropdown(id);outsideInteraction.current=false;setDraft({min:snapshot.state.min_price,max:snapshot.state.max_price})}
    setOpen(next);
  }
  function change(key,value) {
    setDraft(previous=>({...previous,[key]:value}));
    const input=document.getElementById(key==='min'?'min-price':'max-price');
    input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));
  }
  function clear() {
    setDraft({min:'',max:''});
    document.getElementById('min-price').value='';document.getElementById('max-price').value='';
    document.getElementById('max-price').dispatchEvent(new Event('change',{bubbles:true}));
  }
  return <Popover.Root open={open} onOpenChange={changeOpen}>
    <Popover.Trigger asChild><button ref={trigger} type="button" className="budget-trigger" data-active={!!(snapshot.state.min_price||snapshot.state.max_price)}>Бюджет<ChevronDown size={14}/></button></Popover.Trigger>
    <Popover.Portal><Popover.Content className="budget-panel" sideOffset={10} collisionPadding={12} aria-label="Бюджет"
      onOpenAutoFocus={event=>{event.preventDefault();first.current?.focus({preventScroll:true})}}
      onInteractOutside={()=>{outsideInteraction.current=true}}
      onCloseAutoFocus={event=>{event.preventDefault();if(!outsideInteraction.current)trigger.current?.focus({preventScroll:true})}}>
      <div className="budget-title">Цена, ₽<Popover.Close aria-label="Закрыть бюджет"><X size={17}/></Popover.Close></div>
      <div className="budget-inputs"><label>От<input ref={first} type="number" min="0" max="1000000000" step="1" value={draft.min} onChange={event=>change('min',event.target.value)} placeholder="0" disabled={!snapshot.fx?.rate}/></label>
      <label>До<input type="number" min="0" max="1000000000" step="1" value={draft.max} onChange={event=>change('max',event.target.value)} placeholder="Любая" disabled={!snapshot.fx?.rate}/></label></div>
      {error && <p className="budget-error" role="alert">{error}</p>}
      <button type="button" className="budget-clear" onClick={clear}>Сбросить бюджет</button>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}

export function ActiveFilters() {
  const snapshot=useFilters();
  const chips=[];
  for(const field of ['collection','model','backdrop','symbol']) {
    for(const value of selections(snapshot.state[field])) {
      const option=snapshot.data?.[field]?.find(item=>item.name===value);
      const label=field==='model' && value.includes('::model::') ? value.split('::model::').join(' · ') : option?.title || value;
      chips.push({field,value,label});
    }
  }
  if(snapshot.state.min_price)chips.push({field:'min_price',label:`От ${Number(snapshot.state.min_price).toLocaleString('ru-RU')} ₽`});
  if(snapshot.state.max_price)chips.push({field:'max_price',label:`До ${Number(snapshot.state.max_price).toLocaleString('ru-RU')} ₽`});
  function remove(chip) {
    if(chip.field==='min_price'||chip.field==='max_price') {
      const input=document.getElementById(chip.field==='min_price'?'min-price':'max-price');input.value='';input.dispatchEvent(new Event('change',{bubbles:true}));
    } else commit(chip.field,encoded(selections(snapshot.state[chip.field]).filter(value=>value!==chip.value)));
  }
  if(!chips.length)return null;
  return <div className="applied-filters" aria-label="Применённые фильтры">
    {chips.map(chip=><button type="button" key={chip.field+':'+(chip.value || '')} className="applied-filter" aria-label={`Убрать: ${chip.label}`} onClick={()=>remove(chip)}><span>{chip.label}</span><X size={12}/></button>)}
    <button type="button" className="applied-reset" onClick={()=>document.getElementById('reset').click()}>Сбросить всё</button>
  </div>;
}
