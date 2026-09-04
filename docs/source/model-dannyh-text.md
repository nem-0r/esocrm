Модель данных CRM «Астра»
Приложение к ТЗ · Версия 1.0 · Август 2026
Типы указаны в нотации PostgreSQL. При использовании другой СУБД типы адаптируются, ограничения сохраняются.
1. Принципы
Клиент существует в одном экземпляре. Идентификатор — telegram_id. Переход между чатами или сообщение в другом канале не создаёт новую карточку.
Менеджер привязан к диалогу, а не к клиенту. Один клиент может одновременно обслуживаться разными менеджерами в разных чатах воронки.
Цены и названия фиксируются в момент продажи. Изменение справочника услуг не меняет исторические сделки.
Все внешние события идемпотентны. Повторная доставка не создаёт дублей.
Удаление записей — логическое. Физически удаляются только по запросу на удаление персональных данных.
2. Справочники и сотрудники
2.1 users — сотрудники

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| full_name | varchar(255) | not null | ФИО |
| email | varchar(255) | not null, unique | Логин |
| password_hash | varchar(255) | not null | bcrypt / argon2 |
| phone | varchar(20) |  |  |
| role | enum | not null, default manager | manager, admin |
| is_active | boolean | not null, default true | Блокировка доступа |
| created_at | timestamptz | not null |  |
| last_login_at is-online | Timestamptz boolean | not null | Онлайн-статус |
[/TABLE]

Индексы: email (unique), is_active.
2.2 bots — подключённые Telegram-боты

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| name | varchar(100) | not null | Название для интерфейса, напр. «Прогрев» |
| username | varchar(100) | not null, unique | @astra_start_bot |
| token_ref | varchar(255) | not null | Ссылка на секрет в хранилище, не сам токен |
| mode | enum | not null | bot_only — только автоматика; mixed — бот и менеджер |
| funnel_stage | smallint | not null | Порядковый номер этапа воронки: 1, 2, 3 |
| is_active | boolean | not null, default true |  |
| webhook_secret | varchar(255) | not null | Секрет для проверки входящих webhook |
| created_at | timestamptz | not null |  |
[/TABLE]

Индексы: username (unique), funnel_stage.
2.3 services — справочник услуг (пока нет в MVP)

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| name | varchar(255) | not null |  |
| description | text |  | Подпись под названием при выборе |
| price | numeric(10,2) | not null | Цена по прайсу |
| is_active | boolean | not null, default true |  |
| sort_order | smallint | not null, default 0 |  |
| created_at | timestamptz | not null |  |
[/TABLE]

2.4 material_types — справочник типов материалов (пока нет в MVP)

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| name | varchar(100) | not null | «Натальная карта», «Прогноз на месяц» |
| icon | varchar(50) |  | Имя иконки для интерфейса |
| is_active | boolean | not null, default true |  |
| sort_order | smallint | not null, default 0 |  |
[/TABLE]

Справочник редактируемый: добавление типа не требует релиза.
3. Клиенты и коммуникация
3.1 clients — клиенты

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| telegram_id | bigint | not null, unique | Глобальный ID пользователя Telegram |
| telegram_username | varchar(100) |  | Может меняться, не идентификатор |
| telegram_first_name | varchar(255) |  | Имя из профиля |
| telegram_last_name | varchar(255) |  |  |
| display_name | varchar(255) |  | Как обращаться, задаёт менеджер или клиент |
| phone | varchar(20) |  | Если клиент поделился |
| source | varchar(255) |  | Параметр из /start при первом обращении |
| birth_date | date |  |  |
| birth_time | time |  | Опционально |
| birth_time_approx | enum |  | morning, day, evening, night — если точное время неизвестно |
| birth_city | varchar(255) |  | Опционально |
| birth_lat | numeric(9,6) |  | Координаты города для расчёта карты |
| birth_lon | numeric(9,6) |  |  |
| birth_tz | varchar(64) |  | Часовой пояс места рождения |
| zodiac_sign | varchar(20) |  | Вычисляется из birth_date |
| possible_duplicate_of | uuid | FK → clients | Признак возможного дубля по телефону или дате рождения |
| pdn_consent_at | timestamptz |  | Дата согласия на обработку ПДн |
| pdn_consent_version | varchar(20) |  | Версия текста согласия |
| marketing_consent | boolean | not null, default true | Согласие на рассылки |
| marketing_consent_at | timestamptz |  |  |
| first_contact_at | timestamptz | not null | Первое обращение в любой бот |
| created_at | timestamptz | not null |  |
| deleted_at | timestamptz |  | Логическое удаление |
[/TABLE]

