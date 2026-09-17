"use client";

import Link from "next/link"
import { useRouter } from "next/navigation"
import { AuthOptionCard } from "@/components/ui/auth-option-card"
import { api } from "@/lib/api/client"
import { useState } from "react"
import { PrimaryButton, SecondaryButton } from "@/components/ui/button"
import { CheckCircle2 } from "lucide-react"

type Step = "credentials" | "profile" | "payment";

export default function SignupPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState<Step>("credentials");

  const handleGoogleAuth = async () => {
    setLoading(true);
    const redirectUrl = await api.loginGoogleStart();
    router.push(redirectUrl);
  };

  const handleCredentialsSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setStep("profile");
  };

  const handleProfileSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setStep("payment");
  };

  const handlePaymentSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    // Simulate final API call
    const redirectUrl = await api.loginGoogleStart();
    router.push(redirectUrl);
  };

  return (
    <div className="bg-card w-full max-w-md mx-auto rounded-2xl border shadow-lg p-6 sm:p-8 animate-in fade-in zoom-in-95 duration-300">

      {step === "credentials" && (
        <div className="animate-in fade-in slide-in-from-right-4 duration-300">
          <div className="text-center space-y-2 mb-8">
            <h1 className="text-2xl font-bold tracking-tight">Create an account</h1>
            <p className="text-sm text-muted-foreground">Start your 3-day free trial today.</p>
          </div>

          <form className="space-y-4" onSubmit={handleCredentialsSubmit}>
            <div className="space-y-4">
               <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="email">Email Address</label>
                <input 
                  id="email" 
                  type="email" 
                  placeholder="you@example.com" 
                  className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                  required 
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="password">Password</label>
                <input 
                  id="password" 
                  type="password" 
                  placeholder="Create a strong password" 
                  className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                  required 
                  minLength={8}
                />
              </div>
            </div>
            
            <PrimaryButton className="w-full h-10 mt-6" type="submit">
              Continue
            </PrimaryButton>
          </form>

          <div className="relative my-6">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-card px-2 text-muted-foreground">Or</span>
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
            Already have an account?{" "}
            <Link href="/login" className="text-primary hover:underline font-medium">Log in</Link>
          </div>
        </div>
      )}

      {step === "profile" && (
        <div className="animate-in fade-in slide-in-from-right-4 duration-300">
          <div className="text-center space-y-2 mb-8">
            <h1 className="text-2xl font-bold tracking-tight">Tell us about yourself</h1>
            <p className="text-sm text-muted-foreground">Help us personalize your experience.</p>
          </div>

          <form className="space-y-6" onSubmit={handleProfileSubmit}>
            <div className="space-y-4">
               <div className="grid grid-cols-2 gap-4">
                 <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="firstName">First Name</label>
                  <input 
                    id="firstName" 
                    type="text" 
                    placeholder="Jane" 
                    className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                    required 
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="lastName">Last Name</label>
                  <input 
                    id="lastName" 
                    type="text" 
                    placeholder="Doe" 
                    className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                    required 
                  />
                </div>
               </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="dob">Date of Birth</label>
                <input 
                  id="dob" 
                  type="date" 
                  className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                  required 
                />
              </div>
            </div>
            
            <div className="flex gap-3 pt-2">
              <SecondaryButton type="button" className="w-full" onClick={() => setStep("credentials")}>
                Back
              </SecondaryButton>
              <PrimaryButton className="w-full" type="submit">
                Continue
              </PrimaryButton>
            </div>
          </form>
        </div>
      )}

      {step === "payment" && (
        <div className="animate-in fade-in slide-in-from-right-4 duration-300">
          <div className="text-center space-y-2 mb-8">
            <h1 className="text-2xl font-bold tracking-tight">Start your trial</h1>
            <p className="text-sm text-muted-foreground">Enjoy full access for 3 days. Cancel anytime. $7.99/mo after.</p>
          </div>

          <form className="space-y-6" onSubmit={handlePaymentSubmit}>
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="cardName">Name on Card</label>
                <input 
                  id="cardName" 
                  type="text" 
                  placeholder="Jane Doe" 
                  className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                  required 
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="cardNumber">Card Number</label>
                <input 
                  id="cardNumber" 
                  type="text" 
                  placeholder="4242 4242 4242 4242" 
                  maxLength={19}
                  className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background tabular-nums"
                  required 
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                 <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="expiry">Expiry (MM/YY)</label>
                  <input 
                    id="expiry" 
                    type="text" 
                    placeholder="12/26" 
                    className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                    required 
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="cvc">CVC</label>
                  <input 
                    id="cvc" 
                    type="text" 
                    placeholder="123" 
                    maxLength={4}
                    className="w-full flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                    required 
                  />
                </div>
               </div>
            </div>
            
            <div className="flex gap-3 pt-2">
              <SecondaryButton type="button" className="w-full" onClick={() => setStep("profile")} disabled={loading}>
                Back
              </SecondaryButton>
              <PrimaryButton className="w-full" type="submit" disabled={loading}>
                {loading ? "Processing..." : "Start Free Trial"}
              </PrimaryButton>
            </div>
            
            <p className="text-xs text-center text-muted-foreground flex items-center justify-center gap-1.5">
               <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
               Payments are securely encrypted and processed.
            </p>
          </form>
        </div>
      )}

    </div>
  )
}
