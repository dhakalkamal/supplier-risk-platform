import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { formatDistanceToNow, format, parseISO } from "date-fns";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatTimeAgo(date: string): string {
  return formatDistanceToNow(parseISO(date), { addSuffix: true });
}

export function formatScore(score: number | null): string {
  if (score === null) return "—";
  return String(score);
}

export function getScoreTrend(delta: number): "up" | "down" | "flat" {
  if (delta > 3) return "up";
  if (delta < -3) return "down";
  return "flat";
}

export function formatDate(date: string): string {
  return format(parseISO(date), "MMM d, yyyy");
}

export function getCountryFlag(countryCode: string): string {
  const codePoints = [...countryCode.toUpperCase()].map(
    (char) => 0x1f1e0 + char.charCodeAt(0) - 65,
  );
  return String.fromCodePoint(...codePoints);
}

export function getGreeting(name: string): string {
  const hour = new Date().getHours();
  if (hour < 12) return `Good morning, ${name}`;
  if (hour < 17) return `Good afternoon, ${name}`;
  return `Good evening, ${name}`;
}
