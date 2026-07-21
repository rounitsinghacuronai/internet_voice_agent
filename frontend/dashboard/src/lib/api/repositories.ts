/**
 * Repository interfaces — the contract the UI depends on.
 *
 * The UI imports ONLY these interfaces (via the provider in index.ts). Neither
 * the pages nor the hooks know whether the concrete implementation is a live
 * HTTP repo or a mock repo. Swapping to a new backend = write a new class that
 * satisfies the interface, wire it in index.ts. Nothing else changes.
 */
import type {
  AppNotification,
  Conversation,
  Customer,
  CustomerProfile,
  DashboardStats,
  Escalation,
  Executive,
  LiveCall,
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

export interface ExecutiveRepository {
  list(): Promise<Executive[]>;
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
  system: SystemRepository;
  executives: ExecutiveRepository;
  escalations: EscalationRepository;
  conversations: ConversationRepository;
  notifications: NotificationRepository;
}