Индексы: telegram_id (unique), phone, birth_date, display_name (для поиска), zodiac_sign.
Критично: уникальный индекс по telegram_id создаётся на уровне базы данных, а не только проверяется в коде.
3.2 conversations — диалоги

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| client_id | uuid | FK → clients, not null |  |
| bot_id | uuid | FK → bots, not null |  |
| manager_id | uuid | FK → users | Пусто для bot_only и для нераспределённых |
| is_unassigned | boolean | not null, default false | Требует назначения менеджера |
| started_at | timestamptz | not null | Первое сообщение в этом боте |
| last_message_at | timestamptz |  |  |
| last_client_message_at | timestamptz |  | Для расчёта времени ожидания |
| last_manager_message_at | timestamptz |  |  |
| unread_count | integer | not null, default 0 | Непрочитанные менеджером |
| is_blocked_by_client | boolean | not null, default false | Клиент заблокировал бота |
| closed_at | timestamptz |  | Диалог завершён, клиент ушёл дальше по воронке |
[/TABLE]

Ограничения: unique (client_id, bot_id).
Индексы: (manager_id, last_client_message_at), (bot_id, is_unassigned), client_id.
Время ожидания ответа вычисляется как разница между now() и last_client_message_at, если last_client_message_at > last_manager_message_at. В базе не хранится.
3.3 messages — сообщения

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| conversation_id | uuid | FK → conversations, not null |  |
| telegram_message_id | bigint |  | ID сообщения в Telegram, нужен для редактирования |
| direction | enum | not null | in, out |
| author_type | enum | not null | client, manager, bot, system |
| author_id | uuid | FK → users | Заполняется при author_type = manager |
| campaign_id | uuid | FK → campaigns | Если сообщение — часть рассылки |
| text | text |  |  |
| attachments | jsonb |  | Массив ссылок на файлы |
| delivery_status | enum | not null | queued, sent, delivered, failed |
| error_text | text |  | Причина ошибки отправки |
| sent_at | timestamptz |  |  |
| created_at | timestamptz | not null |  |
[/TABLE]

Индексы: (conversation_id, created_at), campaign_id, telegram_message_id.
3.4 telegram_updates — журнал входящих событий

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| bot_id | uuid | FK → bots, not null |  |
| update_id | bigint | not null | Номер события от Telegram |
| payload | jsonb | not null | Тело события целиком |
| processed_at | timestamptz |  |  |
| error_text | text |  |  |
| created_at | timestamptz | not null |  |
[/TABLE]

Ограничения: unique (bot_id, update_id).
Пара, а не одно поле: нумерация событий у каждого бота своя, и событие второго бота с тем же номером не должно отбрасываться как дубль.
Хранение — 30 дней, далее очистка по расписанию.
4. Рассылки (продумать на стороне разработки)
Раздел описывает журналирование рассылок, отправляемых внешним сервисом. Если рассылки будут отправляться из CRM, к этим таблицам добавляются поля сегмента и планировщика.
4.1 campaigns — рассылки

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| external_id | varchar(255) | unique | Идентификатор в сервисе рассылок |
| bot_id | uuid | FK → bots, not null |  |
| name | varchar(255) | not null | Название для интерфейса |
| text_preview | text |  | Текст сообщения |
| sent_at | timestamptz |  | Время запуска |
| recipients_total | integer |  | Со слов сервиса рассылок |
| created_at | timestamptz | not null |  |
[/TABLE]

