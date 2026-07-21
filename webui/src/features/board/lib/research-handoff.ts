/**
 * Handoff helpers: launcher → Dim0 board chat.
 *
 * Best-practice: after graph generation, land the user ON the board with
 * chat open and a ready-to-send draft — don't force them to invent prompts.
 */

export const RESEARCH_DRAFT_KEY = "dim0_research_chat_draft"
export const RESEARCH_HANDOFF_FLAG = "dim0_research_handoff"


export type ResearchDraftKind = "summary" | "focus" | "gaps" | "next" | "custom"


const DRAFTS: Record<Exclude<ResearchDraftKind, "custom">, string> = {
  summary:
    "Đọc research graph trên board. Tóm tắt: câu hỏi chính, các workstream/mode, " +
    "5 insight quan trọng, chỗ evidence còn yếu. Ngắn, dễ hiểu.",
  focus:
    "Chỉ làm việc với (các) node đang chọn trong message context. " +
    "Làm rõ / đào sâu nhánh này: thêm Source/Evidence/Finding nếu thiếu; " +
    "brand/campaign/message + URL nếu có. Không rewrite nhánh khác.",
  gaps:
    "Rà soát board: claim thiếu Source, nhánh trống, mâu thuẫn. " +
    "Thêm Unknown/Contradiction/Finding về gap. Không đổi taxonomy tổng thể.",
  next:
    "Từ graph hiện tại, đề xuất 3–5 câu hỏi research tiếp theo (hẹp, actionable) " +
    "và ghi gợi ý lên board nếu phù hợp.",
}


/**
 * Return a canned draft prompt for a research follow-up action.
 */
export function getResearchDraft(kind: ResearchDraftKind, custom?: string): string {
  if (kind === "custom") return (custom || "").trim()
  return DRAFTS[kind]
}


/**
 * Persist draft so the board chat can prefill after navigation.
 */
export function stashResearchHandoff(draft: string): void {
  try {
    sessionStorage.setItem(RESEARCH_DRAFT_KEY, draft)
    sessionStorage.setItem(RESEARCH_HANDOFF_FLAG, "1")
  } catch {
    // private mode / quota — ignore
  }
}


/**
 * Read and clear handoff draft (one-shot).
 */
export function consumeResearchDraft(): string | null {
  try {
    const draft = sessionStorage.getItem(RESEARCH_DRAFT_KEY)
    sessionStorage.removeItem(RESEARCH_DRAFT_KEY)
    return draft && draft.trim() ? draft.trim() : null
  } catch {
    return null
  }
}


/**
 * Whether this page load came from a research handoff (banner).
 */
export function consumeResearchHandoffFlag(): boolean {
  try {
    const v = sessionStorage.getItem(RESEARCH_HANDOFF_FLAG)
    sessionStorage.removeItem(RESEARCH_HANDOFF_FLAG)
    return v === "1"
  } catch {
    return false
  }
}


/**
 * Build board URL with research handoff search params.
 */
export function boardResearchUrl(frontendOrigin: string, boardId: string): string {
  const base = frontendOrigin.replace(/\/$/, "")
  return `${base}/boards/${boardId}?research=1`
}
