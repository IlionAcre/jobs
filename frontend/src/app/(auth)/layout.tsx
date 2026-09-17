import * as React from "react"
import Link from "next/link"
import { Zap } from "lucide-react"

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4 bg-muted/20 relative">
      <Link href="/" className="absolute top-6 left-6 flex items-center gap-2 font-bold text-lg">
        <Zap className="h-6 w-6 text-primary" />
        <span>Alerta</span>
      </Link>
      <main className="w-full">
        {children}
      </main>
    </div>
  )
}
