"use client";

import Link from "next/link"
import { useState } from "react"
import { PrimaryButton } from "@/components/ui/button"

export default function ForgotPasswordPage() {
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    // Simulate API call to send reset email
    setTimeout(() => {
      setLoading(false);
      setSubmitted(true);
    }, 1000);
  };

  return (
    <div className="bg-card w-full max-w-md rounded-2xl border shadow-lg p-6 sm:p-8 animate-in fade-in zoom-in-95 duration-300 mx-auto">
      {!submitted ? (
        <>
          <div className="text-center space-y-2 mb-8">
            <h1 className="text-2xl font-bold tracking-tight">Forgot Password</h1>
            <p className="text-sm text-muted-foreground">Enter your email address and we'll send you a link to reset your password.</p>
          </div>

          <form className="space-y-4" onSubmit={handleSubmit}>
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
            
            <PrimaryButton className="w-full h-10 mt-4" type="submit" disabled={loading}>
              {loading ? "Sending..." : "Send Reset Link"}
            </PrimaryButton>
          </form>
        </>
      ) : (
        <div className="text-center space-y-6">
          <div className="w-12 h-12 bg-primary/10 rounded-full flex items-center justify-center mx-auto text-primary">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 13V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v12c0 1.1.9 2 2 2h8"></path><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"></path><path d="m16 19 2 2 4-4"></path></svg>
          </div>
          <div className="space-y-2">
            <h1 className="text-xl font-bold tracking-tight">Check your email</h1>
            <p className="text-sm text-muted-foreground">We have sent a password reset link to your email address. Please check your inbox.</p>
          </div>
          <Link href="/login" className="block w-full">
            <PrimaryButton className="w-full h-10">
              Return to Login
            </PrimaryButton>
          </Link>
        </div>
      )}

      {!submitted && (
        <div className="mt-8 text-center text-sm text-muted-foreground">
          Remember your password?{" "}
          <Link href="/login" className="text-primary hover:underline font-medium">
            Log in
          </Link>
        </div>
      )}
    </div>
  )
}
