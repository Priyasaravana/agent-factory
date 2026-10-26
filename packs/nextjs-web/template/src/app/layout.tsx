import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { Toaster } from "@/components/ui/toast";
import { auth, currentUser } from "@/modules/auth";

import { APP_NAME, LOOK } from "./look";
import "./globals.css";

export const metadata: Metadata = {
  title: APP_NAME,
  description: "Built by Agent Factory",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const signInEnabled = auth().enabled;
  const user = signInEnabled ? await currentUser() : null;
  return (
    <html lang="en" data-look={LOOK}>
      <body>
        <AppShell user={user} signInEnabled={signInEnabled}>
          {children}
        </AppShell>
        <Toaster />
      </body>
    </html>
  );
}
