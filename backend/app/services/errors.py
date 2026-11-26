"""Framework-agnostic errors raised by services; mapped to HTTP responses in main.py."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if status_code:
            self.status_code = status_code


class BadRequest(ServiceError):
    status_code = 400


class Unauthorized(ServiceError):
    status_code = 401


class Forbidden(ServiceError):
    status_code = 403


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409
