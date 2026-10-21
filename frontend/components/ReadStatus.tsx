"use client";
import { useEffect, useState } from "react";
import { Notice } from "./ui";

/** Preserve cached content through transient poll failures; writes report their own errors. */
export default function ReadStatus({ error, cached = false }: { error: any; cached?: boolean }) {
  const failed = Boolean(error);
  const [persistent, setPersistent] = useState(false);
  useEffect(() => {
    setPersistent(false);
    if (!failed) return;
    const timer = setTimeout(() => setPersistent(true), 10000);
    return () => clearTimeout(timer);
  }, [failed]);
  if (!failed || (cached && !persistent)) return null;
  if (cached) return <p className="sync-status muted" role="status">Восстанавливаем связь с сервером. Пока показаны последние полученные данные.</p>;
  return <Notice error>{error.message}</Notice>;
}
