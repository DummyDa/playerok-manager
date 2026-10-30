"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

export default function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    function sync() {
      let saved: string | null = null;
      try { saved = localStorage.getItem("playerok.theme"); } catch {}
      const value = saved === "dark" || (saved !== "light" && media.matches);
      document.documentElement.dataset.theme = value ? "dark" : "light";
      setDark(value);
    }
    sync();
    media.addEventListener("change", sync);
    window.addEventListener("storage", sync);
    return () => { media.removeEventListener("change", sync); window.removeEventListener("storage", sync); };
  }, []);
  const label = dark ? "Включить светлую тему" : "Включить тёмную тему";
  return <button type="button" className="icon-button theme-toggle" aria-label={label} title={label} onClick={() => {
    const value = !dark;
    document.documentElement.dataset.theme = value ? "dark" : "light";
    try { localStorage.setItem("playerok.theme", value ? "dark" : "light"); } catch {}
    setDark(value);
  }}>{dark ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}</button>;
}
