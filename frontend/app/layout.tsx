import type { Metadata } from "next";
import "./globals.css";
import Dashboard from "../components/Dashboard";
export const metadata: Metadata = { title: "Playerok · Gift Manager", description: "Управление Telegram NFT Gifts и продажами Playerok" };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body><Dashboard />{children}</body></html>;
}
