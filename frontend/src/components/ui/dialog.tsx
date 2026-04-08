import * as React from "react"
import { createPortal } from "react-dom"
import { cn } from "@/lib/utils"
import { uiChrome, uiSurface } from "@/components/ui/styles"

const Dialog = ({
    open,
    onOpenChange,
    children,
}: {
    open?: boolean
    onOpenChange?: (open: boolean) => void
    children: React.ReactNode
}) => {
    const [isOpen, setIsOpen] = React.useState(open || false)
    const [mounted, setMounted] = React.useState(false)

    React.useEffect(() => {
        if (open !== undefined) {
            setIsOpen(open)
        }
    }, [open])

    React.useEffect(() => {
        setMounted(true)
    }, [])

    React.useEffect(() => {
        const handleEscape = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                if (open !== undefined && onOpenChange) {
                    onOpenChange(false)
                } else {
                    setIsOpen(false)
                }
            }
        }

        if (isOpen) {
            document.addEventListener("keydown", handleEscape)
            const currentCount = Number(document.body.dataset.uiModalCount || "0")
            document.body.dataset.uiModalCount = String(currentCount + 1)
            document.body.style.overflow = "hidden"
        }

        return () => {
            document.removeEventListener("keydown", handleEscape)
            const currentCount = Number(document.body.dataset.uiModalCount || "0")
            const nextCount = Math.max(0, currentCount - 1)
            if (nextCount === 0) {
                delete document.body.dataset.uiModalCount
                document.body.style.overflow = "unset"
                return
            }
            document.body.dataset.uiModalCount = String(nextCount)
        }
    }, [isOpen, onOpenChange, open])

    if (!isOpen || !mounted) return null

    return createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            {/* Backdrop */}
            <div
                className={cn(
                    "fixed inset-0 animate-in fade-in duration-200",
                    uiSurface.overlay
                )}
                onClick={() => {
                    if (open !== undefined && onOpenChange) {
                        onOpenChange(false)
                    } else {
                        setIsOpen(false)
                    }
                }}
            />
            {children}
        </div>,
        document.body
    )
}

const DialogContent = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => (
    <div
        ref={ref}
        data-ui-modal="true"
        className={cn(
            "relative z-[1] mx-4 grid w-[min(100%,40rem)] max-h-[calc(100vh-2rem)] gap-4 overflow-y-auto p-6 text-foreground duration-200 animate-in fade-in-90 zoom-in-95",
            uiChrome.modal,
            className
        )}
        {...props}
    >
        {children}
    </div>
))
DialogContent.displayName = "DialogContent"

const DialogHeader = ({
    className,
    ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
    <div
        className={cn(
            "flex flex-col space-y-1.5 text-center sm:text-left",
            className
        )}
        {...props}
    />
)
DialogHeader.displayName = "DialogHeader"

const DialogFooter = ({
    className,
    ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
    <div
        className={cn(
            "flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2",
            className
        )}
        {...props}
    />
)
DialogFooter.displayName = "DialogFooter"

const DialogTitle = React.forwardRef<
    HTMLParagraphElement,
    React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
    <h3
        ref={ref}
        className={cn(
            "text-lg font-semibold leading-none tracking-tight",
            className
        )}
        {...props}
    />
))
DialogTitle.displayName = "DialogTitle"

const DialogDescription = React.forwardRef<
    HTMLParagraphElement,
    React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
    <p
        ref={ref}
        className={cn("text-sm text-muted-foreground", className)}
        {...props}
    />
))
DialogDescription.displayName = "DialogDescription"

export {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogFooter,
    DialogTitle,
    DialogDescription,
}
