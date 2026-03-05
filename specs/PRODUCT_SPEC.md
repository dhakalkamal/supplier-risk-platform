# PRODUCT_SPEC.md — Product Specification

> Read this before building any UI component, frontend route, or user-facing feature.
> Every feature here has acceptance criteria — "done" means ALL criteria are checked.
> If a UI behaviour is not specified, check the journey descriptions before inventing.

---

## 1. Users & Personas

### Primary: Maya — The Procurement Manager

```
Company:     $50M–$300M manufacturer
Role:        Manages 30–150 supplier relationships
Biggest fear: Production line stoppage from an undetected supplier failure
Current tools: Excel, email, phone calls, gut feel
Phone habits: Checks email before 8am, on her phone
Tech comfort: Moderate — uses Salesforce and NetSuite but dislikes complex UI
Core need:   "Tell me which 3 suppliers I need to worry about this week"
```

Maya is the daily active user. She opens the app every morning, checks alerts, and
acts on them. She does not want to configure, explore, or analyse — she wants answers.
Every feature must be evaluated through this lens: does it surface answers faster?

**What Maya will not do:**
- Read documentation or tooltips
- Configure complex settings without IT help
- Tolerate a dashboard that makes her work to find the signal

---

### Secondary: Rohan — VP of Supply Chain

```
Company:     Same as Maya (her manager)
Role:        Board-level risk reporting, strategic supplier decisions
Usage:       Weekly, not daily — reads the Monday report
Core need:   "Show me we have visibility. Give me something for the board deck."
```

Rohan is a consumer of outputs, not an operator. He receives the weekly PDF email,
opens the dashboard occasionally to verify a concern, and forwards summaries to the CEO.
He will not act on individual alerts — Maya does that.

---

### Tertiary: Sam — IT Admin / Operations

```
Role:        Sets up the account, manages SSO, owns ERP integrations
Usage:       Bursts of heavy use during setup, then occasional admin tasks
Core need:   Security, audit trail, SAML SSO, API access for integrations
```

Sam enables Maya and Rohan. He doesn't use the risk features — he makes them accessible.

---

## 2. Design System & Component Library

**Decisions made — Claude must follow these, not invent alternatives:**

```
Framework:          React + TypeScript (already decided — see DECISIONS.md)
Styling:            Tailwind CSS utility classes only — no CSS modules, no inline styles
Component library:  shadcn/ui — pre-built accessible components (Button, Dialog, Table,
                    Select, Badge, Tooltip, Sheet, etc.)
                    Import: from '@/components/ui/{component}'
Charts:             Recharts — line charts, area charts, bar charts
Icons:              lucide-react — consistent icon set
Fonts:              Display: 'DM Serif Display' (headings, scores, key numbers)
                    Body: 'DM Sans' (all body text, UI labels)
                    Load via: Google Fonts or Fontsource npm package
Colour tokens:      See Section 2.1
Animation:          Tailwind transition classes for micro-interactions
                    No heavy animation libraries — performance first
```

### 2.1 Colour Tokens

Define these as CSS variables in `frontend/src/styles/globals.css`.
Use these everywhere — never hardcode hex values in components.

```css
:root {
  /* Background */
  --color-bg-base:        #0F1117;   /* page background — dark slate */
  --color-bg-surface:     #1A1D27;   /* card/panel background */
  --color-bg-elevated:    #242736;   /* hover state, selected rows */
  --color-bg-input:       #1F2232;   /* form inputs */

  /* Risk colours — used everywhere a risk level appears */
  --color-risk-high:      #EF4444;   /* red-500 */
  --color-risk-high-bg:   #450A0A;   /* red-950 — background for high risk badges */
  --color-risk-medium:    #F59E0B;   /* amber-500 */
  --color-risk-medium-bg: #451A03;   /* amber-950 */
  --color-risk-low:       #22C55E;   /* green-500 */
  --color-risk-low-bg:    #052E16;   /* green-950 */

  /* Brand */
  --color-brand:          #6366F1;   /* indigo-500 — primary actions */
  --color-brand-hover:    #4F46E5;   /* indigo-600 */

  /* Text */
  --color-text-primary:   #F1F5F9;   /* slate-100 */
  --color-text-secondary: #94A3B8;   /* slate-400 */
  --color-text-muted:     #475569;   /* slate-600 */

  /* Borders */
  --color-border:         #2D3147;
  --color-border-focus:   #6366F1;
}
```

