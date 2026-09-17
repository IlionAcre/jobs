"use client";

import { useEffect } from "react";
import Link from "next/link";
import { PrimaryButton } from "@/components/ui/button";
import { MessageSquare } from "lucide-react";

export default function BotRedirectPage() {
  const BOT_USERNAME = process.env.NEXT_PUBLIC_BOT_USERNAME || "PlaceholderAlertaBot";
  const botUrl = `https://t.me/${BOT_USERNAME}?start=web`;

  useEffect(() => {
    // Attempt redirect
    window.location.replace(botUrl);
  }, [botUrl]);

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4">
      <div className="text-center space-y-6 max-w-md w-full animate-in fade-in zoom-in-95 duration-300">
        <div className="mx-auto h-20 w-20 bg-primary/10 text-primary rounded-full flex items-center justify-center mb-4">
          <MessageSquare className="h-10 w-10 relative left-[-2px] top-[2px]" />
        </div>
        
        <h1 className="text-2xl font-bold">Opening Telegram...</h1>
        <p className="text-muted-foreground">
          You are being redirected to our Telegram bot to start receiving alerts.
        </p>

        <div className="pt-8">
          <Link href={botUrl} target="_blank" rel="noopener noreferrer" className="w-full">
            <PrimaryButton className="w-full gap-2">
              <MessageSquare className="h-4 w-4" />
              If not redirected, tap here
            </PrimaryButton>
          </Link>
        </div>
        
        <div className="pt-4">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground hover:underline transition-colors">
            Return to home
          </Link>
        </div>
      </div>
    </div>
  );
}
