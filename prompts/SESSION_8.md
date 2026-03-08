# SESSION_8.md — React Frontend: Dashboard, Supplier Profile, Alert Centre

## HOW TO START THIS SESSION

Say this to Claude Code:
```
Read CLAUDE.md first. Then read prompts/SESSION_8.md.
Do not read any other file yet.
Tell me what you're going to build before writing any code.
```

Only start after Session 7 checklist is fully green and `make test` passes clean.

---

## CONTEXT CHECK

Read:
1. `CLAUDE.md`
2. `specs/PRODUCT_SPEC.md` — Sections 2, 3, 4 (design system, layout, features F01–F05)
3. `specs/API_SPEC.md` — Section 7 (endpoints the frontend calls)

Confirm:
> "I am building the React frontend. Dark theme, shadcn/ui components, Tailwind only,
> Recharts for charts, lucide-react icons, DM Sans + DM Serif Display fonts.
> The frontend calls the FastAPI backend at /api/v1/. Auth is Auth0."

---

## RULES FOR THIS SESSION

- Tailwind utility classes only. No CSS modules, no inline styles, no styled-components.
- shadcn/ui for all UI primitives (Button, Badge, Table, Dialog, Sheet, Tooltip, Select).
- Every colour from CSS variables defined in globals.css — never hardcode hex values.
- All risk level styling via `RISK_CONFIG` from `src/lib/risk.ts` — no one-off colour decisions.
- All API calls via custom hooks in `src/hooks/` — never fetch directly in components.
- TypeScript strict mode — no `any`, no implicit types.
- Run `npm run lint` and `npm run typecheck` after Steps 3 and 5.
- Do not build the Risk Map or Reports — those are Phase 4.
- Do not build the XGBoost training UI — that is backend ML work.

---

## STEP 1: Project Setup

### Scaffold the React app

```bash
cd frontend
npm create vite@latest . -- --template react-ts
npm install
```

### Install dependencies

```bash
# UI
npm install tailwindcss @tailwindcss/vite
npm install class-variance-authority clsx tailwind-merge
npm install @radix-ui/react-dialog @radix-ui/react-select @radix-ui/react-tooltip
npm install @radix-ui/react-badge @radix-ui/react-separator @radix-ui/react-sheet

# shadcn/ui setup
npx shadcn@latest init
# When prompted: Dark theme, CSS variables yes, src/components/ui path

# Charts + icons
npm install recharts
npm install lucide-react

# Routing + data fetching
npm install react-router-dom
npm install @tanstack/react-query

# Auth
npm install @auth0/auth0-react

# Fonts
npm install @fontsource/dm-sans @fontsource/dm-serif-display

# Date formatting
npm install date-fns
```

### `frontend/src/styles/globals.css`
Define all CSS variables from PRODUCT_SPEC.md Section 2.1 exactly:
```css
@import '@fontsource/dm-sans/400.css';
@import '@fontsource/dm-sans/500.css';
@import '@fontsource/dm-serif-display/400.css';
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --color-bg-base:        #0F1117;
  --color-bg-surface:     #1A1D27;
  --color-bg-elevated:    #242736;
  --color-bg-input:       #1F2232;
  --color-risk-high:      #EF4444;
  --color-risk-high-bg:   #450A0A;
  --color-risk-medium:    #F59E0B;
  --color-risk-medium-bg: #451A03;
  --color-risk-low:       #22C55E;
  --color-risk-low-bg:    #052E16;
  --color-brand:          #6366F1;
  --color-brand-hover:    #4F46E5;
  --color-text-primary:   #F1F5F9;
  --color-text-secondary: #94A3B8;
  --color-text-muted:     #475569;
  --color-border:         #2D3147;
  --color-border-focus:   #6366F1;
}

body {
  background-color: var(--color-bg-base);
  color: var(--color-text-primary);
  font-family: 'DM Sans', sans-serif;
}
```

### `frontend/src/lib/risk.ts`
Copy exactly from PRODUCT_SPEC.md Section 2.2 — `RISK_CONFIG`, `RiskLevel`, `getRiskLevel()`.

### `frontend/src/lib/api.ts`
API client — base URL from env, attaches Auth0 token to every request:
```typescript
export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> { ... }
```

### `frontend/src/lib/utils.ts`
```typescript
export function formatTimeAgo(date: string): string { ... }  // "2h ago", "3d ago"
export function formatScore(score: number | null): string { ... }  // "72" or "—"
export function getScoreTrend(delta: number): "up" | "down" | "flat" { ... }
export function formatDate(date: string): string { ... }  // "Mar 4, 2025"
```