### 2.2 Risk Level Styling Convention

Every place a risk level appears uses these exact classes — no one-offs:

```tsx
// frontend/src/lib/risk.ts

export const RISK_CONFIG = {
  high: {
    label: "High",
    badgeClass: "bg-red-950 text-red-400 border border-red-800",
    dotClass: "bg-red-500",
    textClass: "text-red-400",
    scoreRange: "≥ 70",
  },
  medium: {
    label: "Medium",
    badgeClass: "bg-amber-950 text-amber-400 border border-amber-800",
    dotClass: "bg-amber-500",
    textClass: "text-amber-400",
    scoreRange: "40–69",
  },
  low: {
    label: "Low",
    badgeClass: "bg-green-950 text-green-400 border border-green-800",
    dotClass: "bg-green-500",
    textClass: "text-green-400",
    scoreRange: "< 40",
  },
  insufficient_data: {
    label: "Monitoring",
    badgeClass: "bg-slate-800 text-slate-400 border border-slate-700",
    dotClass: "bg-slate-500",
    textClass: "text-slate-400",
    scoreRange: "—",
  },
} as const;

export type RiskLevel = keyof typeof RISK_CONFIG;

export function getRiskLevel(score: number | null): RiskLevel {
  if (score === null) return "insufficient_data";
  if (score >= 70) return "high";
  if (score >= 40) return "medium";
  return "low";
}
```

---

## 3. Application Layout

### 3.1 Overall Shell

