"use client";

import { useUser } from "@/lib/api/hooks"
import { PrimaryButton, SecondaryButton } from "@/components/ui/button"
import { MessageSquare, Mail, User } from "lucide-react"
import Link from "next/link"

export default function HelpPage() {
  const { data: user } = useUser();

  return (
    <div className="space-y-8 animate-in fade-in duration-500 max-w-3xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Help & Support</h1>
        <p className="text-muted-foreground mt-1">Get assistance with your Alerta account.</p>
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <div className="bg-card border rounded-xl p-6 shadow-sm flex flex-col h-full">
          <div className="h-10 w-10 flex items-center justify-center rounded-lg bg-primary/10 text-primary mb-4">
            <MessageSquare className="h-5 w-5" />
          </div>
          <h3 className="text-lg font-semibold mb-2">Telegram Bot Help</h3>
          <p className="text-sm text-muted-foreground mb-6 flex-1">
            Not receiving alerts? Make sure you have started the bot. The easiest way is to click the Open Bot link and send /start.
          </p>
          <Link href="/bot" target="_blank">
            <PrimaryButton className="w-full gap-2">
              <MessageSquare className="h-4 w-4" />
              Open Bot
            </PrimaryButton>
          </Link>
        </div>

        <div className="bg-card border rounded-xl p-6 shadow-sm flex flex-col h-full">
          <div className="h-10 w-10 flex items-center justify-center rounded-lg bg-secondary text-secondary-foreground mb-4">
            <Mail className="h-5 w-5" />
          </div>
          <h3 className="text-lg font-semibold mb-2">Contact Support</h3>
          <p className="text-sm text-muted-foreground mb-6 flex-1">
            Need advanced help or experiencing a bug? Our support team is ready to assist you.
          </p>
          <Link href="mailto:support@alerta.com">
            <SecondaryButton className="w-full gap-2">
              <Mail className="h-4 w-4" />
              Contact Support
            </SecondaryButton>
          </Link>
        </div>
      </div>

      <div className="bg-card border rounded-xl p-6 shadow-sm flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold">Account Context</h3>
          <p className="text-sm text-muted-foreground mt-1">
            Logged in as {user?.email || "loading..."}
          </p>
        </div>
        <Link href="/app/settings">
          <SecondaryButton className="gap-2">
            <User className="h-4 w-4" />
            For My Account
          </SecondaryButton>
        </Link>
      </div>
    </div>
  )
}
