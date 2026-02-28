
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
    icon?: React.ReactNode
}

export function Label({ children, className = '', required, htmlFor, icon }: LabelProps) {
    return (
        <label htmlFor={htmlFor} className={`flex items-center gap-1.5 text-sm font-semibold text-slate-700 ${className}`}>
            {icon && (
                <span className="flex items-center justify-center w-[22px] h-[22px] rounded-md bg-gradient-to-b from-white to-slate-50/80 border border-slate-200/80 shadow-[0_1px_1px_rgba(0,0,0,0.02)] text-slate-500 [&_svg]:!w-3.5 [&_svg]:!h-3.5">
                    {icon}
                </span>
            )}
            <span className="flex items-center">
                {children}
                {required && <span className="text-red-500 ml-0.5">*</span>}
            </span>
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
    icon?: React.ReactNode
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
    icon,
}: CommonRichInputProps) {
    return (
        <div className="space-y-1.5">
            {label && <Label required={required} icon={icon}>{label}</Label>}
            <div className="relative w-full rounded-xl border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] focus-within:bg-white focus-within:ring-[3px] focus-within:ring-primary/10 focus-within:border-primary/30 transition-all duration-200">
                <RichMentionInput
                    value={value}
                    onChange={onChange}
                    inputParams={mentionParams}
                    placeholder={placeholder}
                    multiline
                    inputClassName={`w-full px-3 text-sm border-0 bg-transparent focus:ring-0 focus:outline-none text-slate-700 ${rows === 1 ? 'min-h-[38px] py-2' : 'min-h-[56px] py-2.5'}`}
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
    disabled?: boolean
    className?: string
    icon?: React.ReactNode
}

export function CommonSelect({
    label,
    value,
    onChange,
    options,
    placeholder,
    required,
    disabled = false,
    className = '',
    icon,
}: CommonSelectProps) {
    return (
        <div className={`space-y-1.5 ${className}`}>
            {label && <Label required={required} icon={icon}>{label}</Label>}
            <select
                value={value}
                onChange={(e) => onChange(e.target.value)}
                disabled={disabled}
                className={`w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white outline-none transition-all appearance-none ${disabled
                    ? 'cursor-not-allowed opacity-60 bg-slate-50'
                    : 'hover:bg-slate-50 focus:ring-2 focus:ring-primary/20 focus:border-primary/50 cursor-pointer shadow-sm'
                    }`}
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
    icon?: React.ReactNode
}


export function CommonSwitch({ label, checked, onChange, description, icon }: CommonSwitchProps) {
    return (
        <div className="flex flex-row items-center justify-between rounded-xl border border-slate-200 p-3 shadow-sm bg-white hover:bg-slate-50 transition-colors">
            <div className="space-y-0.5 pr-4">
                <Label icon={icon}>{label}</Label>
                {description && (
                    <p className="text-xs text-slate-500 leading-relaxed">{description}</p>
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
    icon?: React.ReactNode
}

export function CommonSegmentedControl({ label, value, onChange, options, className = '', icon }: CommonSegmentedControlProps) {
    return (
        <div className={`space-y-1.5 ${className}`}>
            {label && <Label icon={icon}>{label}</Label>}
            <div className="flex p-0.5 bg-slate-100/80 rounded-xl border border-slate-200/60 shadow-inner">
                {options.map((option) => {
                    const isSelected = value === option.value
                    return (
                        <button
                            key={option.value}
                            onClick={() => onChange(option.value)}
                            className={`flex-1 text-sm font-semibold py-1.5 px-2 rounded-lg transition-all duration-200 ${isSelected
                                ? 'bg-white text-primary shadow ring-1 ring-slate-200/50'
                                : 'text-slate-500 hover:text-slate-700 hover:bg-white/50'
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
    icon?: React.ReactNode
}

export function CommonOutputList({ label, outputs, description, icon }: CommonOutputListProps) {
    if (!outputs || outputs.length === 0) return null

    return (
        <div className="space-y-2.5 pt-3 border-t border-slate-200/80 mt-4 overflow-hidden">
            <div className="flex items-center justify-between">
                {label && <Label icon={icon}>{label}</Label>}
                <div className="text-xs font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full border border-slate-200/50">
                    Output
                </div>
            </div>
            {description && <p className="text-xs text-slate-500 mb-2">{description}</p>}

            <div className="space-y-1.5">
                {outputs.map((output, idx) => (
                    <div
                        key={`${output}-${idx}`}
                        className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-xl border border-slate-200/80 bg-slate-50/50 text-sm font-mono text-slate-600 shadow-sm"
                    >
                        <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-sm" />
                        <span>{output}</span>
                    </div>
                ))}
            </div>
        </div>
    )
}
