import lottie from 'lottie-web/build/player/lottie_light';
const cache=new Map();
export function preloadAnimation(id,slug,model) {
  const valid=/^[a-z0-9]+-\d+$/i.test(slug || '') && !/^mythmask-\d+$/i.test(slug || '');
  const myth=/^mythmask-\d+$/i.test(slug || '');
  const key=myth?'myth-mask:'+model:valid?slug.toLowerCase():id;
  if(!cache.has(key)) {
    const local=`/api/animation/${encodeURIComponent(id)}`;
    const read=async url=>{const r=await fetch(url);if(!r.ok)throw new Error();return r.json()};
    const directMyth=async()=>{
      const value=await read('https://api.changes.tg/model/Myth%20Mask/'+encodeURIComponent(model)+'.json');
      if(!Array.isArray(value.layers)||!value.w||!value.h||(value.assets||[]).some(asset=>asset.p||asset.u))throw new Error('Invalid animation');
      return value;
    };
    const task=(myth?directMyth():valid?read(`https://nft.fragment.com/gift/${key}.lottie.json`).catch(()=>read(local)):read(local))
      .catch(error=>{cache.delete(key);throw error});
    cache.set(key,task);
    while(cache.size>8)cache.delete(cache.keys().next().value);
  }
  return cache.get(key);
}
export function mountAnimation(target,id) {
  let stopped=false,player;
  const layer=document.createElement('div');layer.className='nft-animation';layer.setAttribute('aria-hidden','true');
  layer.style.cssText='inset:0;width:100%;height:100%;margin:0;aspect-ratio:auto';target.append(layer);
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  const visibility=()=>{if(!player)return;if(document.hidden||reduced.matches)player.pause();else player.play()};
  preloadAnimation(id,target.dataset.slug,target.dataset.model).then(data=>{
    if(stopped||!target.isConnected)return;
    player=lottie.loadAnimation({container:layer,renderer:'svg',loop:true,autoplay:false,animationData:structuredClone(data),rendererSettings:{preserveAspectRatio:'xMidYMid meet',progressiveLoad:true}});
    player.addEventListener('DOMLoaded',()=>{if(stopped)return;target.classList.add('nft-ready');visibility()});
  }).catch(()=>{});
  document.addEventListener('visibilitychange',visibility);reduced.addEventListener('change',visibility);
  return()=>{stopped=true;player?.destroy();layer.remove();target.classList.remove('nft-ready');document.removeEventListener('visibilitychange',visibility);reduced.removeEventListener('change',visibility)};
}
