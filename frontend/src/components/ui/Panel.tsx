import { useRef, type ComponentPropsWithoutRef } from 'react'
import { useSurfaceInteraction, type SurfaceInteraction } from '../../hooks/useSurfaceInteraction'
import { cn } from '../../utils/cn'

type PanelTag = 'section' | 'article' | 'aside'

interface PanelProps extends ComponentPropsWithoutRef<'section'> {
  as?: PanelTag
  /** `spotlight`: cursor-tracking border light. `tilt`: spotlight plus subtle 3D response. */
  interaction?: SurfaceInteraction
}

export function Panel({ as: Tag = 'section', interaction = 'none', className, children, ...rest }: PanelProps) {
  const ref = useRef<HTMLElement>(null)
  useSurfaceInteraction(ref, interaction)

  return (
    <Tag
      ref={ref}
      className={cn(
        'panel',
        interaction !== 'none' && 'panel-interactive',
        interaction === 'tilt' && 'panel-tilt',
        className,
      )}
      {...rest}
    >
      {children}
    </Tag>
  )
}
