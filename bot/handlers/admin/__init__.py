"""Admin panel handlers."""

from __future__ import annotations

from aiogram import Router

from bot.handlers.admin import (
    applications,
    exports,
    insights,
    menu,
    organizations,
    payment_methods,
    raffle_wizard,
    raffles,
    roles,
    settings,
)
from bot.middlewares.admin import AdminPanelMiddleware

admin_router = Router(name="admin")
admin_router.callback_query.middleware(AdminPanelMiddleware())
for _module in (
    menu,
    raffles,
    raffle_wizard,
    applications,
    organizations,
    payment_methods,
    roles,
    exports,
    settings,
    insights,
):
    _module.router.callback_query.middleware(AdminPanelMiddleware())
admin_router.include_router(menu.router)
admin_router.include_router(raffles.router)
admin_router.include_router(raffle_wizard.router)
admin_router.include_router(applications.router)
admin_router.include_router(organizations.router)
admin_router.include_router(payment_methods.router)
admin_router.include_router(roles.router)
admin_router.include_router(exports.router)
admin_router.include_router(settings.router)
admin_router.include_router(insights.router)

__all__ = ["admin_router"]
