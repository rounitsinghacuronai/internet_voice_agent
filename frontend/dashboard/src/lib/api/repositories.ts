/**
 * Repository interfaces — the contract the UI depends on.
 *
 * The UI imports ONLY these interfaces (via the provider in index.ts). Neither
 * the pages nor the hooks know whether the concrete implementation is a live
 * HTTP repo or a mock repo. Swapping to a new backend = write a new class that
 * satisfies the interface, wire it in index.ts. Nothing else changes.
 */
import type {
  AdminSettings,
  AppNotification,
  CallRecord,
  Conversation,
  Customer,
  CustomerProfile,
  DashboardStats,
  Escalation,
  Executive,
  ExecutiveRecord,
  KbResult,
  LiveCall,
  SearchResult,
  SystemHealth,
  Ticket,
} from "./types";

export interface TicketQuery {
  q?: string;
  status?: string;
  priority?: string;
  category?: string;
  limit?: number;
}

export interface DashboardRepository {
  getStats(): Promise<DashboardStats>;
}

export interface TicketRepository {
  list(query?: TicketQuery): Promise<Ticket[]>;
  get(id: string): Promise<Ticket | null>;
  setStatus(id: string, status: string): Promise<Ticket>;
  assign(id: string, executive: string): Promise<Ticket>;
  addNotes(id: string, notes: string): Promise<Ticket>;
}

export interface CallRepository {
  list(q?: string): Promise<CallRecord[]>;
  get(id: string): Promise<CallRecord | null>;
}

export interface SettingsRepository {
  get(): Promise<AdminSettings>;
  save(patch: Partial<AdminSettings>): Promise<AdminSettings>;
}

export interface SearchRepository {
  query(q: string): Promise<SearchResult[]>;
}

export interface KbRepository {
  search(q: string): Promise<KbResult[]>;
  reload(): Promise<{ reloaded: boolean; chunks: number }>;
}

export interface CustomerRepository {
  list(q?: string): Promise<Customer[]>;
  getProfile(accountNo: string): Promise<CustomerProfile>;
}

export interface LiveCallRepository {
  list(): Promise<LiveCall[]>;
}

export interface SystemRepository {
  health(): Promise<SystemHealth>;
}

export interface ExecutiveAdminRepository {
  list(): Promise<ExecutiveRecord[]>;
  create(e: Omit<ExecutiveRecord, "id" | "created_at">): Promise<ExecutiveRecord>;
  update(id: number, e: Omit<ExecutiveRecord, "id" | "created_at">): Promise<ExecutiveRecord>;
  remove(id: number): Promise<void>;
}

export interface EscalationRepository {
  list(): Promise<Escalation[]>;
}

export interface ConversationRepository {
  list(q?: string): Promise<Conversation[]>;
}

export interface NotificationRepository {
  list(): Promise<AppNotification[]>;
}

export interface Repositories {
  dashboard: DashboardRepository;
  tickets: TicketRepository;
  customers: CustomerRepository;
  liveCalls: LiveCallRepository;
  calls: CallRepository;
  system: SystemRepository;
  executivesAdmin: ExecutiveAdminRepository;
  escalations: EscalationRepository;
  conversations: ConversationRepository;
  notifications: NotificationRepository;
  settings: SettingsRepository;
  search: SearchRepository;
  kb: KbRepository;
}
