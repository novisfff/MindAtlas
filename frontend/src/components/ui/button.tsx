import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"
import { uiRadius, uiSurface } from "@/components/ui/styles"

const buttonVariants = cva(
  cn(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap text-sm font-medium transition-[background-color,color,border-color,box-shadow,transform] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-primary/12 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
    uiRadius.control
  ),
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground shadow-[0_10px_24px_rgba(15,23,42,0.14)] hover:bg-primary/92 active:translate-y-[1px]",
        destructive:
          "bg-destructive text-destructive-foreground shadow-[0_10px_24px_rgba(220,38,38,0.18)] hover:bg-destructive/92 active:translate-y-[1px]",
        outline:
          cn(uiSurface.control, "text-foreground hover:bg-muted/55 hover:text-foreground"),
        secondary:
          cn(uiSurface.inset, "text-foreground hover:bg-muted/80"),
        ghost: "text-muted-foreground hover:bg-muted/55 hover:text-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 px-3.5 text-xs",
        lg: "h-11 px-5 text-sm",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
