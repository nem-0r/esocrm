/**
 * Типы, приходящие с сервера. Один источник правды для всего фронтенда.
 * Имена полей совпадают с ответами API один в один — без переименований по дороге.
 */

export type UserRole = 'admin' | 'manager'
export type FunnelStage = 'warmup' | 'diagnostic' | 'sales'
export type AccountStatus = 'pending' | 'connected' | 'error' | 'disconnected'
export type Direction = 'in' | 'out'
export type AuthorKind = 'client' | 'manager' | 'userbot' | 'system'
export type MessageKind = 'text' | 'photo' | 'video' | 'document' | 'voice' | 'service'
/** Статуса «доставлено» нет намеренно: MTProto его не отдаёт. */
export type MessageStatus = 'queued' | 'sent' | 'read' | 'failed'
export type BirthTimeApprox = 'morning' | 'day' | 'evening' | 'night'
export type PaymentMethod = 'link' | 'requisites'
export type DealStatus = 'draft' | 'awaiting' | 'paid' | 'cancelled' | 'expired'
export type DealEventKind =
  | 'created'
  | 'sent'
  | 'edited'
  | 'cancelled'
  | 'expired'
  | 'paid'
  | 'reminded'
export type NotificationKind =
  | 'deal_paid'
  | 'account_assigned'
  | 'account_unassigned'
  | 'account_error'
  | 'payment_mismatch'
  | 'payment_orphaned'

export interface UserRef {
  id: number
  full_name: string
  avatar_color?: string
}

export interface Me {
  id: number
  full_name: string
  email: string
  phone: string | null
  role: UserRole
  is_active: boolean
  accepting_leads: boolean
  avatar_color: string
  last_seen_at: string | null
  is_admin: boolean
  sees_all_accounts: boolean
  account_ids: number[]
  /** С какого момента человек работает: принял приглашение или заведён (ТЗ Б.1). */
  works_since: string
  /** Личные показатели за календарный месяц — те же, что в списке сотрудников. */
  month_conversations: number
  month_sales_amount: number
  avg_response_seconds: number | null
  /** Сколько «моих» аккаунтов требуют внимания (ТЗ Б.5). */
  accounts_attention: number
  /** Свой график работы: сотрудник видит, меняет руководитель (ТЗ Б.3). */
  schedule: WorkSchedule
  /** Демо-режим: настоящий Telegram не подключён, действия с ним недоступны. */
  demo_mode: boolean
  /** Способ оплаты «Ссылка» показывается в форме только когда это true. */
  robokassa_enabled: boolean
}

export interface AccountRef {
  id: number
  title: string
  funnel_stage: FunnelStage
}

export interface Account extends AccountRef {
  phone: string
  status: AccountStatus
  status_reason: string | null
  tg_username: string | null
  last_activity_at: string | null
  is_active: boolean
  needs_attention: boolean
  managers: (UserRef & { online: boolean })[]
  conversations_count: number
}

export interface AccountSummary {
  total: number
  connected: number
  attention: number
  conversations_total: number
}

/** График работы сотрудника (ТЗ Б.3). `on_shift` считает сервер в поясе организации. */
export interface WorkSchedule {
  enabled: boolean
  /** Дни недели по ISO: 1 — понедельник, 7 — воскресенье. */
  days: number[]
  start: string | null
  end: string | null
  summary: string
  on_shift: boolean
}

export interface StaffMember extends UserRef {
  email: string
  phone: string | null
  role: UserRole
  is_active: boolean
  online: boolean
  last_seen_at: string | null
  invite_pending: boolean
  accounts: AccountRef[]
  month_conversations: number
  month_sales_amount: number
  schedule: WorkSchedule
}

export interface ClientRef {
  id: number
  name: string
  phone?: string | null
  tg_username?: string | null
  data_complete?: boolean
  /** Ниже — только в ответе на GET /conversations/{id}, в списке этих полей нет. */
  first_contact_at?: string
  birth_date?: string | null
  birth_time?: string | null
  birth_city?: string | null
  zodiac_sign?: string | null
}

