/**
 * Represents a URL annotation.
 */
export interface UrlAnnotation {
  type: "url"
  url: string
  title?: string
  content?: string
  favicon?: string
  coverImage?: string
  sourceDomain?: string
  publishedAt?: string // ISO 8601 date string
  tags?: string[]
}

export interface FileAnnotation {
  type: "file"
  fileType: string
  filePath: string
  fileId: string
}

export interface RefAnnotation {
  type: "reference"
  refId: string
}

export type Annotation = UrlAnnotation | FileAnnotation | RefAnnotation

export interface WebSearchOutput {
  type: "web_search"
  answer: string
  searchResults: UrlAnnotation[]
}

export interface MemorySearchOutput {
  type: "memory_search"
  answer: string
  references: RefAnnotation[]
}

export interface CodeInterpreterOutput {
  type: "code_interpreter"
  status: "success" | "error" | "timeout"
  stdout: string
  stderr: string
  durationMs: number
}

export interface CreateNoteOutput {
  type: "create_note"
  noteId: string
  graphUid: string
  label: string | null
  noteType: string
  parentId?: string | null
}

export interface WriteNoteOutput {
  type: "write_note"
  action: "created" | "rewritten"
  noteId: string
  graphUid: string
  label: string | null
  noteType: string
  parentId?: string | null
}

export interface EditNoteOutput {
  type: "edit_note"
  noteId: string
  graphUid: string
  label: string | null
  noteType: string
  parentId?: string | null
}


export interface GetNoteOutput {
  type: "get_note"
  noteId: string
  graphUid: string
  label: string | null
  content: string
  noteType: string
  parentId?: string | null
}


export interface LinkNotesOutput {
  type: "link_notes"
  linkId: string
  sourceId: string
  targetId: string
  graphUid: string
  label: string | null
}

export interface WeatherWidgetOutput {
  type: "display_weather_widget"
  city: string
}

export interface StockWidgetOutput {
  type: "display_stock_widget"
  symbol: string
}

export interface ImageSearchWidgetOutput {
  type: "display_image_search_widget"
  query: string
  images: string[]
}

export interface ImageGenerationOutput {
  type: "image_generation"
  imageUrls: string[]
}


/** Output from the change-note-kind structural tool. */
export interface ChangeNoteKindOutput {
  type: "change_note_kind"
  noteId: string
  graphUid: string
  kind: string
}


/** Output from the reparent-note structural tool. */
export interface ReparentNoteOutput {
  type: "reparent_note"
  noteId: string
  graphUid: string
  parentId: string | null
}


/** Output from the delete-subtree structural tool. */
export interface DeleteSubtreeOutput {
  type: "delete_subtree"
  graphUid: string
  deletedNodes: number
  deletedEdges: number
}


/** Output from the merge-notes structural tool. */
export interface MergeNotesOutput {
  type: "merge_notes"
  targetId: string
  graphUid: string
  absorbed: number
}


/** Output from the split-note structural tool. */
export interface SplitNoteOutput {
  type: "split_note"
  graphUid: string
  createdIds: string[]
  originalDeleted: boolean
}


/** Output from the relayout-board structural tool. */
export interface RelayoutOutput {
  type: "relayout_board"
  graphUid: string
  moved: number
  mode: string
}


export type ToolOutput =
  | WebSearchOutput
  | MemorySearchOutput
  | CodeInterpreterOutput
  | WriteNoteOutput
  | CreateNoteOutput
  | EditNoteOutput
  | GetNoteOutput
  | LinkNotesOutput
  | WeatherWidgetOutput
  | StockWidgetOutput
  | ImageSearchWidgetOutput
  | ImageGenerationOutput
  | ChangeNoteKindOutput
  | ReparentNoteOutput
  | DeleteSubtreeOutput
  | MergeNotesOutput
  | SplitNoteOutput
  | RelayoutOutput
  | string
