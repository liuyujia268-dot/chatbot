"""Small view-state helpers; no Tk, platform or model side effects."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReadingPosition:
    follow: bool = True
    anchor_id: int | None = None
    offset: float = 0
    fraction: float = 1


def near_bottom(view: tuple[float, float]) -> bool:
    return view[1] >= 0.96


def has_new_tail(old_ids: tuple[int, ...], new_ids: tuple[int, ...]) -> bool:
    return bool(old_ids and new_ids and old_ids[-1] in new_ids and new_ids.index(old_ids[-1]) < len(new_ids) - 1)