export interface ClientRow extends ClientRef {
  birth_date: string | null
  birth_time: string | null
  /** Часть суток, когда точного времени клиент не помнит. */
  birth_time_approx: BirthTimeApprox | null
  birth_city: string | null
  zodiac_sign: string | null
  first_contact_at: string
  created_at: string
  last_contact_at: string | null
  paid_amount: number
  paid_count: number
  awaiting_amount: number
  conversations_count: number
}

export interface ClientCard extends ClientRow {
  source_code: string | null
  source: string | null
  tg_first_name: string | null
  tg_last_name: string | null
  /** Имя, введённое менеджером. Пусто — сверху показан @username или id. */
  display_name: string | null
  created_via_account: AccountRef | null
  accounts_count: number
  pdn_consent_at: string | null
  pdn_consent_version: string | null
  marketing_consent: boolean
  marketing_consent_at: string | null
  conversations: {
    id: number
    account: AccountRef
    responsible: UserRef | null
    last_message_at: string | null
    unread_count: number
  }[]
}

export interface Conversation {
  id: number
  client: ClientRef
  account: AccountRef
  last_message_at: string | null
  last_message_preview: string | null
  unread_count: number
  awaiting_reply_since: string | null
  awaiting_minutes: number | null
  responsible: UserRef | null
  has_awaiting_deal: boolean
  awaiting_deal_amount: number | null
  is_blocked_by_client: boolean
  /** Есть только в ответе на GET /conversations/{id} */
  client_paid_amount?: number
  client_paid_count?: number
}

export interface Counters {
  total: number
  awaiting: number
  awaiting_payment: number
  over_threshold: number
  /** Порог из настроек — подпись баннера берёт число отсюда, а не из константы. */
  over_threshold_minutes: number
}

export interface AttachmentRef {
  id: number
  file_name: string
  mime_type: string | null
  size_bytes: number
  url: string
  width?: number | null
  height?: number | null
  duration_sec?: number | null
}

export interface Message {
  id: number
  direction: Direction
  author_kind: AuthorKind
  author: UserRef | null
  kind: MessageKind
  text: string | null
  is_internal: boolean
  status: MessageStatus
  error_text: string | null
  created_at: string
  sent_at: string | null
  read_at: string | null
  edited_at: string | null
  reply_to_tg_id: number | null
  attachments: AttachmentRef[]
}

export interface DealItem {
  id: number
  name: string
  amount: number
  position: number
}

export interface DealEvent {
  id: number
  kind: DealEventKind
  comment: string | null
  actor: UserRef | null
  created_at: string
}

export interface DealRow {
  id: number
  number: string
  title: string
  client: ClientRef
  /** Канал, через который прошла оплата (ТЗ п. 4.6). */
  account: AccountRef
  total_amount: number
  status: DealStatus
  payment_method: PaymentMethod
  created_at: string
  /** Дата, по которой сделка попадает в период: оплата или создание. */
  event_at: string
  sent_at: string | null
  expires_at: string | null
  paid_at: string | null
  /** Кто подтвердил: менеджер вручную или уведомление Робокассы. */
  paid_source: 'manual' | 'provider' | null
  sold_by: UserRef
  items_count: number
  days_without_answer: number | null
}

export interface DealCard extends DealRow {
  items: DealItem[]
  requisites_snapshot: string | null
  requisite_id: number | null
  /** Ссылка на оплату — только у способа «Ссылка», после отправки в чат. */
  payment_url: string | null
  /** Куда деньги пришли фактически — с этим сверяют банковскую выписку. */
  paid_to_requisite_id: number | null
  paid_to_requisite_title: string | null
  intro_text: string | null
  /** Чек оплаты — файл, приложенный менеджером при подтверждении (PNG/PDF/…). */
  receipt_file_name: string | null
  receipt_mime_type: string | null
  receipt_size_bytes: number | null
  /** Ссылка на скачивание — есть только когда чек приложен. */
  receipt_url: string | null
  cancel_reason: string | null
  edit_count: number
  conversation_id: number
  events: DealEvent[]
}

export interface DealsSummary {
  paid_amount: number
  awaiting_amount: number
  paid_count: number
  awaiting_count: number
  clients_with_deals: number
}

