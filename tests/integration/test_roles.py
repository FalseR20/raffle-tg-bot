"""Roles, permissions, invites and consent integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.db.enums import InviteRole, RoleName
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import roles as roles_repo
from bot.db.session import session_scope
from bot.services import invites as invites_service
from bot.services import roles as roles_service
from bot.services import users as users_service
from bot.services.cards import NoReviewerAvailableError, resolve_target
from bot.services.errors import AlreadyProcessedError, PermissionDeniedError
from bot.services.permissions import Permissions, load_permissions
from bot.services.settings import environment_defaults
from tests.helpers import (
    make_application,
    make_organization,
    make_raffle,
    make_user,
)

pytestmark = pytest.mark.integration

ADMIN = Permissions(user_id=100, is_admin=True)
ORGANIZER = Permissions(user_id=300, is_organizer=True)
REP = Permissions(user_id=400, org_rep_ids=frozenset({1}))


async def test_sync_admin_roles_grants_and_revokes(session_factory):
    async with session_scope(session_factory) as session:
        await make_user(session, 100)
        await make_user(session, 101)
        await make_user(session, 102)
        await make_organization(session)
        await roles_service.sync_admin_roles(session, frozenset({100, 101}), now=datetime.now(UTC))
    async with session_scope(session_factory) as session:
        await roles_service.assign_role(
            session,
            actor=ADMIN,
            target_user_id=101,
            role=RoleName.ORGANIZER,
            organization_id=None,
            now=datetime.now(UTC),
        )
        granted, revoked = await roles_service.sync_admin_roles(
            session, frozenset({100, 102}), now=datetime.now(UTC)
        )
        assert 102 in granted
        assert 101 in revoked
    async with session_factory() as session:
        assert await roles_repo.has_role(session, 100, RoleName.ADMIN)
        assert not await roles_repo.has_role(session, 101, RoleName.ADMIN)
        assert await roles_repo.has_role(session, 101, RoleName.ORGANIZER)
        assert await roles_repo.has_role(session, 102, RoleName.ADMIN)


async def test_organizer_assignment_requires_admin(session_factory):
    async with session_scope(session_factory) as session:
        await make_user(session, 300)
        await make_user(session, 500)
        await make_user(session, 501)
        org = await make_organization(session)
        organizer = await roles_service.assign_role(
            session,
            actor=ADMIN,
            target_user_id=300,
            role=RoleName.ORGANIZER,
            organization_id=None,
            now=datetime.now(UTC),
        )
        del organizer
    async with session_factory() as session:
        permissions = await load_permissions(session, 300, env_admin_ids=frozenset({100}))
        assert permissions.is_organizer
        assert permissions.can_manage_organizations
        assert not permissions.can_manage_organizers
        with pytest.raises(PermissionDeniedError):
            await roles_service.assign_role(
                session,
                actor=permissions,
                target_user_id=501,
                role=RoleName.ORGANIZER,
                organization_id=None,
                now=datetime.now(UTC),
            )
        await roles_service.assign_role(
            session,
            actor=permissions,
            target_user_id=501,
            role=RoleName.ORG_REP,
            organization_id=org.id,
            now=datetime.now(UTC),
        )


async def test_admin_role_cannot_be_assigned_by_bot(session_factory):
    async with session_scope(session_factory) as session:
        await make_user(session, 500)
        with pytest.raises(PermissionDeniedError):
            await roles_service.assign_role(
                session,
                actor=ADMIN,
                target_user_id=500,
                role=RoleName.ADMIN,
                organization_id=None,
                now=datetime.now(UTC),
            )


async def test_invite_is_one_time_and_expires(session_factory):
    async with session_scope(session_factory) as session:
        await make_user(session, 400)
        org = await make_organization(session)
        created = await invites_service.create_invite(
            session,
            actor=ADMIN,
            role=InviteRole.ORG_REP,
            organization_id=org.id,
            bot_username="test_bot",
            now=datetime.now(UTC),
        )
        assert created.url.startswith("https://t.me/test_bot?start=inv_")
        redeemed = await invites_service.redeem_invite(
            session, token=created.token, user_id=400, now=datetime.now(UTC)
        )
        assert redeemed.organization_id == org.id
    async with session_scope(session_factory) as session:
        with pytest.raises(AlreadyProcessedError):
            await invites_service.redeem_invite(
                session, token=created.token, user_id=400, now=datetime.now(UTC)
            )


async def test_expired_and_revoked_invites_fail(session_factory):
    async with session_scope(session_factory) as session:
        await make_user(session, 400)
        org = await make_organization(session)
        expired = await invites_service.create_invite(
            session,
            actor=ADMIN,
            role=InviteRole.ORG_REP,
            organization_id=org.id,
            bot_username="bot",
            now=datetime.now(UTC) - timedelta(days=8),
        )
        revoked = await invites_service.create_invite(
            session,
            actor=ADMIN,
            role=InviteRole.ORG_REP,
            organization_id=org.id,
            bot_username="bot",
            now=datetime.now(UTC),
        )
        await invites_service.revoke_invite(
            session, actor=ADMIN, invite_id=revoked.invite.id, now=datetime.now(UTC)
        )
    async with session_scope(session_factory) as session:
        with pytest.raises(AlreadyProcessedError):
            await invites_service.redeem_invite(
                session, token=expired.token, user_id=400, now=datetime.now(UTC)
            )
    async with session_scope(session_factory) as session:
        with pytest.raises(AlreadyProcessedError):
            await invites_service.redeem_invite(
                session, token=revoked.token, user_id=400, now=datetime.now(UTC)
            )


async def test_role_revocation_is_immediate(session_factory):
    async with session_scope(session_factory) as session:
        await make_user(session, 400)
        org = await make_organization(session)
        invite = await invites_service.create_invite(
            session,
            actor=ADMIN,
            role=InviteRole.ORG_REP,
            organization_id=org.id,
            bot_username="bot",
            now=datetime.now(UTC),
        )
        await invites_service.redeem_invite(
            session, token=invite.token, user_id=400, now=datetime.now(UTC)
        )
    async with session_factory() as session:
        permissions = await load_permissions(session, 400, env_admin_ids=frozenset({100}))
        assert permissions.can_review_organization(org.id)
    async with session_scope(session_factory) as session:
        await roles_service.revoke_role(
            session,
            actor=ADMIN,
            target_user_id=400,
            role=RoleName.ORG_REP,
            organization_id=org.id,
            now=datetime.now(UTC),
        )
    async with session_factory() as session:
        permissions = await load_permissions(session, 400, env_admin_ids=frozenset({100}))
        assert not permissions.has_admin_panel
        entries = await audit_repo.list_entries(session, limit=50)
        actions = {entry.action for entry in entries}
        assert "role_granted" in actions
        assert "role_revoked" in actions


async def test_self_review_routes_to_management_chat(session_factory, env_settings):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )
        role = await roles_service.assign_role(
            session,
            actor=ADMIN,
            target_user_id=500,
            role=RoleName.ORG_REP,
            organization_id=org.id,
            now=datetime.now(UTC),
        )
        del role
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        settings = environment_defaults(env_settings)
        target = await resolve_target(session, application, settings, env_admin_ids=frozenset())
        assert target.chat_id == settings.management_log_chat_id
        assert target.is_management_fallback
    env_settings.management_log_chat_id = None
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        settings = environment_defaults(env_settings)
        with pytest.raises(NoReviewerAvailableError):
            await resolve_target(session, application, settings, env_admin_ids=frozenset())


async def test_consent_versions_require_reacceptance(session_factory, env_settings):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        settings = environment_defaults(env_settings)
        assert not users_service.has_valid_consent(user, settings)
        await users_service.accept_consent(
            session, user, settings=settings, now=datetime.now(UTC), publish_name=None
        )
        assert users_service.has_valid_consent(user, settings)
        from dataclasses import replace

        changed = replace(environment_defaults(env_settings), rules_version="2")
        assert not users_service.has_valid_consent(user, changed)


async def test_startup_admin_sync_returns_lists(session_factory, env_settings):
    from bot.main import _sync_admins

    granted, revoked = await _sync_admins(session_factory, env_settings)
    assert set(granted) == {100, 101}
    assert revoked == []
