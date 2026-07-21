import { useState } from "react"
import { getResearchDraft, type ResearchDraftKind } from "../lib/research-handoff"


type ResearchHandoffBannerProps = {
  onPick: (prompt: string) => void
  onDismiss: () => void
}


const ACTIONS: { kind: ResearchDraftKind; label: string }[] = [
  { kind: "summary", label: "Tóm tắt board" },
  { kind: "focus", label: "Đào sâu ô đang chọn" },
  { kind: "gaps", label: "Tìm gap" },
  { kind: "next", label: "Câu hỏi tiếp" },
]


/**
 * One-shot banner after launcher handoff: guides continue-on-board chat.
 */
export function ResearchHandoffBanner({
  onPick,
  onDismiss,
}: ResearchHandoffBannerProps) {
  const [hidden, setHidden] = useState(false)
  if (hidden) return null

  return (
    <div className="absolute top-3 left-1/2 z-[55] w-[min(640px,calc(100vw-2rem))] -translate-x-1/2 pointer-events-auto">
      <div className="rounded-xl border border-primary/25 bg-card/95 backdrop-blur-md shadow-lg px-3 py-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              Research graph sẵn sàng — chat tiếp trên board
            </p>
            <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
              Click 1 ô rồi bấm gợi ý bên dưới, hoặc hỏi cả board. Graph là bộ nhớ —
              agent đọc từ canvas.
            </p>
          </div>
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground shrink-0 px-1"
            onClick={() => {
              setHidden(true)
              onDismiss()
            }}
          >
            Đóng
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {ACTIONS.map((a) => (
            <button
              key={a.kind}
              type="button"
              className="rounded-md border border-border/70 bg-background/70 px-2 py-1 text-xs font-medium hover:bg-sidebar"
              onClick={() => onPick(getResearchDraft(a.kind))}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
