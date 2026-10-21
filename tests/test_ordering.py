from types import SimpleNamespace

from bot.domain.ordering import randomized_organizations, stable_hash
from bot.services.applications import PAYMENT_CODE_ALPHABET, PAYMENT_CODE_LENGTH


def _orgs(count=6):
    return [SimpleNamespace(id=index, name=f"Org {index}") for index in range(1, count + 1)]


def test_payment_code_alphabet_excludes_confusing_characters():
    assert PAYMENT_CODE_LENGTH == 5
    assert not set("0O1I") & set(PAYMENT_CODE_ALPHABET)
    assert len(PAYMENT_CODE_ALPHABET) >= 30


def test_stable_hash_is_deterministic():
    assert stable_hash(1, 2) == stable_hash(1, 2)
    assert stable_hash(1, 2) != stable_hash(2, 1)


def test_organization_order_is_stable_per_user_and_differs_between_users():
    orgs = _orgs()
    first = [org.id for org in randomized_organizations(orgs, user_id=1, raffle_id=2)]
    again = [org.id for org in randomized_organizations(orgs, user_id=1, raffle_id=2)]
    other = [org.id for org in randomized_organizations(orgs, user_id=3, raffle_id=2)]
    assert first == again
    assert first != other
    assert sorted(first) == sorted(org.id for org in orgs)


def test_single_organization_is_not_randomized():
    orgs = _orgs(1)
    assert randomized_organizations(orgs, user_id=1, raffle_id=2) == orgs


def test_apps_callback_parsing():
    from bot.handlers.admin.applications import parse_apps_filter

    assert parse_apps_filter("adm:apps_pending") == ("pending", 0)
    assert parse_apps_filter("adm:apps_all:42") == ("all", 42)
    assert parse_apps_filter("adm:apps_confirmed:7") == ("confirmed", 7)


def test_permission_matrix():
    from bot.db.enums import RoleName
    from bot.services.permissions import Permissions, build_permissions

    participant = build_permissions(1, [], env_admin_ids=frozenset())
    assert not participant.has_admin_panel
    organizer_role = SimpleNamespace(role=RoleName.ORGANIZER, organization_id=None)
    organizer = build_permissions(2, [organizer_role], env_admin_ids=frozenset())
    assert organizer.has_admin_panel
    assert organizer.can_manage_raffles
    assert organizer.can_manage_organizations
    assert not organizer.can_manage_organizers
    rep_role = SimpleNamespace(role=RoleName.ORG_REP, organization_id=7)
    rep = build_permissions(3, [rep_role], env_admin_ids=frozenset())
    assert rep.can_review_organization(7)
    assert not rep.can_review_organization(8)
    assert not rep.can_manage_raffles
    admin = Permissions(user_id=4, is_admin=True)
    assert admin.can_manage_organizers