```
┌─────────────────────────────────────────────────────────┐
│  SIDEBAR (240px, fixed, dark)                           │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Logo + tenant name                             │   │
│  │  ─────────────────────────────────────────────  │   │
│  │  Dashboard (home icon)                          │   │
│  │  Suppliers (building icon)                      │   │
│  │  Alerts (bell icon) [unread badge]              │   │
│  │  Risk Map (globe icon)                          │   │
│  │  Reports (file icon)           [Phase 4]        │   │
│  │  ─────────────────────────────────────────────  │   │
│  │  Settings (gear icon)                           │   │
│  │  [User avatar + name + role]                    │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  MAIN CONTENT AREA (fluid width, scrollable)            │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Page header (title + primary action button)    │   │
│  │  ─────────────────────────────────────────────  │   │
│  │  Page content                                   │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Sidebar behaviour:**
- Desktop (≥ 1024px): always visible, fixed 240px
- Tablet (768–1023px): collapsed to icon-only 64px, hover to expand
- Mobile (< 768px): hidden, accessible via hamburger menu → slide-in sheet

**Active state:** Current route highlighted with `bg-[--color-bg-elevated]` and left border `border-l-2 border-[--color-brand]`.

---

### 3.2 Page Routes

```
/                           → redirect to /dashboard
/dashboard                  → Portfolio Dashboard (F01)
/suppliers                  → Supplier list (same as dashboard table, standalone)
/suppliers/:supplier_id     → Supplier Profile (F02)
/alerts                     → Alert Centre (F03)
/suppliers/add              → Add Supplier (F04)
/settings                   → Account Settings (F05)
/settings/users             → User Management
/settings/billing           → Billing & Plan
/map                        → Risk Map (F06, Phase 4)
/reports                    → Reports (F07, Phase 4)
/onboarding                 → First-run onboarding flow (see Section 7)
```

---

## 4. Feature Specifications

---

### F01 — Portfolio Dashboard

**Route:** `/dashboard`
**Primary user:** Maya — daily, first thing
**Purpose:** Surface the 3–5 things Maya needs to act on today, without her having to search.

#### Layout

```
┌─────────────────────────────────────────────────────────┐
│  "Good morning, Maya" [date]      [+ Add Supplier btn]  │
├──────────┬──────────┬──────────┬───────────────────────┤
│ STAT     │ STAT     │ STAT     │  STAT                 │
│ 87       │ 12 🔴    │ 8        │ ↑ Worsening           │
│ Suppliers│ High Risk│ New Alerts│ Portfolio trend       │
├──────────┴──────────┴──────────┴───────────────────────┤
│  ALERTS STRIP (top 3 unread alerts, horizontal cards)   │
│  [Score spike: TSMC +18pts] [Sanctions: Vendor X] [...] │
├─────────────────────────────────────────────────────────┤
│  SUPPLIER TABLE                                         │
│  [Search...] [Country ▼] [Industry ▼] [Risk Level ▼]   │
│                                                         │
│  Name            Country  Risk   Score  Trend  Alerts  │
│  TSMC            TW       🔴 72   ↑+8    2       │
│  Samsung Elec.   KR       🟡 58   →0     0       │
│  ...                                                    │
└─────────────────────────────────────────────────────────┘
```

#### Stat Cards (top row)
- **Total Suppliers** — count with link to full list
- **High Risk** — count of suppliers with score ≥ 70, red badge, click to filter table
- **New Alerts** — count of unread alerts, click navigates to `/alerts`
- **Portfolio Trend** — "Improving / Worsening / Stable" based on 7-day average score change, with arrow icon

#### Alerts Strip
- Shows top 3 unread alerts, sorted by severity then recency
- Each card: supplier name, alert type, score delta or event description, time ago
- Click card → navigates to alert detail in `/alerts`
- "View all alerts →" link at right end
- Strip is hidden if zero unread alerts

#### Supplier Table
- Columns: Name, Country flag + code, Industry, Risk Score (colour-coded), 7d Trend (↑↓→ with delta), Unread Alerts (count badge), Last Updated
- Default sort: risk_score descending
- Click row → navigate to `/suppliers/:id`
- Search: client-side filtering on canonical_name and custom_name, debounced 300ms
- Filters: Country (multi-select), Industry (multi-select), Risk Level (High/Medium/Low checkboxes)
- Filters apply simultaneously (AND logic)
- Active filters shown as dismissible chips above table

#### Acceptance Criteria
- [ ] Loads in < 2 seconds for portfolios up to 200 suppliers (data from `/api/v1/portfolio/summary` + `/api/v1/portfolio/suppliers`)
- [ ] Stat cards animate in with staggered fade (50ms delay between each)
- [ ] Risk level thresholds: High ≥ 70, Medium 40–69, Low < 40
- [ ] Score trend arrow: ↑ if 7d_delta > 3, ↓ if 7d_delta < -3, → otherwise
- [ ] Score trend colour: ↑ red (higher = worse), ↓ green, → grey
- [ ] Unread alert count badge disappears when alerts marked as read
- [ ] Table search filters within 300ms of typing (no API call — client-side)
- [ ] Filters persist on page reload (stored in URL query params)
- [ ] "Good morning/afternoon/evening" changes based on local time
- [ ] Table shows skeleton loading state while data fetches (not spinner)

---

### F01-E — Empty States

Every empty state must be helpful, not just "nothing here."

**Zero suppliers (new account):**
```
[Icon: building with plus]
"Your portfolio is empty"
"Add your first supplier to start monitoring risk signals."
[+ Add Supplier]  [Upload CSV]
```

**Zero alerts (all clear):**
```
[Icon: shield with checkmark, green]
"All clear"
"No new alerts. Your portfolio is healthy."
[last checked: 6 minutes ago]
```

**Zero search results:**
```
[Icon: search with X]
"No suppliers match '{search term}'"
"Try a different name or clear your filters."
[Clear filters]
```

**Score not yet available (new supplier, < 7 days):**
```
Score display shows: "—"
Tooltip on hover: "Gathering data — score available in {N} days"
Risk badge shows: "Monitoring" in slate colour
```

---

### F02 — Supplier Profile Page

**Route:** `/suppliers/:supplier_id`
**Primary user:** Maya — after clicking an alert, to understand context

#### Layout

```
┌─────────────────────────────────────────────────────────┐
│  ← Back to portfolio                                    │
│                                                         │
│  [Flag] TAIWAN SEMICONDUCTOR MANUFACTURING CO           │
│  TW · Semiconductor Manufacturing · DUNS: 123456789     │
│  [In portfolio since Sep 2024]  [Remove from portfolio] │
├──────────────────────────┬──────────────────────────────┤
│  SCORE DIAL              │  SIGNAL BREAKDOWN            │
│                          │                              │
│      72                  │  Financial    45  ████░░ 30% │
│   HIGH RISK              │  News         80  ██████ 25% │
│  ↑ +8 this week          │  Shipping     60  █████░ 20% │
│  model: heuristic_v0     │  Geopolitical 90  ██████ 15% │
│  scored: 2h ago          │  Macro        55  ████░░ 10% │
│  completeness: 91%       │                              │
├──────────────────────────┴──────────────────────────────┤
│  SCORE HISTORY (line chart, 90 days, Recharts)          │
│  [7d] [30d] [90d] [1y]  toggle buttons                  │
├─────────────────────────────────────────────────────────┤
│  WHAT'S DRIVING THIS SCORE (SHAP waterfall)             │
│                                                         │
│  +18  Country political risk     ████████████████       │
│  +12  Negative news (30d)        █████████░░░           │
│   +8  Altman Z-Score             ██████░░░              │
│   -5  Strong shipping volume     ████░░  (positive)     │
│   -3  Low debt-to-equity         ██░░    (positive)     │
│                                                         │
│  "Score is {X} above/below average for {industry}"      │
├─────────────────────────────────────────────────────────┤
│  RECENT NEWS (latest articles linked to this supplier)  │
│  [All] [Negative] [Positive] [Neutral]  filter tabs     │
│                                                         │
│  🔴 Reuters · 2h ago                                    │
│  "TSMC faces power shortage amid Taiwan drought"        │
│  Sentiment: -0.72  Topics: [disaster] [regulatory]     │
│  Score impact: +4 points                                │
│                                                         │
│  [Load more articles]                                   │
├─────────────────────────────────────────────────────────┤
│  ACTIVE ALERTS                                          │
│  [score_spike: +18pts · High · 2h ago · Investigating]  │
└─────────────────────────────────────────────────────────┘
```

#### Score Dial
- Large circular gauge (SVG or CSS), colour matches risk level
- Centre: score number in DM Serif Display, 64px
- Below score: risk level label
- Below label: 7d delta (↑+8 in red, ↓-5 in green, →0 in grey)
- Footer: model version, scored timestamp, data completeness percentage

#### SHAP Waterfall
- Horizontal bar chart showing top 5 positive and top 5 negative contributors
- Bars right of centre = increases risk (red)
- Bars left of centre = decreases risk (green)
- Click any bar → tooltip with full explanation text from `SignalContribution.explanation`
- Below chart: 1-sentence benchmark — "This score is 14 points above average for semiconductor manufacturers"

#### Score History Chart
- Recharts AreaChart with gradient fill
- Colour of line and fill changes with current risk level
- Risk threshold lines at 40 and 70 (dashed, labelled)
- Hover tooltip: date, score, risk level
- Toggle buttons for time window: 7d / 30d / 90d / 1y
- If < 7 days of data: show "Gathering data" placeholder, not empty chart

#### Data Staleness Indicator
- If `financial_data_is_stale = true`: yellow warning banner
  "Financial data is {N} days old. SEC filing may be overdue."
- If `data_completeness < 0.5`: grey info banner
  "Score based on {X}% of available signals. {missing_sources} data unavailable."

#### Acceptance Criteria
- [ ] Score dial renders for all risk levels including `insufficient_data`
- [ ] SHAP waterfall renders for every supplier with a score
- [ ] SHAP bars are sorted: largest absolute contribution at top
- [ ] News articles link to original source (open in new tab)
- [ ] Score history chart shows at minimum 7 days when available, placeholder when not
- [ ] Time window toggle updates chart without full page reload
- [ ] Staleness banner shows when `financial_data_is_stale = true`
- [ ] Low completeness banner shows when `data_completeness < 0.5`
- [ ] Page loads in < 2 seconds
- [ ] Score dial animates on load (sweep from 0 to final score, 600ms)

---

### F03 — Alert Centre

**Route:** `/alerts`
**Primary user:** Maya — triage and action

#### Layout

```
┌─────────────────────────────────────────────────────────┐
│  Alerts                          [Mark all read]        │
│  [New (8)] [Investigating (3)] [Resolved] [All]         │
├───────────────────────────────────────┬─────────────────┤
│  ALERT LIST                           │  ALERT DETAIL   │
│                                       │  (right panel,  │
│  🔴 TSMC — Score Spike                │  opens on click)│
│  Risk rose 18pts in 7 days · 2h ago   │                 │
│  [Investigating]                      │  [full detail]  │
│                                       │                 │
│  🔴 Vendor Corp — Sanctions Hit       │                 │
│  Added to OFAC SDN list · 4h ago      │                 │
│  [New]                                │                 │
│                                       │                 │
│  🟡 Samsung Elec — High Threshold     │                 │
│  Score crossed 70 · 1d ago            │                 │
│  [New]                                │                 │
└───────────────────────────────────────┴─────────────────┘
```

#### Alert List
- Grouped by status tabs: New / Investigating / Resolved / All
- Each row: severity icon (🔴🟡⚪), supplier name, alert title, time ago, status badge
- Unread alerts have left border accent in risk colour
- Click row: opens detail panel on right (desktop) or full page (mobile)
- Real-time: WebSocket push adds new alert to top of New tab with subtle highlight animation

#### Alert Detail Panel
```
┌──────────────────────────────────────────────────┐
│  [← Back]                          [X Close]     │
│                                                  │
│  🔴  Score Spike — High                          │
│  Taiwan Semiconductor Manufacturing Co           │
│  Fired: March 4, 2025 at 06:05 UTC               │
│                                                  │
│  Score went from 54 → 72 (+18 points)            │
│  over the past 7 days.                           │
│                                                  │
│  Primary drivers:                                │
│  • Negative news volume: +12pts                  │
│  • Country political risk: +8pts                 │
│  • Shipping volume drop: +4pts                   │
│                                                  │
│  ─────────────────────────────────────           │
│  STATUS                                          │
│  [New ▼]   ← status dropdown                    │
│                                                  │
│  INVESTIGATION NOTE                              │
│  [Text area — add your note...]                  │
│                                                  │
│  [Save]    [View Supplier Profile →]             │
│                                                  │
│  ─────────────────────────────────────           │
│  SHARE                                           │
│  [Copy link]  [Send to Slack]  [Email alert]     │
└──────────────────────────────────────────────────┘
```

#### Acceptance Criteria
- [ ] New alerts appear in real-time via WebSocket without page refresh
- [ ] New alert arrival: brief highlight animation (500ms yellow flash on row)
- [ ] Tab counts update in real-time as alerts arrive and status changes
- [ ] Status dropdown enforces valid transitions (see API_SPEC.md Section 7.4)
- [ ] Note saves on blur or Ctrl+Enter (not requiring explicit Save button click)
- [ ] "Send to Slack" copies formatted message to clipboard if no Slack configured, sends via webhook if configured
- [ ] Email alert sends formatted email to tenant alert recipients
- [ ] "Mark all read" moves all New → Investigating only for admin role; prompts confirmation
- [ ] Alert detail panel on mobile is full-screen sheet (shadcn Sheet component)
- [ ] Email notification dispatched within 5 minutes of alert firing

#### Alert Email Template

```
Subject: 🔴 [HIGH] TSMC — Risk score rose 18 points | Supplier Risk Platform

