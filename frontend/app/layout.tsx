import type { Metadata } from "next";
import "./globals.css";
import Dashboard from "../components/Dashboard";
export const metadata: Metadata = { title: "Playerok · Gift Manager", description: "Управление Telegram NFT Gifts и продажами Playerok" };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: `(function(){var t;try{t=localStorage.getItem('playerok.theme')}catch(e){}document.documentElement.dataset.theme=t==='dark'||(t!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light'})()` }} /></head><body><Dashboard />{children}</body></html>;
}
