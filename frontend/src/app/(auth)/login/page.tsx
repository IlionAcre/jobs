"use client";

import Link from "next/link"
import { useRouter } from "next/navigation"
import { AuthOptionCard } from "@/components/ui/auth-option-card"
import { KeyRound } from "lucide-react"
import { api } from "@/lib/api/client"
import { useState } from "react"
import { PrimaryButton } from "@/components/ui/button"

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const handleGoogleAuth = async () => {
    setLoading(true);
    const redirectUrl = await api.loginGoogleStart();
    router.push(redirectUrl);
  };

  const handleEmailAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    // Simulate email login
    const redirectUrl = await api.loginGoogleStart();
    router.push(redirectUrl);
  };

  return (
    <div className="bg-card w-full max-w-md rounded-2xl border shadow-lg p-6 sm:p-8 animate-in fade-in zoom-in-95 duration-300 mx-auto">
      <div className="text-center space-y-2 mb-8">
        <h1 className="text-2xl font-bold tracking-tight">Login</h1>
        <p className="text-sm text-muted-foreground">Access your account to manage your instant job alerts.</p>
      </div>

      <form className="space-y-4" onSubmit={handleEmailAuth}>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="email">Email Address</label>
            <input 
              id="email" 
              type="email" 
              placeholder="you@example.com" 
              className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              required 
            />
          </div>
          <div className="space-y-2">
             <div className="flex items-center justify-between">
                <label className="text-sm font-medium" htmlFor="password">Password</label>
                <Link href="/forgot-password" className="text-xs text-primary hover:underline">Forgot password?</Link>
             </div>
            <input 
              id="password" 
              type="password" 
              placeholder="••••••••" 
              className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              required 
            />
          </div>
        </div>
        <PrimaryButton className="w-full h-10 mt-2" type="submit" disabled={loading}>
          Sign In
        </PrimaryButton>
      </form>

      <div className="relative my-6">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-card px-2 text-muted-foreground">
            Or
          </span>
        </div>
      </div>

      <AuthOptionCard
        icon={
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
          </svg>
        }
        label="Continue with Google"
        onClick={handleGoogleAuth}
        disabled={loading}
      />

      <div className="mt-8 text-center text-sm text-muted-foreground">
        Don&apos;t have an account?{" "}
        <Link href="/signup" className="text-primary hover:underline font-medium">
          Sign up
        </Link>
      </div>
    </div>
  )
}
