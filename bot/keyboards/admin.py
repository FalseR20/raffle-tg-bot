"""Admin panel inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.enums import RaffleStatus
from bot.services.permissions import Permissions


def _rows(pairs: list[tuple[str, str]], columns: int = 2) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for text, callback in pairs:
        row.append(InlineKeyboardButton(text=text, callback_data=callback))
        if len(row) == columns:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def main_menu(permissions: Permissions) -> InlineKeyboardMarkup:
    if permissions.can_manage_raffles:
        rows = [
            [
                InlineKeyboardButton(text="🎗 Розыгрыши", callback_data="adm:raffles"),
                InlineKeyboardButton(text="📥 Заявки", callback_data="adm:apps"),
            ],
            [
                InlineKeyboardButton(text="👥 Участники", callback_data="adm:participants"),
                InlineKeyboardButton(text="🏢 Организации", callback_data="adm:orgs"),
            ],
            [
                InlineKeyboardButton(text="💳 Способы оплаты", callback_data="adm:methods"),
                InlineKeyboardButton(text="📤 Экспорт", callback_data="adm:exports"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
                InlineKeyboardButton(text="🎲 Розыгрыш", callback_data="adm:draw"),
            ],
            [
                InlineKeyboardButton(text="👤 Роли", callback_data="adm:roles"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm:settings"),
            ],
            [
                InlineKeyboardButton(text="🧾 Аудит", callback_data="adm:audit"),
                InlineKeyboardButton(text="🛡 Целостность", callback_data="adm:integrity"),
            ],
        ]
    else:
        org_id = next(iter(permissions.org_rep_ids)) if permissions.org_rep_ids else 0
        rows = [
            [
                InlineKeyboardButton(
                    text="📥 Заявки моей организации", callback_data=f"adm:orgapps:{org_id}"
                )
            ],
            [
                InlineKeyboardButton(text="📤 Экспорт", callback_data="adm:exports"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
            ],
            [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="adm:help")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def raffles_list(
    raffles: list[tuple[int, str, RaffleStatus]],
    *,
    can_manage: bool,
) -> InlineKeyboardMarkup:
    status_icon = {
        RaffleStatus.DRAFT: "📝",
        RaffleStatus.SCHEDULED: "⏳",
        RaffleStatus.ACTIVE: "🟢",
        RaffleStatus.CLOSED: "🔒",
        RaffleStatus.FINISHED: "🏁",
        RaffleStatus.ARCHIVED: "🗄",
    }
    rows = [
        [
            InlineKeyboardButton(
                text=f"{status_icon.get(status, '')} {title[:40]}",
                callback_data=f"adm:raffle:{raffle_id}",
            )
        ]
        for raffle_id, title, status in raffles
    ]
    if can_manage:
        rows.append(
            [InlineKeyboardButton(text="➕ Создать розыгрыш", callback_data="adm:raffle_new")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def raffle_card(
    raffle_id: int,
    status: RaffleStatus,
    *,
    can_manage: bool,
    applications_count: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_manage:
        if status in {RaffleStatus.DRAFT, RaffleStatus.SCHEDULED}:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="▶️ Активировать", callback_data=f"adm:raffle_activate:{raffle_id}"
                    )
                ]
            )
        if status is RaffleStatus.ACTIVE:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⏹ Остановить приём", callback_data=f"adm:raffle_stop:{raffle_id}"
                    )
                ]
            )
        if status is RaffleStatus.CLOSED:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="▶️ Возобновить приём", callback_data=f"adm:raffle_resume:{raffle_id}"
                    )
                ]
            )
        if status in {RaffleStatus.ACTIVE, RaffleStatus.CLOSED}:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🏁 Завершить", callback_data=f"adm:raffle_finish:{raffle_id}"
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📅 Продлить", callback_data=f"adm:raffle_extend:{raffle_id}"
                    )
                ]
            )
            if applications_count == 0:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="↩️ В черновик",
                            callback_data=f"adm:raffle_draft:{raffle_id}",
                        )
                    ]
                )
        if status is RaffleStatus.FINISHED:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🗄 В архив", callback_data=f"adm:raffle_archive:{raffle_id}"
                    )
                ]
            )
            rows.append(
                [InlineKeyboardButton(text="🖨 PDF билетов", callback_data=f"adm:pdf:{raffle_id}")]
            )
        if status is RaffleStatus.DRAFT:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить", callback_data=f"adm:raffle_del:{raffle_id}"
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать", callback_data=f"adm:raffle_edit:{raffle_id}"
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="📄 Дублировать", callback_data=f"adm:raffle_dup:{raffle_id}"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="📊 Статистика", callback_data=f"adm:stats:{raffle_id}")]
    )
    rows.append([InlineKeyboardButton(text="⬅️ К розыгрышам", callback_data="adm:raffles")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def raffle_edit_fields(raffle_id: int, *, locked: bool) -> InlineKeyboardMarkup:
    fields = [
        ("Название", "title"),
        ("Описание", "description"),
        ("Цена билета", "ticket_price"),
        ("Мин. сумма", "min_amount"),
        ("Призы", "prizes_count"),
        ("Дата окончания", "end_at"),
        ("Ссылка на канал", "channel_url"),
        ("Название канала", "channel_title"),
        ("Организации", "organizations"),
    ]
    pairs = [
        (
            f"🔒 {label}"
            if locked
            and field
            in {
                "ticket_price",
                "min_amount",
                "prizes_count",
                "organizations",
            }
            else label,
            f"adm:raffle_set:{raffle_id}:{field}",
        )
        for label, field in fields
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *_rows(pairs),
            [InlineKeyboardButton(text="⬅️ К розыгрышу", callback_data=f"adm:raffle:{raffle_id}")],
        ]
    )


def finish_confirmation(raffle_id: int, pending_count: int) -> InlineKeyboardMarkup:
    rows = []
    if pending_count:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗂 Перевести на проверке в «истекла» и завершить",
                    callback_data=f"adm:raffle_finish_yes:{raffle_id}:1",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Сначала обработать заявки",
                    callback_data=f"adm:apps_pending:{raffle_id}",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Завершить", callback_data=f"adm:raffle_finish_yes:{raffle_id}:0"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"adm:raffle:{raffle_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_action(text: str, callback: str, cancel: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=callback)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=cancel)],
        ]
    )


def applications_menu(raffle_id: int | None = None) -> InlineKeyboardMarkup:
    suffix = f":{raffle_id}" if raffle_id else ""
    rows = [
        [
            InlineKeyboardButton(text="🕒 На проверке", callback_data=f"adm:apps_pending{suffix}"),
            InlineKeyboardButton(
                text="✅ Подтверждённые", callback_data=f"adm:apps_confirmed{suffix}"
            ),
        ],
        [
            InlineKeyboardButton(text="❌ Отклонённые", callback_data=f"adm:apps_rejected{suffix}"),
            InlineKeyboardButton(text="📋 Все", callback_data=f"adm:apps_all{suffix}"),
        ],
        [InlineKeyboardButton(text="🔎 Поиск", callback_data="adm:app_search")],
    ]
    if raffle_id:
        rows.append(
            [InlineKeyboardButton(text="⬅️ К розыгрышу", callback_data=f"adm:raffle:{raffle_id}")]
        )
    else:
        rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_card(application_id: int, *, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status in {"pending_review", "revision_requested"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить", callback_data=f"rev:confirm:{application_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"rev:reject:{application_id}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="📎 Запросить новый чек",
                    callback_data=f"rev:revision:{application_id}",
                )
            ]
        )
    if status == "confirmed":
        rows.append(
            [
                InlineKeyboardButton(
                    text="♻️ Аннулировать",
                    callback_data=f"rev:void:{application_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="📎 Отправить чек", callback_data=f"adm:app_file:{application_id}"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔔 Напомнить проверяющему", callback_data=f"adm:app_remind:{application_id}"
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ К заявкам", callback_data="adm:apps")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pagination(prefix: str, offset: int, total: int, page_size: int = 10) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:{max(0, offset - page_size)}")
        )
    if offset + page_size < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:{offset + page_size}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def organizations_menu(organizations: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if active else '⚪️'} {name[:45]}",
                callback_data=f"adm:org:{org_id}",
            )
        ]
        for org_id, name, active in organizations
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить организацию", callback_data="adm:org_new")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def organization_card(org_id: int, active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Название", callback_data=f"adm:org_set:{org_id}:name"),
                InlineKeyboardButton(text="🔗 Slug", callback_data=f"adm:org_set:{org_id}:slug"),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Описание", callback_data=f"adm:org_set:{org_id}:description"
                ),
                InlineKeyboardButton(text="💬 Чат", callback_data=f"adm:org_set:{org_id}:chat"),
            ],
            [
                InlineKeyboardButton(text="🧵 Тема", callback_data=f"adm:org_set:{org_id}:thread"),
                InlineKeyboardButton(
                    text="🔗 Контакт", callback_data=f"adm:org_set:{org_id}:contact"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚪️ Выключить" if active else "🟢 Включить",
                    callback_data=f"adm:org_toggle:{org_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Способы оплаты", callback_data=f"adm:methods:{org_id}"
                ),
                InlineKeyboardButton(text="🧪 Тест чата", callback_data=f"adm:org_test:{org_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ К организациям", callback_data="adm:orgs")],
        ]
    )


def payment_methods_menu(
    organization_id: int, methods: list[tuple[int, str, bool, int]]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if active else '⚪️'} {title[:40]}",
                callback_data=f"adm:method:{method_id}",
            )
        ]
        for method_id, title, active, _order in methods
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить способ", callback_data=f"adm:method_new:{organization_id}"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="⬅️ К организации", callback_data=f"adm:org:{organization_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_method_card(organization_id: int, method_id: int, active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Название", callback_data=f"adm:method_set:{method_id}:title"
                ),
                InlineKeyboardButton(
                    text="📝 Инструкция", callback_data=f"adm:method_set:{method_id}:instructions"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👁 Предпросмотр", callback_data=f"adm:method_preview:{method_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚪️ Выключить" if active else "🟢 Включить",
                    callback_data=f"adm:method_toggle:{method_id}",
                ),
                InlineKeyboardButton(text="⬆️", callback_data=f"adm:method_up:{method_id}"),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К способам", callback_data=f"adm:methods:{organization_id}"
                )
            ],
        ]
    )


def roles_menu(permissions: Permissions) -> InlineKeyboardMarkup:
    rows = []
    if permissions.can_manage_organizers:
        rows.append(
            [InlineKeyboardButton(text="👤 Организаторы", callback_data="adm:roles_organizers")]
        )
    rows.append(
        [InlineKeyboardButton(text="🏢 Представители организаций", callback_data="adm:roles_reps")]
    )
    if permissions.can_manage_organizers:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить организатора", callback_data="adm:role_add_id:organizer"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить представителя", callback_data="adm:role_add_id:org_rep"
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="🔗 Приглашения", callback_data="adm:invites")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_role_menu(role: str, permissions: Permissions) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🆔 По Telegram ID", callback_data=f"adm:role_add_id:{role}")],
        [
            InlineKeyboardButton(
                text="🔗 Создать приглашение", callback_data=f"adm:role_invite:{role}"
            )
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:roles")],
    ]
    if role == "org_rep":
        rows.insert(
            2,
            [
                InlineKeyboardButton(
                    text="📨 Переслать сообщение", callback_data="adm:role_forward:org_rep"
                )
            ],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def exported_raffles(raffles: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title[:50], callback_data=f"adm:export_raffle:{raffle_id}")]
        for raffle_id, title in raffles
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def export_formats(raffle_id: int, *, can_pdf: bool, include_all: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="CSV", callback_data=f"adm:export_do:{raffle_id}:csv"),
            InlineKeyboardButton(text="XLSX", callback_data=f"adm:export_do:{raffle_id}:xlsx"),
        ],
        [
            InlineKeyboardButton(
                text="🖨 PDF билетов",
                callback_data=f"adm:export_do:{raffle_id}:pdf"
                if can_pdf
                else "adm:export_pdf_locked",
            )
        ],
        [
            InlineKeyboardButton(
                text=("✅ " if include_all else "") + "Все статусы",
                callback_data=f"adm:export_toggle:{raffle_id}",
            )
        ],
        [InlineKeyboardButton(text="⬅️ К экспорту", callback_data="adm:exports")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu(values: list[tuple[str, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{label}: {value[:30]}", callback_data=f"adm:setting:{key}")]
        for key, label, value in values
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def draw_menu(raffles: list[tuple[int, str]], *, random_enabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🏆 {title[:40]}", callback_data=f"adm:winners:{raffle_id}")]
        for raffle_id, title in raffles
    ]
    if random_enabled:
        rows.append(
            [InlineKeyboardButton(text="🎲 Случайный розыгрыш", callback_data="adm:draw_random")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def winners_menu(raffle_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏅 Записать победителя", callback_data=f"adm:winner_add:{raffle_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="♻️ Сбросить победителей", callback_data=f"adm:winners_reset:{raffle_id}"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:draw")],
        ]
    )


def org_rep_applications(organization_id: int) -> InlineKeyboardMarkup:
    return applications_menu(raffle_id=None)


def wizard_cancel(callback: str = "rw:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=callback)]]
    )


def wizard_organizations(
    organizations: list[tuple[int, str]], selected: set[int]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if organization_id in selected else '◻️'} {name[:40]}",
                callback_data=f"rw:org:{organization_id}",
            )
        ]
        for organization_id, name in organizations
    ]
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="rw:org_done")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="rw:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_organizations(
    raffle_id: int, organizations: list[tuple[int, str]], selected: set[int]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if organization_id in selected else '◻️'} {name[:40]}",
                callback_data=f"rw:edit_org:{organization_id}",
            )
        ]
        for organization_id, name in organizations
    ]
    rows.append(
        [InlineKeyboardButton(text="✅ Сохранить", callback_data=f"rw:edit_org_done:{raffle_id}")]
    )
    rows.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"adm:raffle_edit:{raffle_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def roles_list(entries: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"❌ {label[:45]}", callback_data=f"adm:role_revoke:{user_id}:{role_key}"
            )
        ]
        for user_id, role_key, label in entries
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:roles")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def role_organization_choice(
    organizations: list[tuple[int, str]], role: str
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=name[:50], callback_data=f"adm:role_org:{role}:{organization_id}"
            )
        ]
        for organization_id, name in organizations
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:roles")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invites_list(invites: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"❌ {label[:45]}", callback_data=f"adm:invite_revoke:{invite_id}"
            )
        ]
        for invite_id, label, _status in invites
    ]
    rows.append(
        [InlineKeyboardButton(text="🔗 Новое приглашение", callback_data="adm:role_invite:org_rep")]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:roles")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def audit_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Скачать CSV", callback_data="adm:audit_csv")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")],
        ]
    )


def participants_list(entries: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label[:50], callback_data=f"adm:part:{user_id}")]
        for user_id, label in entries
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_navigation(prefix: str, offset: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:{max(0, offset - 10)}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:{offset + 10}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="adm:apps")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
