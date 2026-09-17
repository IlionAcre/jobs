import Link from "next/link"
import { PrimaryButton, SecondaryButton } from "@/components/ui/button"
import { Zap, Bell, Shield, ZapIcon } from "lucide-react"

export default function LandingPage() {
  return (
    <div className="flex-1 flex flex-col pt-16 md:pt-24 pb-20">
      <section className="container mx-auto px-4 text-center max-w-4xl space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
        <div className="mx-auto w-fit rounded-full bg-primary/10 px-4 py-1.5 text-sm font-medium text-primary mb-4 flex items-center gap-2">
          <ZapIcon className="h-4 w-4" />
          <span>Real-time job alerts delivered directly to your pocket</span>
        </div>
        
        <h1 className="text-4xl md:text-6xl font-extrabold tracking-tight text-foreground">
          Never miss out on your dream job. <br className="hidden sm:inline" /> 
          <span className="text-primary">Instant Telegram alerts.</span>
        </h1>
        
        <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto leading-relaxed">
          The easiest way to stay ahead of the competition. Set up your job searches in seconds and get instant notifications straight to your phone so you can apply first.
        </p>
        
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 pt-4">
          <Link href="/signup" className="w-full sm:w-auto">
            <PrimaryButton size="lg" className="w-full text-base h-12 px-8">
              Start 3-Day Free Trial
            </PrimaryButton>
          </Link>
          <Link href="/pricing" className="w-full sm:w-auto">
            <SecondaryButton size="lg" className="w-full text-base h-12 px-8">
              View Pricing
            </SecondaryButton>
          </Link>
        </div>
        
        <div className="pt-8">
          <Link href="/bot" target="_blank" className="font-medium text-primary hover:underline underline-offset-4 decoration-primary/50">
            Or try out some Demo Alerts &rarr;
          </Link>
        </div>
      </section>

      <section className="container mx-auto px-4 mt-24 md:mt-32 max-w-5xl">
        <div className="grid md:grid-cols-3 gap-8 text-center md:text-left">
          <div className="space-y-3 flex flex-col items-center md:items-start p-6 bg-card rounded-2xl border shadow-sm">
            <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center text-primary mb-2">
              <Zap className="h-6 w-6" />
            </div>
            <h3 className="text-xl font-bold">Incredibly Easy Setup</h3>
            <p className="text-muted-foreground text-sm leading-relaxed">No confusing technical dashboards. Tell us what jobs you want, connect your Telegram in one click, and you are good to go.</p>
          </div>
          
          <div className="space-y-3 flex flex-col items-center md:items-start p-6 bg-card rounded-2xl border shadow-sm relative overflow-hidden">
            <div className="absolute top-0 right-0 p-4 opacity-5 pointer-events-none">
              <Bell className="h-32 w-32" />
            </div>
            <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center text-primary mb-2">
              <Bell className="h-6 w-6" />
            </div>
            <h3 className="text-xl font-bold">Instant Notifications</h3>
            <p className="text-muted-foreground text-sm leading-relaxed">Hear about a job the exact second it is posted online. Be the very first applicant and multiply your chances of getting hired.</p>
          </div>

          <div className="space-y-3 flex flex-col items-center md:items-start p-6 bg-card rounded-2xl border shadow-sm">
            <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center text-primary mb-2">
              <Shield className="h-6 w-6" />
            </div>
            <h3 className="text-xl font-bold">Always Working For You</h3>
            <p className="text-muted-foreground text-sm leading-relaxed">Our system runs 24/7 scanning for opportunities so you don't have to keep refreshing job boards manually.</p>
          </div>
        </div>
      </section>
    </div>
  )
}
