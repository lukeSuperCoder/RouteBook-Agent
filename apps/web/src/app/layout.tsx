import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "RouteBook Agent · 运行台",
  description: "路书 Agent 一期工程与业务核心状态",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