4.2 campaign_deliveries — факты доставки

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| campaign_id | uuid | FK → campaigns, not null |  |
| client_id | uuid | FK → clients, not null |  |
| message_id | uuid | FK → messages | Созданное сообщение в ленте |
| status | enum | not null | sent, failed, blocked |
| error_text | text |  |  |
| sent_at | timestamptz | not null |  |
[/TABLE]

Ограничения: unique (campaign_id, client_id) — защита от повторной записи при ретрае вызова API.
Индексы: client_id, (campaign_id, status).
5. Материалы
5.1 materials — файлы, отправленные клиенту

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| client_id | uuid | FK → clients, not null |  |
| conversation_id | uuid | FK → conversations, not null | В каком чате отправлен |
| message_id | uuid | FK → messages |  |
| author_id | uuid | FK → users, not null | Кто отправил |
| material_type_id | uuid | FK → material_types, not null |  |
| file_name | varchar(255) | not null |  |
| file_size | integer | not null | Байты |
| mime_type | varchar(100) |  |  |
| storage_key | varchar(500) | not null | Ключ в объектном хранилище |
| telegram_file_id | varchar(255) |  | Для повторной отправки без загрузки |
| sent_at | timestamptz | not null |  |
| deleted_at | timestamptz |  |  |
[/TABLE]

Индексы: (client_id, sent_at), material_type_id.
Файлы, полученные из Telegram, скачиваются и сохраняются в собственном хранилище: telegram_file_id не гарантирует бессрочного доступа.
6. Продажи
6.1 deals — сделки

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| number | varchar(20) | not null, unique | Человекочитаемый, напр. DEAL-1042 |
| client_id | uuid | FK → clients, not null |  |
| conversation_id | uuid | FK → conversations, not null | В каком чате продано |
| manager_id | uuid | FK → users, not null | Кто создал |
| status | enum | not null | draft, awaiting_payment, paid, cancelled, expired |
| total_amount | numeric(10,2) | not null | Сумма к оплате |
| payment_url | varchar(500) |  | Ссылка эквайринга |
| payment_provider_id | varchar(255) |  | Идентификатор платежа у провайдера |
| expires_at | timestamptz |  | Срок действия ссылки |
| telegram_message_id | bigint |  | Для редактирования карточки в чате |
| paid_at | timestamptz |  |  |
| cancelled_at | timestamptz |  |  |
| cancel_reason | enum |  | client_declined, wrong_composition, no_response, other |
| cancel_comment | text |  | Обязателен при other |
| created_at | timestamptz | not null |  |
[/TABLE]

Индексы: number (unique), (manager_id, created_at), (client_id, created_at), (status, expires_at).
Переходы статусов:
created → awaiting_payment → paid awaiting_payment → cancelled awaiting_payment → expired (по истечении expires_at, задачей по расписанию)
Из paid переходов нет. Изменение состава оплаченной сделки запрещено на уровне бизнес-логики.
6.2 deal_items — позиции сделки

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| deal_id | uuid | FK → deals, not null |  |
| service_id | uuid | FK → services | Может быть пусто для произвольной позиции |
| name_snapshot | varchar(255) | not null | Название на момент продажи |
| list_price | numeric(10,2) | not null | Цена по прайсу на момент продажи |
| actual_price | numeric(10,2) | not null | Фактическая цена |
| discount_reason | enum |  | regular_client, compensation, promo, manager_decision, other |
| discount_comment | text |  |  |
| created_at | timestamptz | not null |  |
[/TABLE]

Ограничения: unique (deal_id, service_id) — одна услуга не добавляется в сделку дважды.
list_price и name_snapshot копируются из справочника при создании и далее не меняются.
6.3 payment_events — события от эквайринга

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| deal_id | uuid | FK → deals |  |
| provider_event_id | varchar(255) | not null, unique | Идентификатор события у провайдера |
| event_type | varchar(50) | not null | payment_page_opened, payment_succeeded, payment_failed |
| payload | jsonb | not null |  |
| processed_at | timestamptz |  |  |
| created_at | timestamptz | not null |  |
[/TABLE]