─────────────────────────────────────────────
SUPPLIER ALERT
─────────────────────────────────────────────

Supplier:    Taiwan Semiconductor Manufacturing Co
Alert type:  Score Spike
Severity:    HIGH
Fired:       March 4, 2025 at 6:05 AM UTC

WHAT HAPPENED
Score increased from 54 → 72 (+18 points) over 7 days.

PRIMARY DRIVERS
• Negative news volume (30 days): +12 points
• Country political risk: +8 points
• Shipping volume decline: +4 points

─────────────────────────────────────────────
[View Full Details]  [Mark as Investigating]
─────────────────────────────────────────────

You're receiving this because you're an admin on [Tenant Name].
Manage notification settings → [Settings link]
```

---

### F04 — Add / Import Suppliers

**Route:** `/suppliers/add`
**Primary user:** Sam (bulk setup) and Maya (occasional single adds)

#### Single Add Flow

```
Step 1: Search
  [Search by company name...]
  As Maya types, typeahead dropdown shows matches:
    ✓ Taiwan Semiconductor Manufacturing Co  (TW · Semiconductor)  98% match
    ? TSMC Solar Co Ltd                      (TW · Renewable)      72% match

Step 2: Confirm
  Show supplier card:
  - Canonical name + aliases
  - Country, industry
  - Current risk score (if available) — "Current score: 72/100 (High Risk)"
  - Data available: "18 months of signal history available"
  [Add to Portfolio]  [Cancel]

