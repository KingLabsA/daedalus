import { create } from "zustand";
import { Message, KanbanState, AgentWorker } from "../types";

export interface LogEntry {
  type: string;
  timestamp: number;
  iteration?: number;
  name?: string;
  args?: unknown;
  content?: string;
  result?: string;
  id?: string;
}

export interface StreamEntry {
  type: string;
  line?: string;
  filepath?: string;
  old?: string;
  new?: string;
}

export interface CostData {
  total_cost: number;
  total_input_tokens: number;
  total_output_tokens: number;
  session_calls: number;
  by_provider: Record<string, { calls: number; input: number; output: number; cost: number }>;
}

export interface FileNode {
  name: string;
  type: "file" | "dir";
  size?: number;
  children?: FileNode[];
}

interface AgentState {
  connected: boolean;
  connecting: boolean;
  messages: Message[];
  kanban: KanbanState;
  workers: AgentWorker[];
  provider: string;
  model: string;
  tools: string[];
  skills: string[];
  logs: LogEntry[];
  diff: string;
  lsp: string;
  cost: CostData | null;
  stream: StreamEntry[];
  sessions: string[];
  providerTestResult: string;
  streamingContent: string;
  streamingMessageId: string | null;
  files: FileNode | null;
  gitBranches: string;
  gitLog: string;
  theme: "dark" | "light";
  addMessage: (msg: Message) => void;
  setConnected: (v: boolean) => void;
  setConnecting: (v: boolean) => void;
  setKanban: (k: KanbanState) => void;
  setWorkers: (w: AgentWorker[]) => void;
  setProvider: (p: string) => void;
  setModel: (m: string) => void;
  setTools: (t: string[]) => void;
  setSkills: (s: string[]) => void;
  setLogs: (l: LogEntry[]) => void;
  setDiff: (d: string) => void;
  setLsp: (d: string) => void;
  setCost: (c: CostData) => void;
  setStream: (s: StreamEntry[]) => void;
  appendStream: (s: StreamEntry[]) => void;
  setProviderTestResult: (r: string) => void;
  setSessions: (s: string[]) => void;
  setStreamingContent: (c: string) => void;
  setStreamingMessageId: (id: string | null) => void;
  setFiles: (f: FileNode | null) => void;
  setGitBranches: (b: string) => void;
  setGitLog: (l: string) => void;
  setTheme: (t: "dark" | "light") => void;
}

const emptyKanban: KanbanState = { todo: [], in_progress: [], review: [], done: [] };

export const useStore = create<AgentState>((set) => ({
  connected: false,
  connecting: false,
  messages: [],
  kanban: emptyKanban,
  workers: [],
  provider: "openai",
  model: "gpt-4o-mini",
  tools: [],
  skills: [],
  logs: [],
  diff: "",
  lsp: "",
  cost: null,
  stream: [],
  sessions: [],
  providerTestResult: "",
  streamingContent: "",
  streamingMessageId: null,
  files: null,
  gitBranches: "",
  gitLog: "",
  theme: "dark",
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setConnected: (v) => set({ connected: v }),
  setConnecting: (v) => set({ connecting: v }),
  setKanban: (k) => set({ kanban: k }),
  setWorkers: (w) => set({ workers: w }),
  setProvider: (p) => set({ provider: p }),
  setModel: (m) => set({ model: m }),
  setTools: (t) => set({ tools: t }),
  setSkills: (s) => set({ skills: s }),
  setLogs: (l) => set({ logs: l }),
  setDiff: (d) => set({ diff: d }),
  setLsp: (d) => set({ lsp: d }),
  setCost: (c) => set({ cost: c }),
  setStream: (s) => set({ stream: s }),
  appendStream: (s) => set((prev) => ({ stream: [...prev.stream, ...s] })),
  setProviderTestResult: (r) => set({ providerTestResult: r }),
  setSessions: (s) => set({ sessions: s }),
  setStreamingContent: (c) => set({ streamingContent: c }),
  setStreamingMessageId: (id) => set({ streamingMessageId: id }),
  setFiles: (f) => set({ files: f }),
  setGitBranches: (b) => set({ gitBranches: b }),
  setGitLog: (l) => set({ gitLog: l }),
  setTheme: (t) => set({ theme: t }),
}));

export interface StoreState extends AgentState {
  setSessions: (s: string[]) => void;
}
