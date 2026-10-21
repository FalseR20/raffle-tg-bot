"""FSM states."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ParticipantFlow(StatesGroup):
    amount = State()
    receipt = State()


class ReviewFlow(StatesGroup):
    reject_reason = State()


class RaffleWizard(StatesGroup):
    title = State()
    description = State()
    ticket_price = State()
    min_amount = State()
    prizes_count = State()
    start_at = State()
    end_at = State()
    channel_url = State()
    channel_title = State()
    organizations = State()
    extend_end = State()


class RaffleEdit(StatesGroup):
    value = State()
    organizations = State()


class OrganizationWizard(StatesGroup):
    name = State()
    slug = State()
    description = State()
    review_chat = State()
    review_thread = State()
    contact_url = State()
    edit_value = State()


class PaymentMethodWizard(StatesGroup):
    title = State()
    instructions = State()
    edit_title = State()
    edit_instructions = State()


class RoleWizard(StatesGroup):
    organizer_id = State()
    org_rep_id = State()


class SettingsWizard(StatesGroup):
    value = State()


class ApplicationSearch(StatesGroup):
    query = State()


class ParticipantSearch(StatesGroup):
    query = State()


class WinnerWizard(StatesGroup):
    place = State()
    ticket = State()
