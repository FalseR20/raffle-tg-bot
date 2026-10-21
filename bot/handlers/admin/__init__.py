"""Admin panel handlers."""

from __future__ import annotations

from aiogram import Router

from bot.handlers.admin import (
    applications,
    exports,
    insights,
    menu,
    organizations,
    raffles,
    roles,
    settings,
)

admin_router = Router(name="admin")
admin_router.include_router(menu.router)
admin_router.include_router(raffles.router)
admin_router.include_router(applications.router)
admin_router.include_router(organizations.router)
admin_router.include_router(roles.router)
admin_router.include_router(exports.router)
admin_router.include_router(settings.router)
admin_router.include_router(insights.router)

__all__ = ["admin_router"]