Step 3: Optional metadata
  Internal ID: [VEND-0042]
  Tags: [critical] [tier-1]  [+ Add tag]
  Custom name: [Our TSMC Account]  (optional)
  [Confirm Add]
```

#### Bulk Import Flow

```
Step 1: Download template
  "Download CSV template" → downloads template with columns:
  name, country, internal_id, tags (semicolon-separated)

Step 2: Upload
  Drag-and-drop zone or "Browse files"
  Shows: file name, row count, validation status

Step 3: Resolution review
  Table showing each row:
  Row  Name                  Match                    Confidence  Action
  1    TSMC                  Taiwan Semiconductor...  98%         ✓ Auto-added
  8    XYZ Holdings Ltd      XYZ GmbH (DE)            61%         ⚠ Review needed
  23   Unknown Corp          No match found           0%          ✗ Unresolved

  [Edit] button on each Review/Unresolved row → opens resolution drawer
  [Confirm Import] button only active when no rows in Review state

Step 4: Complete
  "42 of 45 suppliers added. 3 unresolved — review later in Settings."
  [View Portfolio]  [Resolve remaining]
```

#### Acceptance Criteria
- [ ] Typeahead search triggers after 2 characters, debounced 300ms
- [ ] Typeahead shows max 5 results, with confidence score
- [ ] Suppliers already in portfolio shown with "Already added" badge (not re-addable)
- [ ] CSV upload rejects files > 5MB with clear error message
- [ ] CSV upload rejects files with > 500 rows with clear error message
- [ ] CSV upload rejects missing `name` column with specific error
- [ ] Low confidence matches (< 70%) require manual confirmation — never auto-added
- [ ] Import progress shown as a live progress bar (polls `/api/v1/portfolio/imports/:id` every 2s)
- [ ] Import completes within 60 seconds for 500 suppliers
- [ ] Plan limit enforced: if import would exceed plan limit, show exactly how many will be added vs skipped

---

### F05 — Account & Alert Settings

**Route:** `/settings`
**Primary user:** Sam

#### Sub-sections

**Alert Rules** (`/settings/alerts`)
```
Score Spike Threshold:  [15] points in [7] days
High Risk Threshold:    [70] (score above this triggers alert)

