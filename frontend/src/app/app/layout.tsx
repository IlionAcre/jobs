"use client"

import * as React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import { PrimaryButton } from "@/components/ui/button"
import { ThemeToggle } from "@/components/theme-toggle"
import { 
  Zap, 
  LayoutDashboard, 
  ListOrdered, 
  CreditCard, 
  Settings, 
  LifeBuoy,
  MessageSquare,
  User,
  Menu
} from "lucide-react"

const navItems = [
  { href: "/app", label: "Dashboard", icon: LayoutDashboard },
  { href: "/app/queries", label: "Queries", icon: ListOrdered },
  { href: "/app/billing", label: "Billing", icon: CreditCard },
  { href: "/app/settings", label: "Settings", icon: Settings },
  { href: "/app/help", label: "Help", icon: LifeBuoy },
]

export default function AppLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname();
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);

  return (
    <div className="min-h-screen flex flex-col md:flex-row bg-background">
      {/* Desktop Sidebar */}
      <aside className="hidden md:flex w-64 flex-col border-r bg-card/50 px-4 py-6">
        <div className="flex items-center justify-between mb-8 px-2">
          <Link href="/app" className="flex items-center gap-2 font-bold text-xl">
            <Zap className="h-6 w-6 text-primary" />
            <span>Alerta</span>
          </Link>
          <ThemeToggle />
        </div>
        <nav className="flex-1 space-y-1">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive 
                    ? "bg-primary/10 text-primary" 
                    : "text-muted-foreground hover:bg-accent hover:text-foreground"
                )}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </Link>
            )
          })}
        </nav>
        
        <div className="mt-auto flex flex-col gap-4 pt-4 border-t">
          <Link href="/bot" target="_blank" className="w-full">
            <PrimaryButton className="w-full justify-start gap-2" variant="outline">
              <MessageSquare className="h-4 w-4 text-primary" />
              Open Bot
            </PrimaryButton>
          </Link>
          <Link href="/app/settings" className="flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-foreground">
            <User className="h-4 w-4" />
            For My Account
          </Link>
        </div>
      </aside>

      {/* Mobile Header & Content */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="md:hidden sticky top-0 z-40 flex h-14 items-center justify-between border-b bg-background px-4">
          <Link href="/app" className="flex items-center gap-2 font-bold text-lg">
            <Zap className="h-5 w-5 text-primary" />
            <span>Alerta</span>
          </Link>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <button onClick={() => setMobileMenuOpen(!mobileMenuOpen)} className="p-2 -mr-2 cursor-pointer">
              <Menu className="h-5 w-5" />
            </button>
          </div>
        </header>

        {/* Mobile Navigation Dropdown */}
        {mobileMenuOpen && (
          <div className="md:hidden absolute top-14 left-0 right-0 border-b bg-background shadow-lg z-30 p-4 flex flex-col gap-2">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 rounded-md px-3 py-3 text-sm font-medium hover:bg-accent"
              >
                <item.icon className="h-4 w-4 text-muted-foreground" />
                {item.label}
              </Link>
            ))}
            <div className="h-px bg-border my-2" />
            <Link href="/bot" target="_blank" className="flex items-center gap-3 rounded-md px-3 py-3 text-sm font-medium hover:bg-accent text-primary">
              <MessageSquare className="h-4 w-4" />
              Open Bot
            </Link>
            <Link href="/app/settings" className="flex items-center gap-3 rounded-md px-3 py-3 text-sm font-medium hover:bg-accent">
              <User className="h-4 w-4 text-muted-foreground" />
              For My Account
            </Link>
          </div>
        )}

        {/* Persistent Mobile Bottom Action */}
        <div className="md:hidden fixed bottom-0 left-0 right-0 border-t bg-background p-3 z-20 flex px-4">
          <Link href="/bot" className="w-full">
            <PrimaryButton className="w-full bg-primary/10 text-primary hover:bg-primary/20 gap-2 shadow-none">
              <MessageSquare className="h-4 w-4" />
              Open Bot
            </PrimaryButton>
          </Link>
        </div>

        <main className="flex-1 p-4 md:p-8 pb-20 md:pb-8">
          <div className="mx-auto max-w-5xl">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