Уникальность provider_event_id обеспечивает идемпотентность: повторный webhook не засчитывает оплату дважды.
7. Прочее
7.1 notes — заметки о клиенте

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| client_id | uuid | FK → clients, not null |  |
| author_id | uuid | FK → users, not null |  |
| text | text | not null |  |
| created_at | timestamptz | not null |  |
| deleted_at | timestamptz |  |  |
[/TABLE]

7.2 event_log — журнал событий

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| event_type | varchar(100) | not null | См. перечень ниже |
| entity_type | varchar(50) | not null | client, deal, conversation, user |
| entity_id | uuid | not null |  |
| actor_type | enum | not null | user, system, bot |
| actor_id | uuid | FK → users |  |
| data | jsonb |  | Значения до и после изменения |
| created_at | timestamptz | not null |  |
[/TABLE]

Индексы: (entity_type, entity_id, created_at), (actor_id, created_at).
Перечень журналируемых событий:
client.created, client.updated, client.duplicate_flagged conversation.created, conversation.manager_assigned, conversation.transferred deal.created, deal.composition_changed, deal.price_overridden, deal.paid, deal.cancelled, deal.expired material.sent campaign.delivery_recorded user.logged_in, user.login_failed
7.3 sessions — сессии сотрудников

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| user_id | uuid | FK → users, not null |  |
| token_hash | varchar(255) | not null, unique |  |
| user_agent | varchar(500) |  |  |
| ip_address | inet |  |  |
| expires_at | timestamptz | not null |  |
| created_at | timestamptz | not null |  |
| revoked_at | timestamptz |  |  |
[/TABLE]

7.4 push_subscriptions — подписки на уведомления

[TABLE]
| Поле | Тип | Ограничения | Описание |
| id | uuid | PK |  |
| user_id | uuid | FK → users, not null |  |
| endpoint | varchar(500) | not null, unique |  |
| keys | jsonb | not null | Ключи Web Push |
| notify_messages | boolean | not null, default true |  |
| notify_payments | boolean | not null, default true |  |
| created_at | timestamptz | not null |  |
[/TABLE]

8. Вычисляемые данные
Не хранятся в базе, рассчитываются запросом:

[TABLE]
| Показатель | Как считается |
| Время ожидания ответа | now() - last_client_message_at, если клиент писал последним |
| Клиенты менеджера | Клиенты, у которых есть диалог с manager_id = текущий |
| Путь клиента по воронке | Диалоги клиента, отсортированные по started_at, с указанием funnel_stage бота |
| Конверсия офферов | Количество сделок в статусе paid к общему числу созданных за период |
| Средний чек | Сумма оплаченных сделок, делённая на их количество |
| Заработок менеджера | Рассчитывается по схеме вознаграждения — требует решения (см. открытые вопросы ТЗ) |
| Неполные данные клиента Сумма и кол-во продаж пользователя | birth_time is null или birth_city is null |
[/TABLE]

9. Что закладывается под следующие этапы
Поля и таблицы, которые не используются в MVP, но структура должна их допускать без миграции с потерей данных:
Статусы воронки: отдельная таблица funnel_statuses и поле status_id в conversations
Задачи: таблица tasks со связями на clients, users, deals
Шаблоны: таблица templates со связью на users для личных шаблонов
Рассрочка: таблица deal_payments — несколько платежей по одной сделке. По этой причине сумма оплаты не хранится в deals как единственное значение, а выводится из связанных платежей
10. Требования к целостности
Уникальный индекс clients.telegram_id — на уровне базы данных
Уникальный индекс conversations (client_id, bot_id) — один диалог на пару клиент-бот
Уникальный индекс telegram_updates (bot_id, update_id) — идемпотентность входящих
Уникальный индекс payment_events.provider_event_id — идемпотентность платежей
Уникальный индекс campaign_deliveries (campaign_id, client_id) — защита от повторной записи рассылки
Создание клиента и диалога выполняется в одной транзакции. При конфликте вставки по telegram_id система читает существующую запись и продолжает работу с ней
Внешние ключи с on delete restrict для всех связей, кроме журнальных таблиц