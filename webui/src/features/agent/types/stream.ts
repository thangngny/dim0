import type { AppIconComponent } from "@/components/icons"
import {
  BrowserSearchIcon,
  CreateNoteIcon,
  EditNoteIcon,
  ImageGenerationIcon,
  ImageSearchWidgetIcon,
  LinkIcon,
  MemorySearchIcon,
  NoteIcon,
  OutlineGeneratorIcon,
  ReadNoteIcon,
  ScrollIcon,
  StockWidgetIcon,
  ToolCodeIcon,
  WeatherWidgetIcon,
  WebCollectorIcon,
  WriteNoteToolIcon,
} from "@/components/icons"
import type { Annotation, ToolOutput } from "./tool-outputs"


/**
 * Represents the type of streaming message in the agent response.
 */
export type StreamingMessageType = "stream_message" | "stream_reasoning_message"


/**
 * Represents the type of streaming message content in the agent response.
 */
export type StreamingContentType = "token" | "status" | "message"


/**
 * Represents the execution state of a tool in the agent streaming response.
 */
export type ToolExecutionState = "started" | "completed" | "failed"


/**
 * Represents a message in the agent streaming response.
 */
export interface AgentStreamMessage {
  type: StreamingMessageType
  toolId: string
  toolName: ToolName
  content?: {
    type: StreamingContentType
    text: string
    annotations: Annotation[]
  }
  isStop: boolean | "error"
}


/**
 * Represents a persisted reasoning text step.
 */
export interface ReasoningTextStep {
  type: "reasoning_step"
  id: string
  reasoning: string
  message: string
  isSynthesis?: boolean
}


/**
 * Represents a structured tool call step.
 */
export interface ToolCallStep {
  type: "tool_call"
  id: string
  name: ToolName
  thought: string
  output: ToolOutput
  state: ToolExecutionState
  eventMessages: string[]
  arguments?: { input: unknown }
}


/**
 * Represents one ordered item in the assistant process.
 */
export type ReasoningStep = ReasoningTextStep | ToolCallStep


/**
 * AgentResponse represents the response from the agent, containing reasoning steps.
 */
export interface AgentResponse {
  steps: ReasoningStep[]
  sentAt?: string
  isDeepResearch?: boolean
}


/**
 * Agent tool names enum.
 */
export type ToolName =
  | "answer_reformulate"
  | "web_search"
  | "memory_search"
  | "code_interpreter"
  | "write_note"
  | "create_note"
  | "edit_note"
  | "get_note"
  | "describe_image"
  | "link_notes"
  | "change_note_kind"
  | "reparent_note"
  | "delete_subtree"
  | "merge_notes"
  | "split_note"
  | "relayout_board"
  | "outline_generator"
  | "web_collector"
  | "synthesizer"
  | "navigate"
  | "raw_message"
  | "image_description"
  | "topic_illustrator"
  | "image_generation"
  | "display_weather_widget"
  | "display_stock_widget"
  | "display_image_search_widget"
  | "learn_generate_html_widget"
  | "learn_generate_mini_app"
  | "learn_generate_diagram"


export const ToolNameDescription: Record<ToolName, string> = {
  answer_reformulate: "Reformulate answer",
  web_search: "Search the web",
  memory_search: "Search memory",
  code_interpreter: "Interpret code",
  write_note: "Write note",
  create_note: "Create note",
  edit_note: "Edit note",
  get_note: "Read note",
  link_notes: "Link notes",
  change_note_kind: "Change note kind",
  reparent_note: "Reparent note",
  delete_subtree: "Delete subtree",
  merge_notes: "Merge notes",
  split_note: "Split note",
  relayout_board: "Relayout board",
  outline_generator: "Generate outline",
  web_collector: "Collect web content",
  synthesizer: "Synthesize response",
  navigate: "Fetch and analyze web page content",
  raw_message: "Reasoning",
  image_description: "Describe image",
  topic_illustrator: "Illustrate topic",
  image_generation: "Generate images based on prompts",
  display_weather_widget: "Display weather information",
  display_stock_widget: "Display stock information",
  display_image_search_widget: "Search for images from the web",
  learn_generate_html_widget: "Learn widget and visual explainer skill",
  learn_generate_mini_app: "Learn interactive React mini-app skill",
  learn_generate_diagram: "Learn mindmap and diagram skill",
}


export const ToolNameIcon: Record<string, AppIconComponent> = {
  answer_reformulate: NoteIcon,
  web_search: BrowserSearchIcon,
  memory_search: MemorySearchIcon,
  outline_generator: OutlineGeneratorIcon,
  web_collector: WebCollectorIcon,
  synthesizer: NoteIcon,
  navigate: BrowserSearchIcon,
  code_interpreter: ToolCodeIcon,
  write_note: WriteNoteToolIcon,
  create_note: CreateNoteIcon,
  edit_note: EditNoteIcon,
  get_note: ReadNoteIcon,
  link_notes: LinkIcon,
  change_note_kind: EditNoteIcon,
  reparent_note: NoteIcon,
  delete_subtree: NoteIcon,
  merge_notes: NoteIcon,
  split_note: NoteIcon,
  relayout_board: NoteIcon,
  image_description: ImageGenerationIcon,
  topic_illustrator: ImageGenerationIcon,
  image_generation: ImageGenerationIcon,
  display_weather_widget: WeatherWidgetIcon,
  display_stock_widget: StockWidgetIcon,
  display_image_search_widget: ImageSearchWidgetIcon,
  learn_generate_html_widget: ScrollIcon,
  learn_generate_mini_app: ScrollIcon,
  learn_generate_diagram: ScrollIcon,
}


export const RAW_MESSAGE: ToolName = "raw_message"


/**
 * Checks whether a tool name should be rendered as reasoning/message text.
 */
export const isReasoningTextToolName = (toolName: ToolName) =>
  toolName === "raw_message" ||
  toolName === "answer_reformulate" ||
  toolName === "synthesizer"


/**
 * Normalizes text-like tool steps into reasoning text steps for rendering.
 */
export const normalizeReasoningStep = (step: ReasoningStep): ReasoningStep => {
  if (step.type !== "tool_call" || !isReasoningTextToolName(step.name)) {
    return step
  }

  return {
    type: "reasoning_step",
    id: step.id,
    reasoning: step.thought || "",
    message: typeof step.output === "string" ? step.output : "",
    isSynthesis: step.name === "synthesizer",
  }
}


/**
 * Normalizes a mixed step list so text-like tool steps render as reasoning text.
 */
export const normalizeReasoningSteps = (steps: ReasoningStep[]) =>
  steps.map(normalizeReasoningStep)


/**
 * Checks whether a reasoning step is a text step.
 */
export const isReasoningTextStep = (step: ReasoningStep): step is ReasoningTextStep =>
  step.type === "reasoning_step"


/**
 * Checks whether a reasoning step is a tool call step.
 */
export const isToolCallStep = (step: ReasoningStep): step is ToolCallStep =>
  step.type === "tool_call"
