import * as React from 'react'
import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * 원생 select 를 shadcn 양식으로 감싼다.
 * Radix 팝오버를 쓰지 않는 이유 — 선택지가 서버에서 오는 단순 목록뿐이라
 * 접근성·모바일 동작을 브라우저에 맡기는 편이 낫다.
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
