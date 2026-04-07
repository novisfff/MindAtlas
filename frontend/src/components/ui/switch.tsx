import * as React from "react"
import { cn } from "@/lib/utils"
import { uiRadius } from "@/components/ui/styles"

const Switch = React.forwardRef<
    HTMLButtonElement,
    React.ButtonHTMLAttributes<HTMLButtonElement> & {
        checked?: boolean
        onCheckedChange?: (checked: boolean) => void
    }
>(({ className, checked, onCheckedChange, disabled, ...props }, ref) => (
    <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        ref={ref}
        onClick={(e) => {
            onCheckedChange?.(!checked)
            props.onClick?.(e)
        }}
        className={cn(
            "peer inline-flex h-6 w-11 shrink-0 cursor-pointer items-center border transition-colors focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-primary/12 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
            uiRadius.pill,
            checked
              ? "border-primary bg-primary shadow-[0_6px_16px_rgba(15,23,42,0.14)]"
              : "border-slate-300 bg-slate-200/90 dark:border-slate-700 dark:bg-slate-800",
            className
        )}
        {...props}
    >
        <span
            className={cn(
                "pointer-events-none block h-5 w-5 bg-background shadow-[0_3px_10px_rgba(15,23,42,0.18)] ring-0 transition-transform data-[state=checked]:translate-x-5 data-[state=unchecked]:translate-x-0",
                uiRadius.pill,
                checked ? "translate-x-5" : "translate-x-0"
            )}
        />
    </button>
))
Switch.displayName = "Switch"

export { Switch }
