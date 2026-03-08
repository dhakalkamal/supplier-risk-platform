import { useState, useEffect } from "react";
import { NavLink } from "react-router-dom";
import { Loader2, Trash2, UserPlus } from "lucide-react";
import { useAuth0 } from "@auth0/auth0-react";
import { useUsers, useInviteUser, useRemoveUser } from "@/hooks/useSettings";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/SkeletonRow";
import { cn, formatTimeAgo } from "@/lib/utils";
import type { TenantUser } from "@/types/api";

function SettingsNav() {
  const base = "border-b-2 -mb-px px-4 py-3 text-sm font-medium transition-colors";
  return (
    <nav className="flex gap-1 border-b border-[--color-border] px-6 mb-6">
      <NavLink
        to="/settings"
        end
        className={({ isActive }) =>
          cn(base, isActive ? "border-[--color-brand] text-[--color-text-primary]" : "border-transparent text-[--color-text-secondary] hover:text-[--color-text-primary]")
        }
      >
        Alert Rules
      </NavLink>
      <NavLink
        to="/settings/users"
        className={({ isActive }) =>
          cn(base, isActive ? "border-[--color-brand] text-[--color-text-primary]" : "border-transparent text-[--color-text-secondary] hover:text-[--color-text-primary]")
        }
      >
        Users
      </NavLink>
    </nav>
  );
}

// ── Invite modal ───────────────────────────────────────────────────────────────

function InviteModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const inviteUser = useInviteUser();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "viewer">("viewer");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await inviteUser.mutateAsync({ email, role });
      setEmail("");
      setRole("viewer");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invite failed");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite User</DialogTitle>
          <DialogDescription>
            They'll receive an email with a link to join your team. Invites expire after 7 days.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 mt-2">
          <div>
            <label className="block text-sm font-medium text-[--color-text-secondary] mb-1.5">
              Email
            </label>
            <Input
              type="email"
              placeholder="colleague@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-[--color-text-secondary] mb-1.5">
              Role
            </label>
            <Select value={role} onChange={(e) => setRole(e.target.value as "admin" | "viewer")}>
              <option value="viewer">Viewer — read-only access</option>
              <option value="admin">Admin — full access</option>
            </Select>
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={inviteUser.isPending}>
              {inviteUser.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Send Invite
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── User Row ───────────────────────────────────────────────────────────────────

function UserRow({ user, isSelf }: { user: TenantUser; isSelf: boolean }) {
  const removeUser = useRemoveUser();
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-[--color-border]">
      <div className="min-w-0">
        <p className="text-sm font-medium text-[--color-text-primary] truncate">{user.email}</p>
        <p className="text-xs text-[--color-text-muted]">
          Last active: {formatTimeAgo(user.last_active_at)}
        </p>
      </div>
      <div className="flex items-center gap-4 ml-4 shrink-0">
        <span
          className={cn(
            "rounded-full px-2.5 py-0.5 text-xs font-medium",
            user.role === "admin"
              ? "bg-[--color-brand]/20 text-[--color-brand]"
              : "bg-[--color-bg-elevated] text-[--color-text-muted]",
          )}
        >
          {user.role}
        </span>
        {isSelf ? (
          <span className="text-xs text-[--color-text-muted]">You</span>
        ) : confirming ? (
          <div className="flex gap-2">
            <button
              onClick={() => removeUser.mutate(user.user_id)}
              className="text-xs text-red-400 hover:text-red-300"
              disabled={removeUser.isPending}
            >
              {removeUser.isPending ? "Removing…" : "Confirm"}
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="text-xs text-[--color-text-muted] hover:text-[--color-text-secondary]"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirming(true)}
            className="p-1.5 rounded text-[--color-text-muted] hover:text-red-400 hover:bg-red-950/30 transition-colors"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

// ── Users Settings Page ────────────────────────────────────────────────────────

export default function UsersSettingsPage() {
  const [inviteOpen, setInviteOpen] = useState(false);
  const { user: authUser } = useAuth0();
  const { data: users, isLoading } = useUsers();

  useEffect(() => {
    document.title = "Users — Supplier Risk Platform";
  }, []);

  return (
    <div className="pb-12">
      <div className="px-6 py-6 border-b border-[--color-border]">
        <h1
          className="text-2xl text-[--color-text-primary]"
          style={{ fontFamily: "'DM Serif Display', serif" }}
        >
          Settings
        </h1>
      </div>
      <div className="pt-6">
        <SettingsNav />
        <div className="px-6 max-w-2xl">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-[--color-text-secondary] uppercase tracking-wider">
              Team Members
            </h2>
            <Button size="sm" onClick={() => setInviteOpen(true)}>
              <UserPlus className="h-4 w-4" /> Invite User
            </Button>
          </div>

          <div className="rounded-xl border border-[--color-border] overflow-hidden">
            {isLoading && (
              Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex items-center justify-between px-4 py-4 border-b border-[--color-border]">
                  <div className="space-y-2">
                    <Skeleton className="h-4 w-40" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                  <Skeleton className="h-6 w-16 rounded-full" />
                </div>
              ))
            )}
            {!isLoading && (users ?? []).map((user) => (
              <UserRow
                key={user.user_id}
                user={user}
                isSelf={user.email === authUser?.email}
              />
            ))}
            {!isLoading && (users ?? []).length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-[--color-text-muted]">
                No users yet.
              </div>
            )}
          </div>
        </div>
      </div>

      <InviteModal open={inviteOpen} onClose={() => setInviteOpen(false)} />
    </div>
  );
}
