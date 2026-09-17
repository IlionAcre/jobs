import * as React from "react"
import Link from "next/link"
import { PrimaryButton, SecondaryButton } from "@/components/ui/button"
import { ThemeToggle } from "@/components/theme-toggle"
import { Zap } from "lucide-react"

export default function PublicLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container mx-auto max-w-6xl px-4 flex h-16 items-center justify-between">
          <Link href="/" className="flex items-center gap-2 font-bold text-lg">
            <Zap className="h-6 w-6 text-primary" />
            <span>Alerta</span>
          </Link>
          
          <nav className="hidden md:flex items-center gap-6 text-sm font-medium">
            <Link href="/pricing" className="text-muted-foreground hover:text-foreground transition-colors">Pricing</Link>
            <Link href="/bot" className="text-muted-foreground hover:text-foreground transition-colors">Open Bot</Link>
          </nav>

          <div className="flex items-center gap-4">
            <ThemeToggle />
            <Link href="/login" className="hidden sm:inline-block text-sm font-medium text-muted-foreground hover:text-foreground">
              Sign In
            </Link>
            <Link href="/signup">
              <PrimaryButton size="sm">Start Free Trial</PrimaryButton>
            </Link>
          </div>
        </div>
      </header>
      
      <main className="flex-1 flex flex-col">
        {children}
      </main>

      {/* Sticky Mobile CTA Bar */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 border-t bg-background p-4 z-40 flex gap-3 shadow-[0_-10px_40px_rgba(0,0,0,0.1)]">
        <Link href="/bot" className="flex-1">
          <SecondaryButton className="w-full">Open Bot</SecondaryButton>
        </Link>
        <Link href="/signup" className="flex-[2]">
          <PrimaryButton className="w-full">Start Free Trial</PrimaryButton>
        </Link>
      </div>

      <footer className="border-t bg-muted/20 py-8 text-center text-sm text-muted-foreground pb-24 md:pb-8">
        <div className="container mx-auto px-4">
          <p>&copy; {new Date().getFullYear()} Alerta SaaS. All rights reserved.</p>
        </div>
      </footer>
    </div>
  )
}
