import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { uiRadius } from '@/components/ui/styles'

interface ConfirmDialogProps {
  isOpen: boolean
  title: string
  description: string
  onConfirm: () => void
  onCancel: () => void
  confirmText?: string
  cancelText?: string
  variant?: 'default' | 'destructive'
  isLoading?: boolean
}

export function ConfirmDialog({
  isOpen,
  title,
  description,
  onConfirm,
  onCancel,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  variant = 'default',
  isLoading = false,
}: ConfirmDialogProps) {
  return (
    <Dialog open={isOpen} onOpenChange={(nextOpen) => { if (!nextOpen) onCancel() }}>
      <DialogContent className="sm:max-w-md p-6">
        <DialogHeader className="space-y-2 text-left">
          <DialogTitle className="whitespace-pre-line text-lg font-semibold leading-snug text-foreground">
            {title}
          </DialogTitle>
          <DialogDescription className="whitespace-pre-line text-sm leading-7 text-muted-foreground">
            {description}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter className="mt-2 gap-2 sm:gap-2">
          <Button
            type="button"
            onClick={onCancel}
            disabled={isLoading}
            variant="outline"
            className={cn('min-h-10 whitespace-pre-line', uiRadius.control)}
          >
            {cancelText}
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={isLoading}
            variant={variant === 'destructive' ? 'destructive' : 'default'}
            className={cn('min-h-10 whitespace-pre-line', uiRadius.control)}
          >
            {isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
