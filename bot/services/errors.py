"""Service-layer errors with Russian user-facing messages."""

from __future__ import annotations


class ServiceError(Exception):
    """Base service error carrying a safe Russian message for users."""

    user_message = "Не удалось выполнить действие. Попробуйте позже."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.user_message)
        if message:
            self.user_message = message


class NotFoundError(ServiceError):
    user_message = "Запись не найдена."


class PermissionDeniedError(ServiceError):
    user_message = "У вас нет прав для этого действия."


class InvalidStateError(ServiceError):
    user_message = "Действие сейчас недоступно."


class AlreadyProcessedError(ServiceError):
    user_message = "Заявка уже обработана."


class SelfReviewError(ServiceError):
    user_message = "Нельзя подтверждать собственную заявку."


class NoReviewerAvailableError(ServiceError):
    user_message = (
        "Нет второго проверяющего: участник не может подтвердить собственную оплату. "
        "Обратитесь к администратору."
    )


class ValidationError(ServiceError):
    user_message = "Введены некорректные данные."


class RaffleNotAcceptingError(ServiceError):
    user_message = "Приём заявок завершён."


class StorageError(ServiceError):
    user_message = "Не удалось сохранить чек. Попробуйте ещё раз."


class ReceiptError(ServiceError):
    user_message = "Чек не подходит. Пришлите, пожалуйста, другой файл."


class IntegrityError(ServiceError):
    user_message = "Обнаружено расхождение данных. Действие остановлено."


class ExportError(ServiceError):
    user_message = "Не удалось подготовить файл."
