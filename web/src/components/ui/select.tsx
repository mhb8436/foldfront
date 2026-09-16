import * as React from 'react'
import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * A native select in shadcn clothing.
 *
 * Not a Radix popover: the options are a plain list from the server, and
 * leaving keyboard, screen-reader and mobile behaviour to the browser is
 * better than reimplementing it.
 */
function Select({ className, children, ...props }: React.ComponentProps<'select'>) {
  return (
    <div className="relative w-full">
      <select
        data-slot="select"
        className={cn(
          'border-input flex h-9 w-full appearance-none rounded-md border bg-transparent py-1 pr-8 pl-3 text-sm transition-colors outline-none disabled:cursor-not-allowed disabled:opacity-50',
          'focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="text-muted-foreground pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2" />
    </div>
  )
}

export { Select }
