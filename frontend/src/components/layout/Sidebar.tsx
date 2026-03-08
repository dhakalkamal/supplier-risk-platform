import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Building2,
  Bell,
  Settings,
  Globe,
  FileText,
  ShieldAlert,
} from "lucide-react";
import { useAuth0 } from "@auth0/auth0-react";
import { cn } from "@/lib/utils";
import { usePortfolioSummary } from "@/hooks/usePortfolio";

interface NavItem {
  label: string;
  href: string;
  icon: React.FC<{ className?: string }>;
  badge?: number;
  disabled?: boolean;
}

function NavItemLink({ item }: { item: NavItem }) {
  if (item.disabled) {
    return (
      <div className="flex items-center gap-3 px-4 py-2.5 rounded-md opacity-40 cursor-not-allowed select-none">
        <item.icon className="h-5 w-5 shrink-0 text-[--color-text-muted]" />
        <span className="hidden lg:inline group-hover:inline text-sm text-[--color-text-muted] whitespace-nowrap transition-opacity duration-200">
          {item.label}
        </span>
        <span className="hidden lg:inline group-hover:inline ml-auto text-xs text-[--color-text-muted] whitespace-nowrap">
          Soon
        </span>
      </div>
    );
  }

  return (
    <NavLink
      to={item.href}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 px-4 py-2.5 rounded-md transition-colors",
          "text-[--color-text-secondary] hover:text-[--color-text-primary] hover:bg-[--color-bg-elevated]",
          isActive &&
            "bg-[--color-bg-elevated] text-[--color-text-primary] border-l-2 border-[--color-brand] pl-[14px]",
        )
      }
    >
      <item.icon className="h-5 w-5 shrink-0" />
      <span className="hidden lg:inline group-hover:inline text-sm whitespace-nowrap transition-opacity duration-200">
        {item.label}
      </span>
      {item.badge != null && item.badge > 0 && (
        <span className="hidden lg:flex group-hover:flex ml-auto items-center justify-center min-w-5 h-5 rounded-full bg-[--color-brand] text-white text-xs font-semibold px-1">
          {item.badge > 99 ? "99+" : item.badge}
        </span>
      )}
    </NavLink>
  );
}

export function Sidebar() {
  const { user } = useAuth0();
  const { data: summary } = usePortfolioSummary();
  const unreadCount = summary?.unread_alerts_count ?? 0;

  const navItems: NavItem[] = [
    { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
    { label: "Suppliers", href: "/suppliers", icon: Building2 },
    { label: "Alerts", href: "/alerts", icon: Bell, badge: unreadCount },
    { label: "Settings", href: "/settings", icon: Settings },
  ];

  const phase4Items: NavItem[] = [
    { label: "Risk Map", href: "/map", icon: Globe, disabled: true },
    { label: "Reports", href: "/reports", icon: FileText, disabled: true },
  ];

  const initials = user?.name
    ? user.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : "?";

  return (
    <aside
      className={cn(
        "group fixed top-0 left-0 h-screen z-30",
        "hidden md:flex flex-col",
        "w-16 lg:w-60 hover:w-60",
        "transition-[width] duration-200 overflow-hidden",
        "bg-[--color-bg-surface] border-r border-[--color-border]",
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 py-5 border-b border-[--color-border] shrink-0">
        <div className="h-8 w-8 rounded-lg bg-[--color-brand] flex items-center justify-center shrink-0">
          <ShieldAlert className="h-5 w-5 text-white" />
        </div>
        <span className="hidden lg:inline group-hover:inline text-sm font-semibold text-[--color-text-primary] whitespace-nowrap">
          SupplierRisk
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-4 space-y-1 overflow-y-auto overflow-x-hidden">
        {navItems.map((item) => (
          <NavItemLink key={item.href} item={item} />
        ))}

        <div className="pt-4 mt-4 border-t border-[--color-border]">
          {phase4Items.map((item) => (
            <NavItemLink key={item.href} item={item} />
          ))}
        </div>
      </nav>

      {/* User */}
      <div className="shrink-0 px-2 py-4 border-t border-[--color-border]">
        <div className="flex items-center gap-3 px-2 py-2 rounded-md">
          <div className="h-8 w-8 rounded-full bg-[--color-brand] flex items-center justify-center shrink-0 text-xs font-semibold text-white">
            {initials}
          </div>
          <div className="hidden lg:block group-hover:block min-w-0">
            <p className="text-sm font-medium text-[--color-text-primary] truncate whitespace-nowrap">
              {user?.name ?? "User"}
            </p>
            <p className="text-xs text-[--color-text-muted] truncate whitespace-nowrap">
              {user?.email ?? ""}
            </p>
          </div>
        </div>
      </div>
    </aside>
  );
}
