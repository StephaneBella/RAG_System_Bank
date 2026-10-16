import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

import { Icon } from './Icon';
import { Spinner } from './Spinner';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle';
type Size = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
  icon?: string;
  iconFilled?: boolean;
  fullWidth?: boolean;
  children?: ReactNode;
}

const variantClasses: Record<Variant, string> = {
  primary: 'bg-primary-container text-white shadow-sm hover:bg-primary focus-visible:ring-accent',
  secondary:
    'bg-surface-tint text-white shadow-sm border border-transparent hover:bg-primary focus-visible:ring-accent',
  ghost: 'bg-transparent text-on-surface-variant hover:bg-surface-container',
  danger: 'bg-error text-white hover:bg-[#a01616]',
  subtle: 'bg-surface-container text-primary hover:bg-surface-container-high',
};

const sizeClasses: Record<Size, string> = {
  sm: 'h-9 px-3 text-body-sm gap-1.5',
  md: 'h-10 px-4 text-label-md gap-2',
  lg: 'h-12 px-5 text-headline-sm gap-2.5',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    isLoading = false,
    icon,
    iconFilled = false,
    fullWidth = false,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || isLoading}
      className={cn(
        'inline-flex items-center justify-center rounded-lg font-label-md font-semibold transition-all duration-150 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50',
        variantClasses[variant],
        sizeClasses[size],
        fullWidth && 'w-full',
        className,
      )}
      {...rest}
    >
      {isLoading ? (
        <Spinner className="text-current" />
      ) : (
        icon && <Icon name={icon} filled={iconFilled} className="text-[18px]" />
      )}
      {children}
    </button>
  );
});
