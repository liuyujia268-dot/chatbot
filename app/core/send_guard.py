"""Task-local final send guard, evaluated immediately before transport write."""
from contextvars import ContextVar


class OutboundNotSubmittedError(RuntimeError):
    pass


submission_guard: ContextVar = ContextVar('outbound_submission_guard', default=None)


def check_submission() -> None:
    guard = submission_guard.get()
    if guard is not None and not guard():
        raise OutboundNotSubmittedError('发送状态已变化；尚未提交的动作已取消')