export interface Requisite {
  id: number
  title: string
  bank_name: string | null
  account_masked: string | null
  /** Страна и тип: клиент из Казахстана не заплатит на российскую карту. */
  country: string | null
  method: string | null
  kind: string | null
  holder: string | null
  details_text: string
  is_active: boolean
  sort_order: number
}

export interface Template {
  id: number
  title: string
  text: string
  owner_id: number | null
  sort_order: number
}

export interface StatsOverview {
  sales_amount: number
  sales_count: number
  sales_amount_delta_pct: number | null
  sales_count_delta: number | null
  avg_response_seconds: number | null
  response_goal_minutes: number
  active_conversations: number
  new_clients: number
  awaiting_amount: number
  awaiting_count: number
}

export type Granularity = 'day' | 'week' | 'month'

export interface SeriesPoint {
  /** Начало корзины: понедельник недели, первое число месяца, сам день. Ключ точки. */
  date: string
  /** Границы, обрезанные выбранным периодом, — только они годятся для подписи. */
  period_start: string
  period_end: string
  amount: number
  count: number
}

export interface StatsSeries {
  points: SeriesPoint[]
  granularity: Granularity
}

export interface ManagerStats {
  user: UserRef
  sales_amount: number
  sales_count: number
  avg_response_seconds: number | null
  active_conversations: number
  awaiting_count: number
}

export interface SearchResults {
  clients: { id: number; name: string; phone: string | null; paid_amount: number }[]
  deals: {
    id: number
    number: string
    title: string
    total_amount: number
    status: DealStatus
    client_name: string
  }[]
  chats: {
    conversation_id: number
    client_name: string
    account_title: string
    snippet: string
    message_id: number
    created_at: string
  }[]
  files: {
    id: number
    file_name: string
    mime_type: string | null
    client_id: number
    client_name: string
    created_at: string
    url: string
  }[]
  /** Только руководителю: у менеджера раздела сотрудников нет (ТЗ Б.13). */
  managers: {
    id: number
    full_name: string
    email: string
    role: UserRole
    is_active: boolean
    online: boolean
    avatar_color: string
  }[]
}

export interface AppNotification {
  id: number
  kind: NotificationKind
  entity_type: string | null
  entity_id: number | null
  title: string
  text: string
  read_at: string | null
  created_at: string
}

export interface Settings {
  awaiting_banner_minutes: number
  response_time_goal_minutes: number
  deal_link_ttl_days: number
  working_hours_enabled: boolean
  working_hours_start: string
  working_hours_end: string
  working_days: number[]
  timezone: string
  /** Формат ГГГГ-ММ-ДД. Реально управляет подтяжкой истории — приоритетнее
   *  history_sync_days на бэкенде, поэтому в форме только это поле. */
  history_sync_from: string
  /** Для чека 54-ФЗ в ссылке Робокассы — ответ бухгалтера, не константа. */
  robokassa_sno: string
  robokassa_tax: string
}

export interface CursorPage<T> {
  items: T[]
  next_cursor: string | null
  total?: number | null
}

/** События WebSocket. Клиент при обрыве дозагружает пропущенное запросом. */
export type WsEvent =
  | { type: 'message.new'; data: { conversation_id: number; message: Message } }
  | { type: 'message.updated'; data: { conversation_id: number; message: Message } }
  | { type: 'conversation.updated'; data: { conversation: Conversation } }
  | { type: 'conversation.viewers'; data: { conversation_id: number; user_ids: number[] } }
  // Не готовые числа — сигнал перезапросить: счётчики зависят от прав
  // получателя, у каждого подключённого свои.
  | { type: 'counters.updated'; data: { conversation_id: number; stale: true } }
  | { type: 'deal.updated'; data: { deal: DealRow } }
  | { type: 'notification.new'; data: { notification: AppNotification } }
  | { type: 'presence.updated'; data: { user_id: number; online: boolean } }
  | { type: 'account.status'; data: { account_id: number; status: AccountStatus; reason?: string } }
  | { type: 'pong' }
