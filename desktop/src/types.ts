export interface Message {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  timestamp: Date;
  toolCalls?: { name: string; args: Record<string, string> }[];
  routedTo?: string;
  changeset?: ChangesetSummary;
}

export interface ChangesetSummary {
  id: string;
  files: { path: string; tool?: string; status: string; diff: string }[];
}

export interface KanbanTask {
  id: string;
  title: string;
  description: string;
  status: "todo" | "in_progress" | "review" | "done";
  assigned_to?: string;
  retries: number;
}

export interface KanbanState {
  todo: KanbanTask[];
  in_progress: KanbanTask[];
  review: KanbanTask[];
  done: KanbanTask[];
}

export interface AgentWorker {
  id: string;
  name: string;
  type: string;
  status: "idle" | "working" | "blocked" | "zombie";
  currentTask?: string;
  lastHeartbeat: Date;
}
