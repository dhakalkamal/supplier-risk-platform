import { useState } from "react";
import { NavLink } from "react-router-dom";
import { Menu, LayoutDashboard, Building2, Bell, Settings, ShieldAlert } from "lucide-react";
import { useAuth0 } from "@auth0/auth0-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { usePortfolioSummary } from "@/hooks/usePortfolio";

const navItems = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Suppliers", href: "/suppliers", icon: Building2 },
  { label: "Alerts", href: "/alerts", icon: Bell },
  { label: "Settings", href: "/settings", icon: Settings },
];

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const { user } = useAuth0();
  const { data: summary } = usePortfolioSummary();
  const unreadCount = summary?.unread_alerts_count ?? 0;

  return (
    <div className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between px-4 py-3 bg-[--color-bg-surface] border-b border-[--color-border]">
      <div className="flex items-center gap-2">
        <div className="h-7 w-7 rounded-lg bg-[--color-brand] flex items-center justify-center">
          <ShieldAlert className="h-4 w-4 text-white" />
        </div>
        <span className="text-sm font-semibold text-[--color-text-primary]">SupplierRisk</span>
      </div>

      <Sheet open={open} onOpenChange={setOpen}>
        <button
          onClick={() => setOpen(true)}
          className="p-2 rounded-md text-[--color-text-secondary] hover:text-[--color-text-primary] hover:bg-[--color-bg-elevated]"
          aria-label="Open navigation"
        >
          <Menu className="h-5 w-5" />
        </button>

        <SheetContent side="left">
          <SheetHeader>
            <SheetTitle>Navigation</SheetTitle>
          </SheetHeader>

          <nav className="flex flex-col gap-1 px-4 pb-4">
            {navItems.map((item) => (
              <SheetClose key={item.href} asChild>
                <NavLink
                  to={item.href}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
                      "text-[--color-text-secondary] hover:text-[--color-text-primary] hover:bg-[--color-bg-elevated]",
                      isActive &&
                        "bg-[--color-bg-elevated] text-[--color-text-primary] border-l-2 border-[--color-brand] pl-[10px]",
                    )
                  }
                >
                  <item.icon className="h-5 w-5 shrink-0" />
                  {item.label}
                  {item.label === "Alerts" && unreadCount > 0 && (
                    <span className="ml-auto min-w-5 h-5 rounded-full bg-[--color-brand] text-white text-xs font-semibold flex items-center justify-center px-1">
                      {unreadCount > 99 ? "99+" : unreadCount}
                    </span>
                  )}
                </NavLink>
              </SheetClose>
            ))}
          </nav>

          <div className="mt-auto px-4 py-4 border-t border-[--color-border]">
            <div className="flex items-center gap-3">
              <div className="h-8 w-8 rounded-full bg-[--color-brand] flex items-center justify-center text-xs font-semibold text-white shrink-0">
                {user?.name
                  ? user.name
                      .split(" ")
                      .map((n) => n[0])
                      .join("")
                      .toUpperCase()
                      .slice(0, 2)
                  : "?"}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-[--color-text-primary] truncate">
                  {user?.name ?? "User"}
                </p>
                <p className="text-xs text-[--color-text-muted] truncate">{user?.email ?? ""}</p>
              </div>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
