import Link from "next/link"
import { PrimaryButton } from "@/components/ui/button"
import { Check } from "lucide-react"

export default function PricingPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center pt-16 pb-24 px-4">
      <div className="text-center space-y-4 mb-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
        <h1 className="text-3xl md:text-5xl font-bold tracking-tight">Simple, transparent pricing</h1>
        <p className="text-lg text-muted-foreground max-w-xl mx-auto">
          One plan gives you full access to unlimited job searches and instant Telegram alerts.
        </p>
      </div>

      <div className="w-full max-w-4xl grid md:grid-cols-2 gap-8 animate-in fade-in zoom-in-95 duration-500 delay-100">
        
        {/* Monthly Plan */}
        <div className="bg-card rounded-3xl border shadow-lg overflow-hidden flex flex-col">
          <div className="p-8 pb-6 border-b bg-muted/30">
            <h2 className="text-xl font-semibold mb-2">Pro Monthly</h2>
            <div className="flex items-baseline gap-2">
              <span className="text-4xl font-black">$7.99</span>
              <span className="text-muted-foreground font-medium">/ month</span>
            </div>
            <p className="text-sm text-muted-foreground mt-3">Start with a 3-day free trial. Cancel anytime.</p>
          </div>
          
          <div className="p-8 space-y-6 flex-1 flex flex-col">
            <ul className="space-y-4 mb-auto">
              {[
                "Unlimited job searches",
                "Instant Telegram notifications",
                "Cancel or pause anytime"
              ].map((feature, i) => (
                <li key={i} className="flex items-start gap-3 text-sm font-medium">
                  <Check className="h-5 w-5 text-primary shrink-0" />
                  <span>{feature}</span>
                </li>
              ))}
            </ul>
            
            <Link href="/signup" className="block w-full pt-4 mt-auto">
              <PrimaryButton className="w-full h-12 text-base shadow-sm" variant="outline">
                Start 3-Day Free Trial
              </PrimaryButton>
            </Link>
          </div>
        </div>

        {/* Annual Plan */}
        <div className="bg-card rounded-3xl border-2 border-primary shadow-xl overflow-hidden relative flex flex-col">
          <div className="absolute top-0 inset-x-0 h-1.5 bg-primary"></div>
          <div className="absolute -right-12 top-6 bg-primary text-primary-foreground text-xs font-bold px-12 py-1 rotate-45">
            BEST VALUE
          </div>
          <div className="p-8 pb-6 border-b bg-primary/5">
            <h2 className="text-xl font-semibold mb-2">Pro Annual</h2>
            <div className="flex items-baseline gap-2">
              <span className="text-4xl font-black">$79.90</span>
              <span className="text-muted-foreground font-medium">/ year</span>
            </div>
            <p className="text-sm text-primary font-medium mt-3 flex items-center gap-1.5">
              <span className="bg-primary/20 text-primary px-2 py-0.5 rounded text-xs font-bold leading-none">2 MONTHS FREE</span>
              Save ~16% annually
            </p>
          </div>
          
          <div className="p-8 space-y-6 flex-1 flex flex-col">
            <ul className="space-y-4 mb-auto">
              {[
                "Unlimited job searches",
                "Instant Telegram notifications",
                "Priority support",
                "Cancel anytime"
              ].map((feature, i) => (
                <li key={i} className="flex items-start gap-3 text-sm font-medium">
                  <Check className="h-5 w-5 text-primary shrink-0" />
                  <span>{feature}</span>
                </li>
              ))}
            </ul>
            
            <Link href="/signup" className="block w-full pt-4 mt-auto">
              <PrimaryButton className="w-full h-12 text-base shadow-md">
                Start 3-Day Free Trial
              </PrimaryButton>
            </Link>
          </div>
        </div>

      </div>
    </div>
  )
}
