import * as React from 'react'
import { AlertTriangle } from 'lucide-react'

import { cn } from '@/lib/utils'

/** 오류 고지. 상태색 가운데 실패색만 쓰고 나머지는 무채색으로 둔다. */
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
