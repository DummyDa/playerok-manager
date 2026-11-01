import scene from './glass-shards.json';

// Physics and artwork from the supplied glass_shards_interactive.html.
export function mountGlassShards(hero) {
  const layer = document.createElement('div');
  layer.className = 'hero-glass-shards';
  layer.setAttribute('aria-hidden', 'true');
  const plane = document.createElement('div');
  plane.className = 'glass-shards-plane';
  layer.append(plane);
  hero.prepend(layer);
  const image = (src, className) => {
    const el = document.createElement('img');
    el.className = className;
    el.alt = '';
    el.draggable = false;
    el.src = src;
    plane.append(el);
    return el;
  };
  const back = image(scene.background, 'glass-shards-back');
  const objects = scene.shards.map(spec => {
    const el = image(spec.src, 'glass-shard');
    el.style.cssText = `left:${spec.x / 2048 * 100}%;top:${spec.y / 682 * 100}%;width:${spec.w / 2048 * 100}%;height:${spec.h / 682 * 100}%`;
    return {...spec, el, cx:spec.x+spec.w/2, cy:spec.y+spec.h/2, dx:0, dy:0, vx:0, vy:0, rot:0, vr:0};
  });
  let alive = true, ready = false, visible = false, raf = 0, last = 0, scale = 1;
  const pointer = {x:-9999,y:-9999};
  const motion = matchMedia('(prefers-reduced-motion:reduce)');
  const mouse = matchMedia('(hover:hover) and (pointer:fine) and (min-width:761px)');
  const allowed = () => alive && ready && visible && !document.hidden && !motion.matches && mouse.matches;
  function wake() {
    if (allowed() && !raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
  }
  function frame(now) {
    raf = 0;
    if (!allowed()) return;
    const dt = Math.min(.034, (now-last)/1000 || .016);
    last = now;
    const k = 13, force = .48, radius = 210, damping = 2*Math.sqrt(k)*.92;
    let active = false;
    for (const s of objects) {
      const rx=s.cx+s.dx-pointer.x, ry=s.cy+s.dy-pointer.y, dist=Math.hypot(rx,ry);
      let tx=0,ty=0,tr=0;
      if (dist<radius && dist>1) {
        const falloff=Math.pow(1-dist/radius,1.7), magnitude=force*48*s.depth*falloff;
        tx=rx/dist*magnitude;ty=ry/dist*magnitude;tr=(rx>0?1:-1)*force*8*falloff*s.depth;
      }
      s.vx+=(k*(tx-s.dx)-damping*s.vx)*dt;
      s.vy+=(k*(ty-s.dy)-damping*s.vy)*dt;
      s.vr+=(k*(tr-s.rot)-damping*s.vr)*dt;
      s.dx+=s.vx*dt;s.dy+=s.vy*dt;s.rot+=s.vr*dt;
      const settled = Math.abs(tx-s.dx)+Math.abs(ty-s.dy)+Math.abs(tr-s.rot)<.015 && Math.abs(s.vx)+Math.abs(s.vy)+Math.abs(s.vr)<.02;
      if (settled) {s.dx=tx;s.dy=ty;s.rot=tr;s.vx=s.vy=s.vr=0;} else active=true;
      s.el.style.transform=`translate(${s.dx*scale}px,${s.dy*scale}px) rotate(${s.rot}deg)`;
    }
    if (active) raf=requestAnimationFrame(frame);
  }
  function leave() { pointer.x=pointer.y=-9999;wake(); }
  function move(event) {
    if (!allowed() || event.pointerType==='touch') return;
    const rect=plane.getBoundingClientRect();
    pointer.x=(event.clientX-rect.left)/scale;
    pointer.y=(event.clientY-rect.top)/scale;
    wake();
  }
  function resize() {
    scale=Math.max(hero.clientWidth/2048,hero.clientHeight/682);
    plane.style.width=`${2048*scale}px`;
    plane.style.height=`${682*scale}px`;
    leave();
  }
  function reset() {
    if (raf) cancelAnimationFrame(raf);
    raf=0;pointer.x=pointer.y=-9999;
    for (const s of objects) {
      s.dx=s.dy=s.vx=s.vy=s.rot=s.vr=0;
      s.el.style.transform='translate(0px,0px) rotate(0deg)';
    }
  }
  function visibility() { if (!allowed()) reset(); else wake(); }
  const resizeObserver=new ResizeObserver(resize);
  resizeObserver.observe(hero);
  const observer=new IntersectionObserver(([entry])=>{visible=entry.isIntersecting;visibility();});
  observer.observe(hero);
  hero.addEventListener('pointermove',move,{passive:true});
  hero.addEventListener('pointerleave',leave);
  window.addEventListener('blur',leave);
  window.addEventListener('scroll',leave,{passive:true});
  document.addEventListener('visibilitychange',visibility);
  motion.addEventListener('change',visibility);
  mouse.addEventListener('change',visibility);
  resize();
  Promise.all([back,...objects.map(s=>s.el)].map(el=>el.decode())).then(()=>{
    if (!alive) return;
    ready=true;hero.classList.add('hero-shards-ready');wake();
  }).catch(()=>{/* Keep the static image if an animation asset fails. */});
  return () => {
    alive=false;reset();resizeObserver.disconnect();observer.disconnect();
    hero.removeEventListener('pointermove',move);hero.removeEventListener('pointerleave',leave);
    window.removeEventListener('blur',leave);window.removeEventListener('scroll',leave);
    document.removeEventListener('visibilitychange',visibility);
    motion.removeEventListener('change',visibility);mouse.removeEventListener('change',visibility);
    hero.classList.remove('hero-shards-ready');layer.remove();
  };
}
