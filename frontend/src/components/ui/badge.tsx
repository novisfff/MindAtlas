import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"
import { uiRadius } from "@/components/ui/styles"

const badgeVariants = cva(
    cn(
        "inline-flex items-center border px-2.5 py-1 text-xs font-medium transition-colors focus:outline-none focus:ring-[3px] focus:ring-primary/12 focus:ring-offset-2",
        uiRadius.pill
    ),
    {
        variants: {
            variant: {
                default:
                    "border-transparent bg-primary text-primary-foreground hover:bg-primary/88",
                secondary:
                    "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/88",
                destructive:
                    "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/88",
                outline: "border-slate-200/80 bg-background/86 text-foreground/82 shadow-[0_2px_8px_rgba(15,23,42,0.04)] dark:border-slate-700/80 dark:bg-background/70",
            },
        },
        defaultVariants: {
            variant: "default",
        },
    }
)

export interface BadgeProps
    extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> { }

function Badge({ className, variant, ...props }: BadgeProps) {
    return (
        <div className={cn(badgeVariants({ variant }), className)} {...props} />
    )
}

export { Badge, badgeVariants }