### `frontend/vite.config.ts`
Proxy `/api` to `http://localhost:8000` for local development:
```typescript
server: {
  proxy: {
    '/api': 'http://localhost:8000',
    '/ws': { target: 'ws://localhost:8000', ws: true },
  }
}
```

### `frontend/tsconfig.json`
Enable path alias `@/` → `./src/`:
```json
{ "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["./src/*"] } } }
```

**Say: "✅ Step 1 complete — project scaffolded, dependencies installed."**

---

## STEP 2: App Shell (Layout + Routing + Auth)

### `frontend/src/main.tsx`
```tsx
// Auth0Provider wraps QueryClientProvider wraps RouterProvider
// Auth0 domain + clientId from import.meta.env.VITE_AUTH0_DOMAIN / VITE_AUTH0_CLIENT_ID
```

### `frontend/src/components/layout/Sidebar.tsx`
Fixed 240px sidebar, dark background. From PRODUCT_SPEC.md Section 3.1:
- Logo + tenant name at top
- Nav links: Dashboard, Suppliers, Alerts (with unread count badge), Settings
- User avatar + name + role at bottom
- Active route: `bg-[--color-bg-elevated] border-l-2 border-[--color-brand]`
- Responsive: full on desktop, icon-only on tablet (768–1023px), hidden on mobile

### `frontend/src/components/layout/MobileNav.tsx`
Hamburger button + Sheet (shadcn) slide-in for mobile (< 768px).

### `frontend/src/components/layout/AppShell.tsx`
```tsx
// Sidebar (desktop) | MobileNav (mobile) + main content area
// Outlet for child routes
```

### `frontend/src/router.tsx`
```tsx
const router = createBrowserRouter([
  { path: '/', element: <Navigate to="/dashboard" /> },
  {
    element: <AppShell />,
    children: [
      { path: '/dashboard', element: <DashboardPage /> },
      { path: '/suppliers', element: <SuppliersPage /> },
      { path: '/suppliers/add', element: <AddSupplierPage /> },
      { path: '/suppliers/:supplierId', element: <SupplierProfilePage /> },
      { path: '/alerts', element: <AlertsPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '/settings/users', element: <UsersSettingsPage /> },
      { path: '/onboarding', element: <OnboardingPage /> },
    ],
  },
]);
```

### `frontend/src/hooks/useWebSocket.ts`
WebSocket hook — connects to `/api/v1/ws/alerts?token={jwt}`, handles reconnection:
```typescript
export function useWebSocket(): {
  lastAlertEvent: AlertFiredEvent | null;
  lastScoreEvent: ScoreUpdatedEvent | null;
  isConnected: boolean;
}
```
Reconnection backoff: 1s, 2s, 4s, 30s (max). On reconnect, call `invalidateQueries(['alerts'])`.

**Say: "✅ Step 2 complete."**

---

## STEP 3: Shared Components

Build these once — they are used across all pages.

### `frontend/src/components/ui/RiskBadge.tsx`
```tsx
// <RiskBadge level="high" /> → coloured badge using RISK_CONFIG
// <RiskBadge score={72} /> → derives level from score, renders badge
```

### `frontend/src/components/ui/ScoreTrend.tsx`
```tsx
// <ScoreTrend delta={8} /> → "↑ +8" in red
// <ScoreTrend delta={-5} /> → "↓ -5" in green
// <ScoreTrend delta={0} /> → "→" in grey
// delta > 3 = up, < -3 = down, otherwise flat
```

### `frontend/src/components/ui/SkeletonRow.tsx`
```tsx
// Animated skeleton row for table loading states
// Tailwind animate-pulse, grey placeholder blocks
```

### `frontend/src/components/ui/EmptyState.tsx`
```tsx
interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
}
// Centered, icon + title + description + optional action button
```

### `frontend/src/components/ui/PageHeader.tsx`
```tsx
// Page title (DM Serif Display) + optional subtitle + optional primary action button
// Used on every page
```

### `frontend/src/components/suppliers/SupplierRow.tsx`
```tsx
// One row in the supplier table
// Props: supplier (SupplierSummary), onClick
// Shows: name, country flag + code, RiskBadge, score, ScoreTrend, alert count badge
```