Notification Channels:
  Email
    [✓] Enabled
    Recipients: [maya@company.com ×] [rohan@company.com ×] [+ Add]
    
  Slack
    [✓] Enabled
    Webhook URL: [https://hooks.slack.com/services/...]
    [Test Webhook]  → sends "✅ Test from Supplier Risk Platform" to the channel
    
  Webhook (API)
    [ ] Enabled
    URL: [https://...]
    Secret: [auto-generated, copy button]
```

**User Management** (`/settings/users`)
```
[Invite User] button → modal:
  Email: [...]
  Role: [Admin ▼] or [Viewer ▼]
  [Send Invite]

User list:
  Name         Email                Role     Last Active    Action
  Maya Patel   maya@company.com     Admin    2h ago         [Remove]
  Rohan Mehta  rohan@company.com    Viewer   3d ago         [Remove] [Edit role]

Pending invites section below active users.
```

**Billing** (`/settings/billing`)
- Current plan, usage (suppliers used / limit), next billing date
- Upgrade button for non-enterprise plans → links to Stripe customer portal

#### Audit Log
- Visible to admins only at `/settings/audit`
- Table: Timestamp, User, Action, Details
- Events logged:
  - Supplier added/removed
  - Alert status changed (with previous → new status)
  - Alert rule settings changed (with before/after values)
  - User invited/removed
  - Webhook configured/tested
  - CSV import (with summary)
- Retention: 90 days on display (full retention in backend logs)
- Export: CSV download button

#### Acceptance Criteria
- [ ] Slack webhook test sends message synchronously — success/failure shown immediately (< 3s)
- [ ] Webhook URL must start with `https://` — plain HTTP rejected with error message
- [ ] All setting changes appear in audit log within 30 seconds
- [ ] Admin cannot remove themselves — "Remove" button disabled for own row
- [ ] Email recipients validated as valid email format before saving
- [ ] Pending invites expire after 7 days (shown in UI with expiry date)
- [ ] Role change (Admin → Viewer) requires confirmation modal

---

## 5. First-Run Onboarding Flow

**Route:** `/onboarding`
**Triggered:** Automatically on first login if portfolio is empty.
**Goal:** Get Maya to her first meaningful score within 10 minutes.

```
Step 1 of 4: Welcome (10 seconds)
  "Welcome to Supplier Risk Platform, [Company Name]"
  "Let's get your first suppliers monitoring in under 10 minutes."
  [Get Started]

Step 2 of 4: Add Suppliers
  Choice:
    [Upload CSV]  — "I have a supplier list ready"
    [Add manually]  — "I'll add a few suppliers to start"
    [Skip for now]  — "I'll explore first"

Step 3 of 4: Configure Alerts
  "Who should receive risk alerts?"
  Email: [pre-filled with current user's email]  [+ Add another]
  "Alert me when a supplier's score rises by more than [15] points"
  "Alert me when a supplier's score exceeds [70]"
  [Save & Continue]

Step 4 of 4: Done
  "You're all set!"
  "We're gathering data for your {N} suppliers. Scores will be ready in 24 hours."
  [Go to Dashboard]
```

**Rules:**
- Onboarding only shown once — dismissed permanently on "Go to Dashboard" or "Skip"
- State stored in user preferences (not localStorage)
- Skip at any step goes directly to dashboard

---

## 6. Mobile Experience

Maya checks the app on her phone before 8am. Mobile must be first-class, not an afterthought.

**Breakpoints:**
```
Mobile:   < 768px
Tablet:   768px – 1023px
Desktop:  ≥ 1024px
```

**Mobile-specific behaviour:**

| Feature | Mobile Behaviour |
|---|---|
| Sidebar | Hidden. Hamburger → full-screen slide-in nav |
| Dashboard table | Simplified: Name + Risk Score + Alerts only. Tap row → profile |
| Supplier profile | Score dial full-width. Sections stack vertically. |
| Alert centre | No split panel. Tap alert → full-screen detail |
| SHAP waterfall | Horizontal scroll if too wide |
| CSV import | Upload only — CSV template download via email instead |
| Settings | All sections accessible, form inputs touch-friendly (min 44px tap targets) |

**Mobile-first alert notification:**
- Push notifications via browser Web Push API (Phase 4)
- For MVP: email is the mobile notification channel

---

## 7. Data Freshness Indicators

Users must always know how old the data is. Never show a score without context.

**Score freshness banner (shown on every page with a score):**
```
If scored_at < 6 hours ago:     No banner (normal)
If 6–12 hours ago:              Grey info: "Score updated 8h ago"
If 12–24 hours ago:             Yellow warning: "Score may be outdated — updated 14h ago"
If > 24 hours ago:              Red warning: "Score is stale — last updated {date}"
```

**Individual signal freshness:**
- Financial data: show filing date and staleness_days on supplier profile
- News data: show "last article: {time ago}" on news section header
- Shipping data: show "last port call data: {time ago}"

---

## 8. Non-Functional Requirements

| Requirement | Target | How Measured |
|---|---|---|
| API response time p95 | < 300ms | Datadog APM |
| Dashboard load time | < 2 seconds | Lighthouse, real user monitoring |
| Score update frequency | Every 6 hours | Airflow DAG monitoring |
| Alert dispatch latency | < 5 minutes from trigger | Alert fired_at vs email delivered_at |
| Uptime SLA | 99.9% | Datadog synthetic monitoring |
| Data retention | 2 years of score history | Snowflake retention policy |
| Max portfolio size (MVP) | 500 suppliers | Plan limit enforcement |
| Max portfolio size (Enterprise) | 5,000 suppliers | Load tested |
| Concurrent users per tenant | 50 (MVP) | Load tested |
| Accessibility | WCAG 2.1 AA | axe-core automated scan |
| WebSocket reconnect | < 5 seconds | Client-side monitoring |

---

## 9. Pricing Tiers

| Plan | Price | Suppliers | Users | Channels | API Access |
|---|---|---|---|---|---|
| Starter | $299/mo | 25 | 3 | Email only | ❌ |
| Growth | $599/mo | 100 | 10 | Email + Slack | ❌ |
| Pro | $999/mo | 500 | Unlimited | All + Webhook | ✅ |
| Enterprise | Custom | Unlimited | Unlimited | All + custom SLA | ✅ |

**Plan enforcement in UI:**
- Approaching limit (> 80% used): yellow banner "You're using 82 of 100 suppliers. Upgrade for more."
- At limit: prevent adding more, show upgrade prompt inline at the add button
- Upgrade CTA always links to `/settings/billing`
- Downgrade: if tenant downgrades and is over new limit, they can't add but existing suppliers are NOT removed — they just can't add more until under limit

---

## 10. Phase Roadmap & Sequence

### Phase 3 — MVP (Target: Month 4–5)

**Month 4 — Core product (backend-first)**
- Week 1–2: FastAPI backend, all API endpoints from API_SPEC.md
- Week 3: React shell, routing, auth integration (Auth0)
- Week 4: F01 Portfolio Dashboard + F04 Add/Import Suppliers

**Month 5 — Complete MVP**
- Week 1: F02 Supplier Profile Page
- Week 2: F03 Alert Centre + WebSocket
- Week 3: F05 Settings + onboarding flow
- Week 4: QA, performance testing, fix P0/P1 bugs

**MVP launch criteria (all must pass):**
- [ ] All F01–F05 acceptance criteria green
- [ ] API p95 < 300ms under 50 concurrent users
- [ ] Dashboard loads < 2s with 200-supplier portfolio
- [ ] Alert dispatches within 5 minutes (tested with real email)
- [ ] CSV import works for 500-row file end-to-end
- [ ] Auth0 SSO login works
- [ ] Zero P0 bugs (app crash, data loss, security issue)

### Phase 4 — Growth Features (Month 6+)
- F06: Risk Map (Mapbox GL + GeoJSON from API)
- F07: Weekly PDF Report (auto-generated, emailed Monday 06:00 tenant timezone)
- F08: ERP integrations (NetSuite, SAP Ariba CSV export)
- F09: EU CSDD Compliance Report

---

*See API_SPEC.md for all endpoint contracts referenced by these features.*
*See ML_SPEC.md for score, SHAP, and data_completeness definitions.*
*See ARCHITECTURE.md for WebSocket implementation details.*
