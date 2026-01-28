import { cn } from "@/lib/utils";

interface LogoProps {
    className?: string;
}

export function Logo({ className }: LogoProps) {
    return (
        <svg
            viewBox="0 0 100 100"
            fill="currentColor"
            xmlns="http://www.w3.org/2000/svg"
            className={cn("", className)}
        >
            <rect x="15" y="15" width="70" height="70" rx="20" fill="none" stroke="currentColor" strokeWidth="10" />
            <line x1="50" y1="20" x2="50" y2="80" stroke="currentColor" strokeWidth="8" opacity="0.3" />
            <line x1="20" y1="50" x2="80" y2="50" stroke="currentColor" strokeWidth="8" opacity="0.3" />
            <circle cx="50" cy="50" r="12" />
            <circle cx="70" cy="30" r="8" />
        </svg>
    );
}