**Run `npm run lint && npm run typecheck` — fix all errors.**
**Say: "✅ Step 3 complete — lint and typecheck passing."**

---

## STEP 4: Pages

### `frontend/src/hooks/usePortfolio.ts`
```typescript
// usePortfolioSummary() → GET /api/v1/portfolio/summary
// usePortfolioSuppliers(params) → GET /api/v1/portfolio/suppliers
// useRemoveSupplier() → DELETE /api/v1/portfolio/suppliers/:id
// All via @tanstack/react-query — automatic caching + invalidation
```

### `frontend/src/hooks/useAlerts.ts`
```typescript
// useAlerts(filters) → GET /api/v1/alerts
// usePatchAlert() → PATCH /api/v1/alerts/:id
// On WebSocket alert.fired event → invalidate alerts query
```

### `frontend/src/hooks/useSupplier.ts`
```typescript
// useSupplier(id) → GET /api/v1/suppliers/:id
// useScoreHistory(id, days) → GET /api/v1/suppliers/:id/score-history
// useSupplierNews(id, filters) → GET /api/v1/suppliers/:id/news
```

### `frontend/src/pages/DashboardPage.tsx`
Implement F01 from PRODUCT_SPEC.md Section 4 exactly:

**Stat cards row:**
- Total Suppliers, High Risk (red), New Alerts, Portfolio Trend
- Staggered fade-in animation (Tailwind `animation-delay`)
- Skeleton loading state (not spinner)

**Alerts strip:**
- Top 3 unread alerts as horizontal cards
- Hidden when zero unread alerts
- "View all alerts →" link

**Supplier table:**
- All columns from spec: Name, Country, Risk, Score, 7d Trend, Alerts, Last Updated
- Client-side search debounced 300ms
- Filters: Risk Level checkboxes, Country multi-select
- Filters stored in URL query params (`?risk=high&country=TW`)
- Skeleton rows while loading
- Empty states from F01-E (zero suppliers, zero search results)
- Time-of-day greeting ("Good morning/afternoon/evening, {name}")

### `frontend/src/pages/SupplierProfilePage.tsx`
Implement F02 from PRODUCT_SPEC.md Section 4 exactly:

**Score dial:**
- SVG circular gauge, colour = risk level
- Score in DM Serif Display 64px at centre
- 7d delta below with ScoreTrend component
- Model version + scored time + completeness % footer
- Animate on load: sweep from 0 to score (CSS transition, 600ms)

**Signal breakdown:**
- 5 category rows with score + weight bar
- Financial, News, Shipping, Geopolitical, Macro

**Score history chart (Recharts):**
- AreaChart with gradient fill
- Colour changes with current risk level
- Dashed reference lines at 40 and 70
- Hover tooltip: date + score + risk level
- Time window toggle: 7d / 30d / 90d / 1y

**SHAP waterfall:**
- Horizontal bar chart from `top_drivers` + negative drivers
- Bars right of centre = red (increases risk)
- Bars left of centre = green (decreases risk)
- Click bar → Tooltip with `explanation` text

**News feed:**
- Filter tabs: All / Negative / Positive / Neutral
- Each article: source credibility dot, source name, time ago, title, sentiment badge, topics, score impact
- External link opens in new tab
- "Load more" button

**Data freshness banners:**
- Yellow if `financial_data_is_stale = true`
- Grey if `data_completeness < 0.5`

### `frontend/src/pages/AlertsPage.tsx`
Implement F03 from PRODUCT_SPEC.md Section 4:

**Left panel: alert list**
- Tabs: New / Investigating / Resolved / All (with counts)
- Each row: severity icon, supplier name, title, time ago, status badge
- Unread = left border in risk colour
- Real-time: new alert appears at top with 500ms yellow highlight via WebSocket

**Right panel: alert detail**
- Status dropdown with valid transitions only
- Note textarea (save on blur or Ctrl+Enter)
- "View Supplier Profile →" link
- Share: copy link button

**Mobile:** Full-screen sheet instead of right panel (shadcn Sheet).

### `frontend/src/pages/AddSupplierPage.tsx`
Implement F04 from PRODUCT_SPEC.md Section 4:

**Single add flow (3 steps):**
1. Typeahead search (2+ chars, debounced 300ms, max 5 results with confidence score)
2. Confirm supplier card (show current score if available)
3. Optional metadata (internal_id, tags, custom_name)

