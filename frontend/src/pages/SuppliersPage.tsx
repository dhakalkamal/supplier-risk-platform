import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { SupplierTable } from "@/components/suppliers/SupplierTable";

export default function SuppliersPage() {
  const navigate = useNavigate();

  useEffect(() => {
    document.title = "Suppliers — Supplier Risk Platform";
  }, []);

  return (
    <div className="pb-12">
      <PageHeader
        title="Suppliers"
        subtitle="All suppliers in your monitored portfolio"
        action={{ label: "+ Add Supplier", onClick: () => navigate("/suppliers/add") }}
      />
      <SupplierTable />
    </div>
  );
}
