
import { type ReactNode } from 'react'
import { RichMentionInput } from '../../RichMentionInput'
import type { WorkflowToolDefinition } from '../types'
import { useTranslation } from 'react-i18next'
import { Switch } from '@/components/ui/switch'
import type { InputParam } from '../../../api/tools'

interface LabelProps {
    children: ReactNode
    className?: string
    required?: boolean
    htmlFor?: string
}

export function Label({ children, className = '', required, htmlFor }: LabelProps) {
    return (
        <label htmlFor={htmlFor} className={`text-xs font-medium text-foreground/80 ${className}`}>
            {children}
            {required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
    )
}

interface CommonRichInputProps {
    label?: string
    value: string
    onChange: (value: string) => void
    mentionParams: InputParam[]
    placeholder?: string
    minHeight?: string
    rows?: number
    required?: boolean
    className?: string
}

export function CommonRichInput({
    label,
    value,
    onChange,
    mentionParams,
    placeholder,
    minHeight = '80px',
    rows = 3,
    required,
}: CommonRichInputProps) {
    return (
        <div className="space-y-1.5">
            {label && <Label required={required}>{label}</Label>}
            <div className="rounded-md border bg-background/50 focus-within:ring-1 focus-within:ring-primary/20 focus-within:border-primary/50 transition-all">
                <RichMentionInput
                    value={value}
                    onChange={onChange}
                    inputParams={mentionParams}
                    placeholder={placeholder}
                    multiline
                    rows={rows}
                    className="min-h-[56px]" // Pass minimal height class
                />
            </div>
        </div>
    )
}

interface CommonSelectProps {
    label?: string
    value: string
    onChange: (value: string) => void
    options: { label: string; value: string }[]
    placeholder?: string
    required?: boolean
    className?: string
}

export function CommonSelect({
    label,
    value,
    onChange,
    options,
    placeholder,
    required,
    className = '',
}: CommonSelectProps) {
    return (
        <div className={`space-y-1.5 ${className}`}>
            {label && <Label required={required}>{label}</Label>}
            <select
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className="w-full px-2.5 py-2 text-xs rounded-md border bg-background hover:bg-accent/5 focus:ring-1 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all appearance-none cursor-pointer"
                style={{
                    backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                    backgroundPosition: 'right 0.5rem center',
                    backgroundRepeat: 'no-repeat',
                    backgroundSize: '1.5em 1.5em',
                    paddingRight: '2.5rem'
                }}
            >
                {placeholder && (
                    <option value="" disabled className="text-muted-foreground">
                        {placeholder}
                    </option>
                )}
                {options.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                        {opt.label}
                    </option>
                ))}
            </select>
        </div>
    )
}

interface CommonSwitchProps {
    label: string
    checked: boolean
    onChange: (checked: boolean) => void
    description?: string
}


export function CommonSwitch({ label, checked, onChange, description }: CommonSwitchProps) {
    return (
        <div className="flex flex-row items-center justify-between rounded-lg border p-3 shadow-sm bg-card/50">
            <div className="space-y-0.5">
                <Label>{label}</Label>
                {description && (
                    <p className="text-[10px] text-muted-foreground">{description}</p>
                )}
            </div>
            <Switch checked={checked} onCheckedChange={onChange} />
        </div>
    )
}

interface CommonSegmentedControlProps {
    label?: string
    value: string
    onChange: (value: string) => void
    options: { label: string; value: string }[]
    className?: string
}

export function CommonSegmentedControl({ label, value, onChange, options, className = '' }: CommonSegmentedControlProps) {
    return (
        <div className={`space-y-1.5 ${className}`}>
            {label && <Label>{label}</Label>}
            <div className="flex p-1 bg-muted/50 rounded-lg border border-border/50">
                {options.map((option) => {
                    const isSelected = value === option.value
                    return (
                        <button
                            key={option.value}
                            onClick={() => onChange(option.value)}
                            className={`flex-1 text-xs font-medium py-1.5 px-2 rounded-md transition-all ${isSelected
                                ? 'bg-background text-primary shadow-sm ring-1 ring-border'
                                : 'text-muted-foreground hover:text-foreground hover:bg-background/50'
                                }`}
                        >
                            {option.label}
                        </button>
                    )
                })}
            </div>
        </div>
    )
}

interface CommonOutputListProps {
    label?: string
    outputs: string[]
    description?: string
}

export function CommonOutputList({ label, outputs, description }: CommonOutputListProps) {
    if (!outputs || outputs.length === 0) return null

    return (
        <div className="space-y-2 pt-2 border-t border-border/50">
            {label && <Label>{label}</Label>}
            {description && <p className="text-[10px] text-muted-foreground mb-2">{description}</p>}

            <div className="space-y-1.5">
                {outputs.map((output, idx) => (
                    <div
                        key={`${output}-${idx}`}
                        className="flex items-center gap-2 p-2 rounded-md border bg-muted/20 text-xs font-mono text-muted-foreground"
                    >
                        <div className="w-1.5 h-1.5 rounded-full bg-blue-500/50" />
                        <span>{output}</span>
                    </div>
                ))}
            </div>
        </div>
    )
}
