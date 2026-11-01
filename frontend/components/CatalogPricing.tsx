"use client";
import useSWR from "swr";
import { api } from "../lib/api";
import { Panel, Field, Notice } from "./ui";

export default function CatalogPricing({values,update}:{values:Record<string,any>;update:(key:string,value:unknown)=>void}) {
  const profiles=useSWR("/fee-profiles",path=>api(path));
  return <Panel title="Цена продажи в каталоге" subtitle="Целевая чистая прибыль после закупки, комиссий и 25 Stars. Сохраняется общей кнопкой настроек."><div className="form-pad"><div className="form-grid">
    <Field label="Прибыль каталога"><input type="number" min={0} step="0.01" value={values.catalog_profit_value ?? "10"} onChange={e=>update("catalog_profit_value",e.target.value)} /></Field>
    <Field label="Единица прибыли каталога"><select value={values.catalog_profit_type ?? "percent"} onChange={e=>update("catalog_profit_type",e.target.value)}><option value="percent">% от закупочной стоимости NFT</option><option value="rub">Рубли на продажу</option></select></Field>
    <Field label="Профиль комиссий каталога"><select value={values.catalog_fee_profile_id ?? "default"} onChange={e=>update("catalog_fee_profile_id",e.target.value)}>{(profiles.data?.items || []).map((p:any)=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
  </div><Field label="Название лота из каталога" help="Переменные: {collection}, {number}, {model}, {backdrop}, {symbol}. Описание берётся из общего шаблона описания новых лотов."><input value={values.catalog_title_template ?? "🎁 {collection} #{number} | {model} | ⚡ АВТОВЫДАЧА"} onChange={e=>update("catalog_title_template",e.target.value)} /></Field><Notice>10% означает прибыль от закупочной стоимости, а не прибавку к цене MRKT. Цена округляется вверх до рубля. Курс TON и стоимость Star берутся из общих настроек. Премиум-продвижение в каталоге не учитывается.</Notice></div></Panel>;
}
