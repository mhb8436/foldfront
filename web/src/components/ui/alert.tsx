import * as React from 'react'
import { AlertTriangle } from 'lucide-react'

import { cn } from '@/lib/utils'

/** An error notice. Uses the failure colour and nothing else. */
function Alert({ className, children, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      role="alert"
      data-slot="alert"
      className={cn(
        'text-destructive flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 text-[13px]',
        className,
      )}
      {...props}
    >
      <AlertTriangle className="mt-px size-4 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  )
}

export { Alert }
