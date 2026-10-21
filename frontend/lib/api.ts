import createClient from "openapi-fetch";
import type { paths, components } from "./openapi";
export const typedClient = createClient<paths>({ baseUrl: "" });
export type SettingsInput = components["schemas"]["Settings"];
export type StrategyInput = components["schemas"]["Strategy"];
export type PairInput = components["schemas"]["Pair"];
export type Row = { id: string; [key: string]: any };
let csrf = "";
export function setCsrf(value: string) { csrf = value; }
export async function api<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  const form = body instanceof FormData;
  if (body !== undefined && !form) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : form ? body : JSON.stringify(body) });
  } catch {
    throw new Error("Нет связи с сервером. Проверьте, запущено ли приложение.");
  }
  const text = await response.text();
  let data: any;
  try { data = text ? JSON.parse(text) : null; }
  catch {
    throw new Error(response.status >= 500
      ? `Сервер временно недоступен (HTTP ${response.status}). Обновите страницу. Если сохраняли изменения — проверьте, применились ли они.`
      : `Сервер вернул некорректный ответ (HTTP ${response.status}). Обновите страницу.`);
  }
  if (!response.ok && !data) throw new Error(`Запрос не выполнен (HTTP ${response.status}). Обновите страницу.`);
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Проверьте заполнение полей");
  return data as T;
}
export async function waitTask(id: string) {
  for (let i = 0; i < 240; i++) {
    const task = await api("/tasks/" + id);
    if (task.state === "COMPLETE") return task.result;
    if (task.state === "FAILED") throw new Error(task.error || "Задание не выполнено");
    await new Promise(resolve => setTimeout(resolve, 750));
  }
  throw new Error("Задание продолжает работать в фоне. Проверьте историю операций.");
}
