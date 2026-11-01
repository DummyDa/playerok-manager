"use strict";
const grid=document.querySelector('#grid'),status=document.querySelector('#status'),more=document.querySelector('#more'),refresh=document.querySelector('#refresh');
const money=value=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(value)+' ₽';
let cursor=null,busy=false,count=0;const seen=new Set();
function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=text;return node;}
function card(item){
  const link=element('a','card');link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';
  const picture=element('div','picture');
  if(item.image){const image=element('img');image.src=item.image;image.alt='';image.loading='lazy';image.decoding='async';image.addEventListener('error',()=>image.remove(),{once:true});picture.append(image);}
  link.append(picture);
  const prices=element('div','prices');prices.append(element('span','price',money(item.price)));
  if(item.old_price>item.price){prices.append(element('span','discount','−'+Math.round((1-item.price/item.old_price)*100)+'%'),element('span','old',money(item.old_price)));}
  link.append(prices,element('div','title',item.title));
  if(item.rating!=null || item.reviews!=null){const reviews=element('div','reviews');
    if(item.rating!=null){const stars=element('span','stars','★'.repeat(Math.round(item.rating))+'☆'.repeat(5-Math.round(item.rating)));stars.setAttribute('aria-label','Рейтинг '+item.rating+' из 5');reviews.append(stars);}
    if(item.reviews!=null)reviews.append(element('span','',new Intl.NumberFormat('ru-RU').format(item.reviews)));
    reviews.title=item.seller;link.append(reviews);
  }
  return link;
}
async function load(reset=false){
  if(busy)return;busy=true;more.disabled=true;refresh.disabled=true;grid.setAttribute('aria-busy','true');status.textContent='Загружаем объявления…';
  try{
    const response=await fetch('/api/lots'+(!reset&&cursor?'?cursor='+encodeURIComponent(cursor):''));const data=await response.json();
    if(!response.ok)throw new Error(data.error||'Не удалось загрузить объявления');
    if(reset){grid.replaceChildren();seen.clear();count=0;}
    for(const item of data.items){if(seen.has(item.id))continue;seen.add(item.id);grid.append(card(item));count++;}
    cursor=data.next_cursor;more.hidden=!cursor;
    status.textContent=count?'Показано '+count+' · только обычные лоты':cursor?'На этой странице обычных лотов нет. Можно загрузить следующую.':'Обычных лотов сейчас нет.';
  }catch(error){status.textContent=error.message;if(!count)more.hidden=true;}
  finally{busy=false;more.disabled=false;refresh.disabled=false;grid.setAttribute('aria-busy','false');}
}
more.addEventListener('click',()=>load());refresh.addEventListener('click',()=>load(true));load(true);