**Bulk import flow:**
1. CSV template download
2. Drag-and-drop upload zone (max 5MB, max 500 rows)
3. Resolution review table (auto-added ✓, review needed ⚠, unresolved ✗)
4. Poll import status every 2 seconds until complete

### `frontend/src/pages/SettingsPage.tsx`
Implement F05 from PRODUCT_SPEC.md Section 4:

**Alert rules tab:**
- Score spike threshold (number input, 5–50)
- High risk threshold (number input, 50–95)
- Email recipients (tag input, max 10)
- Slack webhook URL (text input + "Test Webhook" button → show success/fail inline)
- Save button

**Users tab (`/settings/users`):**
- User list with role, last active, remove button
- "Invite User" button → modal with email + role select
- Pending invites section with expiry date

**Say: "✅ Step 4 complete."**

---

## STEP 5: Onboarding + Polish

### `frontend/src/pages/OnboardingPage.tsx`
4-step flow from PRODUCT_SPEC.md Section 5:
1. Welcome
2. Add suppliers (CSV upload or manual)
3. Configure alert email recipients
4. Done — navigate to dashboard

Show only on first login (check localStorage flag `onboarding_complete`).

### `frontend/src/components/layout/DataFreshnessBar.tsx`
Global banner on pages with scores:
- No banner if scored < 6h ago
- Grey info if 6–12h ago: "Score updated {N}h ago"
- Yellow warning if 12–24h ago
- Red warning if > 24h ago

### Polish checklist before declaring done:
- `<title>` updates per page (React Helmet or document.title in useEffect)
- Loading states: every data fetch shows skeleton, never blank
- Error states: every failed fetch shows retry button, not blank
- Mobile responsive: test all pages at 375px width
- Country flags: use emoji flags (`🇹🇼`) from ISO code helper function

**Run `npm run lint && npm run typecheck` — must be clean.**
**Say: "✅ Step 5 complete — lint and typecheck passing."**

---

## STEP 6: Environment + Build Verification

### `frontend/.env.example`
```
VITE_AUTH0_DOMAIN=your-tenant.auth0.com
VITE_AUTH0_CLIENT_ID=your-client-id
VITE_AUTH0_AUDIENCE=https://api.supplierrisk.com
VITE_API_BASE_URL=http://localhost:8000
```

### Verify full stack works together:
```bash
# Terminal 1 — backend
conda run -n genai uvicorn backend.app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && npm run dev

# Open http://localhost:5173
# Verify: dashboard loads, supplier table renders, /health returns 200
```

Add to root Makefile:
```makefile
frontend-dev:   # cd frontend && npm run dev
frontend-build: # cd frontend && npm run build
frontend-lint:  # cd frontend && npm run lint && npm run typecheck
```

**Say: "✅ Step 6 complete — full stack running locally."**

---

## SESSION 8 DONE — CHECKLIST

```
□ npm run lint passes clean — zero ESLint errors
□ npm run typecheck passes clean — zero TypeScript errors
□ Full stack starts: uvicorn + npm run dev, dashboard loads
□ Dark theme applied globally via CSS variables
□ DM Sans (body) + DM Serif Display (headings/scores) loaded
□ All colours from CSS variables — no hardcoded hex in components
□ RISK_CONFIG used for all risk level styling — no one-off colours
□ F01 Dashboard: stat cards, alerts strip, supplier table with filters
□ F01 Empty states: zero suppliers, zero alerts, zero search results
□ F02 Supplier Profile: score dial, signal breakdown, SHAP waterfall, score history chart
□ F02 Score history: Recharts AreaChart, threshold lines at 40 and 70
□ F03 Alert Centre: tabs, list, detail panel, status transitions
□ F03 WebSocket: new alert appears in real-time without page refresh
□ F04 Add Supplier: typeahead search + 3-step single add flow
□ F04 Bulk import: CSV upload, resolution review, progress polling
□ F05 Settings: alert rules form, user management, invite modal
□ Skeleton loading states on all data fetches
□ Mobile responsive: sidebar hidden, hamburger nav on < 768px
□ Filters persist in URL query params on dashboard
□ Data freshness banners on supplier profile
```

**Say: "Session 8 complete. Checklist: X/20 items green."**

If any item is red — fix it before declaring done.

---

## WHAT COMES NEXT

Session 9: End-to-end testing, performance optimisation, deployment preparation.

Commit before starting Session 9:
```
git add .
git commit -m "feat(session-8): React frontend — dashboard, supplier profile, alert centre"
git push origin main
```
