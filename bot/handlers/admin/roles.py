"""Roles, invitations and role assignment in the admin panel."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import InviteRole, RoleName
from bot.db.repositories import invites as invites_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import roles as roles_repo
from bot.keyboards.admin import (
    invites_list,
    role_organization_choice,
    roles_list,
    roles_menu,
)
from bot.middlewares.admin import ManagersOnlyMiddleware
from bot.services import invites as invites_service
from bot.services import notifications
from bot.services import roles as roles_service
from bot.services.errors import ServiceError
from bot.services.permissions import Permissions
from bot.services.users import display_name
from bot.states import RoleWizard
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import utcnow

router = Router(name="admin_roles")
router.callback_query.middleware(ManagersOnlyMiddleware())
router.message.middleware(ManagersOnlyMiddleware())


@router.callback_query(F.data == "adm:roles")
async def on_roles_menu(callback: CallbackQuery, bot: Bot, permissions: Permissions) -> None:
    if callback.message is None:
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "👤 *Роли*\n\nУправление организаторами и представителями организаций\\.",
        reply_markup=roles_menu(permissions),
    )


@router.callback_query(F.data == "adm:roles_organizers")
async def on_organizers(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_organizers:
        await callback.answer(
            "Только администратор может управлять организаторами", show_alert=True
        )
        return
    roles = await roles_repo.list_active(session, role=RoleName.ORGANIZER)
    entries = [
        (
            role.user_id,
            "organizer",
            f"{display_name(role.user)} (ID {role.user_id})",
        )
        for role in roles
    ]
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "👤 *Организаторы*\n\nНажмите на имя, чтобы отозвать роль:",
        reply_markup=roles_list(entries),
    )


@router.callback_query(F.data == "adm:roles_reps")
async def on_reps(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_org_representatives:
        await callback.answer("Нет доступа", show_alert=True)
        return
    roles = await roles_repo.list_active(session, role=RoleName.ORG_REP)
    entries = [
        (
            role.user_id,
            f"org_rep:{role.organization_id}",
            f"{display_name(role.user)} · {role.organization.name if role.organization else '—'}",
        )
        for role in roles
    ]
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "🏢 *Представители организаций*\n\nНажмите на имя, чтобы отозвать роль:",
        reply_markup=roles_list(entries),
    )


@router.callback_query(F.data.regexp(r"^adm:role_revoke:\d+:(organizer|org_rep:\d+)$"))
async def on_revoke(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None or callback.data is None:
        return
    parts = callback.data.split(":")
    target_id = int(parts[2])
    if parts[3] == "organizer":
        role = RoleName.ORGANIZER
        organization_id = None
    else:
        role = RoleName.ORG_REP
        organization_id = int(parts[4])
    try:
        await roles_service.revoke_role(
            session,
            actor=permissions,
            target_user_id=target_id,
            role=role,
            organization_id=organization_id,
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Роль отозвана")
    await notifications.send_text(
        bot,
        target_id,
        "Ваша роль в боте изменена: доступ к админ\\-панели обновлён\\.",
    )
    if role is RoleName.ORGANIZER:
        await on_organizers(callback, bot, session, permissions)
    else:
        await on_reps(callback, bot, session, permissions)


@router.callback_query(F.data.regexp(r"^adm:role_add_id:(organizer|org_rep)$"))
async def on_add_role(
    callback: CallbackQuery,
    bot: Bot,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    role = callback.data.rsplit(":", 1)[1]
    if role == "organizer" and not permissions.can_manage_organizers:
        await callback.answer("Только администратор может назначать организаторов", show_alert=True)
        return
    if role == "org_rep" and not permissions.can_manage_org_representatives:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(RoleWizard.organizer_id if role == "organizer" else RoleWizard.org_rep_id)
    await state.update_data(role=role)
    await callback.answer()
    from bot.keyboards.admin import wizard_cancel

    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Введите Telegram ID пользователя. Пользователь должен хотя бы раз открыть бота.",
        reply_markup=wizard_cancel("adm:roles"),
    )


@router.message(RoleWizard.organizer_id, F.text)
async def on_organizer_id(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    await state.clear()
    await _assign_by_id(
        message, bot, session, permissions, role=RoleName.ORGANIZER, organization_id=None
    )


@router.message(RoleWizard.org_rep_id, F.text)
async def on_org_rep_id(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    try:
        target_id = int((message.text or "").strip())
    except ValueError:
        await notifications.send_text(bot, message.chat.id, "Telegram ID должен быть числом\\.")
        return
    await state.update_data(target_id=target_id)
    organizations = await orgs_repo.list_all(session, only_active=True)
    await notifications.send_text(
        bot,
        message.chat.id,
        "Выберите организацию для представителя:",
        reply_markup=role_organization_choice(
            [(organization.id, organization.name) for organization in organizations],
            "org_rep",
        ),
    )


async def _assign_by_id(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    *,
    role: RoleName,
    organization_id: int | None,
    target_id: int | None = None,
) -> None:
    if target_id is None:
        try:
            target_id = int((message.text or "").strip())
        except ValueError:
            await notifications.send_text(bot, message.chat.id, "Telegram ID должен быть числом\\.")
            return
    try:
        await roles_service.assign_role(
            session,
            actor=permissions,
            target_user_id=target_id,
            role=role,
            organization_id=organization_id,
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(exc.user_message))
        return
    await notifications.send_text(
        bot,
        message.chat.id,
        "✅ Роль выдана\\.",
    )
    role_label = "организатора" if role is RoleName.ORGANIZER else "представителя организации"
    await notifications.send_text(
        bot,
        target_id,
        f"Вам выдана роль {escape_markdown_v2(role_label)}\\. Откройте /admin\\.",
    )


@router.callback_query(F.data.regexp(r"^adm:role_org:(org_rep|invite_org_rep):\d+$"))
async def on_role_org(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    _, _, mode, organization_raw = callback.data.split(":")
    organization_id = int(organization_raw)
    organization = await orgs_repo.get(session, organization_id)
    if organization is None:
        await callback.answer("Организация не найдена", show_alert=True)
        return
    if mode == "org_rep":
        data = await state.get_data()
        target_id = int(data.get("target_id", 0))
        await state.clear()
        await callback.answer()
        await _assign_by_id(
            callback.message,
            bot,
            session,
            permissions,
            role=RoleName.ORG_REP,
            organization_id=organization_id,
            target_id=target_id,
        )
        return
    try:
        created = await invites_service.create_invite(
            session,
            actor=permissions,
            role=InviteRole.ORG_REP,
            organization_id=organization_id,
            bot_username=(await bot.me()).username or "",
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "🔗 *Одноразовое приглашение*\n\n"
        f"{escape_markdown_v2(created.url)}\n\n"
        "Ссылка действует 7 дней и срабатывает один раз\\. "
        "Кто откроет её первым, получит роль\\. Не пересылайте её публично\\.",
    )


@router.callback_query(F.data.regexp(r"^adm:role_invite:(organizer|org_rep)$"))
async def on_invite(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None or callback.data is None:
        return
    role = callback.data.rsplit(":", 1)[1]
    if role == "organizer" and not permissions.can_manage_organizers:
        await callback.answer(
            "Только администратор может приглашать организаторов", show_alert=True
        )
        return
    if role == "org_rep":
        organizations = await orgs_repo.list_all(session, only_active=True)
        await callback.answer()
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "Выберите организацию для приглашения:",
            reply_markup=role_organization_choice(
                [(organization.id, organization.name) for organization in organizations],
                "invite_org_rep",
            ),
        )
        return
    try:
        created = await invites_service.create_invite(
            session,
            actor=permissions,
            role=InviteRole.ORGANIZER,
            organization_id=None,
            bot_username=(await bot.me()).username or "",
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "🔗 *Одноразовое приглашение организатора*\n\n"
        f"{escape_markdown_v2(created.url)}\n\n"
        "Ссылка действует 7 дней и срабатывает один раз\\. "
        "Кто откроет её первым, получит роль\\.",
    )


@router.callback_query(F.data == "adm:invites")
async def on_invites(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_org_representatives:
        await callback.answer("Нет доступа", show_alert=True)
        return
    invites = await invites_repo.list_all(session)
    entries = []
    for invite in invites:
        if invite.used_at is not None:
            status = "использовано"
        elif invite.revoked_at is not None:
            status = "отозвано"
        elif invite.expires_at <= utcnow():
            status = "истекло"
        else:
            status = "активно"
        org = invite.organization.name if invite.organization else "—"
        entries.append(
            (
                invite.id,
                f"{invite.role.value} · {org} · {status}",
                status,
            )
        )
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "🔗 *Приглашения*\n\nНажмите на активное приглашение, чтобы отозвать его:",
        reply_markup=invites_list(entries),
    )


@router.callback_query(F.data.regexp(r"^adm:invite_revoke:\d+$"))
async def on_invite_revoke(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None or callback.data is None:
        return
    try:
        await invites_service.revoke_invite(
            session,
            actor=permissions,
            invite_id=int(callback.data.rsplit(":", 1)[1]),
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Приглашение отозвано")
    await on_invites(callback, bot, session, permissions)
