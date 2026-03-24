import { Loader2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

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
        <DialogContent className="sm:max-w-md rounded-[24px] border-slate-200 bg-white p-6 shadow-[0_24px_70px_rgba(15,23,42,0.2)]">
        <DialogHeader className="space-y-2 text-left">
          <DialogTitle className="whitespace-pre-line text-lg font-semibold leading-snug text-slate-900">
            {title}
          </DialogTitle>
          <DialogDescription className="whitespace-pre-line text-sm leading-7 text-slate-600">
            {description}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter className="mt-2 gap-2 sm:gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={isLoading}
            className="inline-flex min-h-10 items-center justify-center rounded-xl border border-slate-200 bg-white px-4 py-2 text-center text-sm font-medium leading-tight whitespace-pre-line text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-50"
          >
            {cancelText}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isLoading}
            className={`inline-flex min-h-10 items-center justify-center rounded-xl px-4 py-2 text-center text-sm font-medium leading-tight whitespace-pre-line text-white transition-colors disabled:opacity-50 ${
              variant === 'destructive'
                ? 'bg-red-600 hover:bg-red-700'
                : 'bg-primary hover:bg-primary/90'
            }`}
          >
            {isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {confirmText}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
