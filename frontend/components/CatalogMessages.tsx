"use client";
import {Field,Notice,Panel} from "./ui";

export default function CatalogMessages({values,update}:{values:Record<string,any>;update:(key:string,value:unknown)=>void}){
  return <Panel title="Сообщения покупателям каталога" subtitle="Отправляются в чаты Playerok после оплаты рекламного лота и доплаты. Ответ после выдачи берётся из «Настройки → Ответы покупателю». Сохраняются кнопкой настроек каталога."><div className="form-pad">
    <Field label="Адрес каталога для покупателей" help="Публичный адрес — HTTPS. Для локальной проверки можно http://127.0.0.1:3180, но у покупателя на другом компьютере он не откроется."><input type="url" value={values.catalog_public_url || ""} placeholder="https://catalog.example.com" onChange={e=>update("catalog_public_url",e.target.value)}/></Field>
    <Field label="После оплаты рекламного лота" help="Подстановки: {amount}, {recipient}, {url}, {code}, {balance}, {delivery_notice}. Вставьте ссылку и код, чтобы покупатель смог выбрать подарок."><textarea rows={8} value={values.catalog_promo_paid_template || ""} maxLength={4000} onChange={e=>update("catalog_promo_paid_template",e.target.value)}/></Field>
    <Field label="После оплаты доплаты" help="Подстановки: {amount}, {recipient}, {gift}, {balance}, {delivery_notice}. При поздней или неподтверждённой оплате бот отправляет отдельное сообщение о проверке."><textarea rows={5} value={values.catalog_topup_paid_template || ""} maxLength={4000} onChange={e=>update("catalog_topup_paid_template",e.target.value)}/></Field>
    <Field label="После выбора подарка — ожидание выдачи" help="Отправляется один раз, когда выбор подтверждён и оплаты достаточно. Если нужна доплата — после её оплаты. Подстановки: {gift}, {recipient}, {delivery_notice}, {balance}."><textarea rows={5} value={values.catalog_selected_template || ""} maxLength={4000} onChange={e=>update("catalog_selected_template",e.target.value)}/></Field>
    <Notice>После оплаты рекламного лота покупатель получает баланс и выбирает подарок по коду. Ссылку на доплату бот отправляет после публикации лота, а сообщение о выдаче — после подтверждённой передачи NFT. Пустой шаблон использует стандартный текст.</Notice>
  </div></Panel>;
}
