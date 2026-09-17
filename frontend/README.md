# Alerta SaaS Frontend

This is a Next.js 15 fully-frontend SaaS application built to the `FRONTEND_ONLY_PROMPT.md` specification. It implements a fully responsive, frictionless interface designed to maximize trial conversions and drive users to the Telegram Bot.

## Button Placements Documentation

As requested in the project deliverables, here is the roadmap of where each mandatory and key interaction element is placed.

### Global & Navigation
*   **Start Free Trial**: Primary CTA in the public top navigation, and on the sticky mobile action bar.
*   **Open Bot**: 
    *   Public Navbar (`/`, `/pricing`)
    *   Sticky Mobile Action bar (public pages)
    *   Dashboard Sidebar bottom (desktop focus)
    *   Dashboard Mobile Navigation Menu
    *   Dashboard persistent mobile bottom action bar
*   **Sign In**: Secondary link in the public top navigation.

### Landing Page (`/`)
*   **Start Free Trial**: Massive primary button in the hero segment.
*   **See Pricing**: Secondary button in the hero segment.
*   **View Demo Alerts**: Distinct text link right below the main CTAs.
*   **For My Account**: Available in the dashboard sidebar once authenticated.

### Auth (`/login` & `/signup`)
*   **Continue with Google**: The dominant, styled `AuthOptionCard` at the top of the auth screen. 
*   **Continue with Email**: The secondary `AuthOptionCard`.
*   **Use Password Instead**: Text link rendered contextually inside the email entry view.

### Dashboard Core (`/app`)
*   **Create Query**: Primary button at the top header area.
*   **Manage All**: Text link to navigate to the queries table.

### Queries (`/app/queries`)
*   **Create Query**: Primary button at the top right, opening the creation form.
*   **Pause / Resume**: The play/pause icon button in the action column of each query row (and mobile card).
*   **Test Query**: The play-circle icon button for triggering a test webhook.
*   **Edit**: The pencil icon button for modifications.
*   **Duplicate**: The copy icon button.
*   **Delete**: The trash icon, tied conditionally to a destructive `ConfirmDialog`.
*   **Modal Actions (`Save & Activate`, `Cancel`)**: Found within the inline `Create Query` form element that slides in upon requesting a new query.

### Billing (`/app/billing`)
*   **Start Trial**: Displayed contextually as `Manage Plan` if a trial is active.
*   **Manage Plan**: Primary action in the active billing panel.
*   **Update Payment Method**: Secondary action.
*   **Cancel Plan**: Destructive-styled secondary action.
*   **View (Invoices)**: Small link next to each rendered invoice line.

### Settings (`/app/settings`)
*   **Save Changes**: Primary action beneath form fields.
*   **Delete Account**: Danger-zone distinct button opening a destructive confirm dialog.

### Help (`/app/help`)
*   **Open Bot**: Large actionable card to troubleshoot alerts.
*   **Contact Support**: Secondary card action routing to mailto.
*   **For My Account**: Secondary context action to navigate back to settings.

---

## Ease-of-Access & UX Rationale

The user experience prioritizes reducing friction to zero for the core "Aha!" moment—opening the Telegram Bot.

### Friction Reduction Decisions
1.  **Omitting Complex Settings First**: The Dashboard and Landing page emphasize action instead of configuration. `Start Free Trial` is practically unavoidable.
2.  **Auth Hierarchy**: "Continue with Google" uses the official Google brand colors and distinct SVG pathing, making it significantly more clickable than email. By abstracting email/password away behind a secondary view, we nudge users to OAuth, which has higher completion rates.
3.  **Persisting "Open Bot"**: Since the product *is* the bot alerts, users should never have to hunt for the bot. It is persistently docked to the bottom of mobile screens, heavily injected into the help page, and prominently displayed on the landing page.
4.  **Bot Redirect Strategy**: The `/bot` route utilizes a seamless `window.location.replace` while still rendering a fallback button. If the browser blocks deep links (common in in-app browsers), the user sees a large, unmistakable button.
5.  **Responsive Degradation**: Data tables scale poorly on mobile. Instead of horizontal scrolls, the `QueryTable` morphs into "Stacked Cards" below the `md` breakpoint, ensuring 44x44px hit areas on all icon actions.
6.  **Interactive Feedback**: Using a custom generic Toast notification context, every action (like pausing a query or test pings) feels immediately tactile without requiring a page reload.

## Development Stack
*   Next.js 15 (App Router)
*   Tailwind CSS v4
*   React Query (@tanstack/react-query)
*   Lucide React (Icons)
