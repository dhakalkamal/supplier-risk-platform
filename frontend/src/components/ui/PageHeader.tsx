import { Button } from "@/components/ui/button";

interface PageHeaderAction {
  label: string;
  onClick: () => void;
  icon?: React.ReactNode;
}

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  action?: PageHeaderAction;
}

export function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 px-6 py-6 border-b border-[--color-border]">
      <div>
        <h1
          className="text-2xl text-[--color-text-primary] leading-tight"
          style={{ fontFamily: "'DM Serif Display', serif" }}
        >
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-sm text-[--color-text-secondary]">{subtitle}</p>
        )}
      </div>
      {action && (
        <Button onClick={action.onClick} className="shrink-0 mt-1">
          {action.icon}
          {action.label}
        </Button>
      )}
    </div>
  );
}
