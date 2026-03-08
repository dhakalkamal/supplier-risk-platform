import { useEffect } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { useAuth0 } from "@auth0/auth0-react";
import { Sidebar } from "./Sidebar";
import { MobileNav } from "./MobileNav";
import { registerTokenFn } from "@/lib/api";
import { TooltipProvider } from "@/components/ui/tooltip";

function TokenRegistrar() {
  const { getAccessTokenSilently, isAuthenticated } = useAuth0();

  useEffect(() => {
    if (isAuthenticated) {
      registerTokenFn(getAccessTokenSilently);
    }
  }, [isAuthenticated, getAccessTokenSilently]);

  return null;
}

function OnboardingGuard() {
  const { isAuthenticated, isLoading } = useAuth0();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isLoading && isAuthenticated && !localStorage.getItem("onboarding_complete")) {
      navigate("/onboarding", { replace: true });
    }
  }, [isLoading, isAuthenticated, navigate]);

  return null;
}

export function AppShell() {
  return (
    <TooltipProvider>
      <TokenRegistrar />
      <OnboardingGuard />
      {/* Desktop + tablet sidebar */}
      <Sidebar />
      {/* Mobile top bar + slide-in nav */}
      <MobileNav />
      {/* Main content — offset to account for sidebar */}
      <main className="min-h-screen md:pl-16 lg:pl-60 pt-0 md:pt-0">
        {/* Mobile top bar spacer */}
        <div className="h-14 md:hidden" />
        <Outlet />
      </main>
    </TooltipProvider>
  );
}
