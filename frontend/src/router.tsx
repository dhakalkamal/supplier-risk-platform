import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import DashboardPage from "@/pages/DashboardPage";
import SuppliersPage from "@/pages/SuppliersPage";
import AddSupplierPage from "@/pages/AddSupplierPage";
import SupplierProfilePage from "@/pages/SupplierProfilePage";
import AlertsPage from "@/pages/AlertsPage";
import SettingsPage from "@/pages/SettingsPage";
import UsersSettingsPage from "@/pages/UsersSettingsPage";
import OnboardingPage from "@/pages/OnboardingPage";

export const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/dashboard" replace /> },
  {
    element: <AppShell />,
    children: [
      { path: "/dashboard", element: <DashboardPage /> },
      { path: "/suppliers", element: <SuppliersPage /> },
      { path: "/suppliers/add", element: <AddSupplierPage /> },
      { path: "/suppliers/:supplierId", element: <SupplierProfilePage /> },
      { path: "/alerts", element: <AlertsPage /> },
      { path: "/settings", element: <SettingsPage /> },
      { path: "/settings/users", element: <UsersSettingsPage /> },
    ],
  },
  { path: "/onboarding", element: <OnboardingPage /> },
]);
