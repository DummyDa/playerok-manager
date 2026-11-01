import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import BounceCards from './BounceCards.jsx';
import DotGrid from './DotGrid.jsx';
import './hero-grid.css';
import { CatalogSelect, CatalogSort, CatalogBudget, ActiveFilters } from '../components/ui/catalog-select.jsx';
function HeroDots() {
  const query = '(hover:hover) and (pointer:fine) and (prefers-reduced-motion:no-preference)';
  const [interactive, setInteractive] = useState(matchMedia(query).matches);
  useEffect(() => {
    const media = matchMedia(query);
    const update = () => setInteractive(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return <DotGrid dotSize={3} gap={24} baseColor="#8DADDF" activeColor="#8DADDF"
    proximity={160} strength={.12} bounce={.3} tension={.4} returnDuration={.9}
    opacity={.3} swell={.2} stretch={.15} glow={0} fade={.3} intro={false}
    mouseInteraction={interactive} clickShock={interactive} shockRadius={180} shockStrength={1.5}/>;
}
const hero = document.querySelector('.hero');
if (hero) {
  const mount = document.createElement('div');
  mount.className = 'hero-dots'; mount.setAttribute('aria-hidden', 'true');
  hero.prepend(mount);
  const root = createRoot(mount); root.render(<HeroDots/>);
  window.addEventListener('pagehide', event => { if (!event.persisted) root.unmount(); }, { once:true });
}
function HeroCards({ container }) {
  const [cards,setCards]=useState(window.giftroomHeroCards || [
    {name:'Toy Bear',image:'/hero-toy-bear.webp'},
    {name:'Plush Pepe',image:'/hero-plush-pepe.webp'},
    {name:'Heart Locket',image:'/hero-heart-locket.webp'}
  ]);
  const [size,setSize]=useState({width:container.clientWidth,height:container.clientHeight,viewport:innerWidth});
  const [reduced,setReduced]=useState(matchMedia('(prefers-reduced-motion:reduce)').matches);
  const [hover,setHover]=useState(matchMedia('(hover:hover) and (pointer:fine)').matches);
  useEffect(()=>{
    const ready=event=>setCards(event.detail);
    window.addEventListener('hero-cards-ready',ready);
    const observer=new ResizeObserver(()=>setSize({width:container.clientWidth,height:container.clientHeight,viewport:innerWidth}));
    observer.observe(container);
    const motion=matchMedia('(prefers-reduced-motion:reduce)'),mouse=matchMedia('(hover:hover) and (pointer:fine)');
    const change=()=>{setReduced(motion.matches);setHover(mouse.matches)};
    motion.addEventListener('change',change);mouse.addEventListener('change',change);
    return()=>{window.removeEventListener('hero-cards-ready',ready);observer.disconnect();motion.removeEventListener('change',change);mouse.removeEventListener('change',change)};
  },[container]);
  const profile=size.viewport<=390?[125,185,92]:size.viewport<=760?[137,197,106]:size.viewport<=1100?[144,209,116]:[170,234,151];
  const [width,height,spread]=profile;
  const colors=[['#947ab2','#4c3e64'],['#b1c994','#637e50'],['#e4b887','#92704c']];
  return <>
    <div className="orbit" aria-hidden="true"/>
    {cards.length>0 && <BounceCards key={reduced?'still':'bounce'}
      className="hero-bounce" images={cards.map(c=>({src:c.image||'/icon.svg',alt:c.title||c.name}))}
      containerWidth={size.width} containerHeight={size.height} cardSize={width} cardHeight={height}
      transformStyles={[`rotate(-13deg) translate(-${spread}px, 12px)`,`rotate(3deg) translate(0px, -7px)`,`rotate(15deg) translate(${spread}px, 17px)`]}
      pushDistance={size.viewport<=760?25:55} hoverScale={1.06} bounciness={.6}
      animationDelay={.15} animationStagger={.08} enableHover={hover&&!reduced}
      borderWidth={1} borderColor="#7b9cbd66" radius={11} shadow
      style={{'--hero-card-height':`${height}px`}}
      renderCard={(card,index)=><>
        <div className="showcase-img" style={{'--c1':colors[index][0],'--c2':colors[index][1]}}><img src={card.src} alt={card.alt} draggable={false} loading="eager" fetchPriority="high" decoding="async"/></div>
        <strong>{card.alt}</strong><small>ynitaz456 SELECT / 00{index+1}</small>
      </>}
    />}
    <span className="art-caption">OBJECTS WORTH COLLECTING / 001</span>
  </>;
}
const heroArt=document.querySelector('#hero-art');
if(heroArt){
  const root=createRoot(heroArt);root.render(<HeroCards container={heroArt}/>);
  window.addEventListener('pagehide',event=>{if(!event.persisted)root.unmount()},{once:true});
}

// Upgrade existing controls without replacing the catalogue's data flow.

const collectionLegacy=document.querySelector('.collection-filter');
if(collectionLegacy) collectionLegacy.classList.add('legacy-collection-filter');
['collection','model','backdrop','symbol'].forEach(field=>{
  const select=document.getElementById(field);
  const legacy=field==='collection'?collectionLegacy:select?.closest('label');
  if(!legacy) return;
  const mount=document.createElement('div');mount.className='filter-picker-mount';
  legacy.before(mount);
  if(field!=='collection') legacy.classList.add('legacy-picker');
  const root=createRoot(mount);root.render(<CatalogSelect field={field}/>);
  window.addEventListener('pagehide',event=>{if(!event.persisted)root.unmount()},{once:true});
});
const oldSort=document.querySelector('.sort-label');
if(oldSort){
  const mount=document.createElement('div');mount.className='sort-picker-mount';oldSort.before(mount);oldSort.classList.add('legacy-picker');
  const root=createRoot(mount);root.render(<CatalogSort/>);
  window.addEventListener('pagehide',event=>{if(!event.persisted)root.unmount()},{once:true});
}

const searchRow=document.querySelector('.search-row');
if(searchRow){
  const toolbar=document.createElement('div');toolbar.className='catalog-toolbar';searchRow.before(toolbar);
  const search=searchRow.querySelector('.search'),grid=document.querySelector('#view-grid'),sort=searchRow.querySelector('.sort-picker-mount');
  toolbar.append(search,grid,sort);
  const divider=document.createElement('span');divider.className='toolbar-divider';divider.setAttribute('aria-hidden','true');toolbar.append(divider);
  const budget=document.createElement('div');budget.className='budget-mount';toolbar.append(budget);
  const root=createRoot(budget);root.render(<CatalogBudget/>);
  document.querySelectorAll('.filter-picker-mount').forEach(mount=>toolbar.append(mount));
  window.addEventListener('pagehide',event=>{if(!event.persisted)root.unmount()},{once:true});
}

const toolbarForChips=document.querySelector('.catalog-toolbar');
if(toolbarForChips){
 const mount=document.createElement('div');mount.className='active-filters-mount';toolbarForChips.after(mount);
 const root=createRoot(mount);root.render(<ActiveFilters/>);
 window.addEventListener('pagehide',event=>{if(!event.persisted)root.unmount()},{once:true});
}
