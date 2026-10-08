"""Durable outbound lifecycle. Generation is not delivery; only ACK is sent."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from uuid import uuid4

from app.core.platform import OutboundMessage
from app.storage.database import Database
from app.core.send_guard import submission_guard, OutboundNotSubmittedError
from datetime import UTC, datetime
from app.core.platform import PlatformMessage


STATUS_LABELS = {"candidate": "候选（未提交）", "pending": "待发送", "simulated": "模拟（未发给QQ）",
                 "sent": "已发送（不代表已读）", "failed": "失败", "unknown": "结果未知（不要直接重发）"}
logger = logging.getLogger(__name__)


class DeliveryService:
    def __init__(self, database: Database, *, callback=lambda record: None, on_history=lambda *args: None):
        self.database = database
        self.callback = callback
        self.on_history = on_history
        self._inflight: set[str] = set()

    def prepare(self, message: OutboundMessage, *, batch_id: str | None = None, index: int = 0) -> str:
        record_id = uuid4().hex
        with self.database.connect() as conn:
            conn.execute("INSERT INTO outbound_deliveries (id,batch_id,segment_index,platform,account_id,conversation_id,text,metadata_json,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (record_id, batch_id or record_id, index, message.platform, message.account_id,
                                                    message.conversation_id, message.text, json.dumps(dict(message.metadata), ensure_ascii=False), datetime.now(UTC).isoformat()))
        self._notify(self.get(record_id))
        return record_id

    def get(self, record_id: str) -> dict:
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM outbound_deliveries WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise ValueError("发送记录不存在")
        return dict(row)

    @staticmethod
    def message(record: dict) -> OutboundMessage:
        metadata = json.loads(record['metadata_json'])
        if 'local_image_paths' in metadata:
            metadata['local_image_paths'] = tuple(metadata['local_image_paths'])
        return OutboundMessage(record['platform'], record['account_id'], record['conversation_id'], record['text'],
                               metadata)

    def _state(self, record_id, status, code=None, error=None, platform_id=None):
        with self.database.connect() as conn:
            conn.execute("UPDATE outbound_deliveries SET status=?,error_code=?,error_text=?,platform_message_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (status, code, error, platform_id, record_id))
        self._notify(self.get(record_id))

    def _notify(self, record):
        try:
            self.callback(record)
        except Exception as exc:
            logger.warning('delivery_observer_failed error=%s', type(exc).__name__)

    async def send(self, record_id, sender, *, guard=lambda message: True) -> dict:
        record = self.get(record_id)
        # Never resend ACKed, simulated, uncertain, or concurrent submissions.
        if record['status'] in {'sent', 'simulated', 'unknown', 'pending'} or record_id in self._inflight:
            return record
        message = self.message(record)
        if not guard(message):
            self._state(record_id, 'candidate', 'invalidated', '状态已变化，未提交；已提交的段落不撤回')
            return self.get(record_id)
        self._inflight.add(record_id)
        simulated = bool(getattr(sender, 'dry_run', False))
        self._state(record_id, 'pending')
        token = submission_guard.set(lambda: guard(message) and simulated == bool(getattr(sender, 'dry_run', False)))
        try:
            receipt = await sender.send(message)
            if not simulated and not receipt:
                self._state(record_id, 'unknown', 'no_ack', '平台未提供发送确认；请先核对QQ记录，不要直接重发')
            else:
                self._state(record_id, 'simulated' if simulated else 'sent', platform_id=receipt)
                try:
                    self._commit_history(record_id, message, simulated)
                except Exception as exc:
                    # Local observer/storage trouble cannot undo a platform ACK
                    # or make a successful message eligible for retransmission.
                    logger.error('delivered_history_commit_failed error=%s', type(exc).__name__)
        except OutboundNotSubmittedError:
            self._state(record_id, 'candidate', 'invalidated', '状态已变化，尚未提交的动作已取消')
        except asyncio.CancelledError:
            self._state(record_id, 'unknown', 'cancelled_after_submit', '提交期间连接或任务结束；请核对QQ记录')
            raise
        except (TimeoutError, ConnectionError, OSError):
            self._state(record_id, 'unknown', 'transport_unknown', '请求超时或连接中断，结果未知；请核对QQ记录，不要直接重发')
        except Exception as exc:
            # Exception bodies may contain credentials or transport payloads.
            self._state(record_id, 'failed', type(exc).__name__, '发送失败；检查目标、权限或连接后可重试，也可复制正文')
        finally:
            submission_guard.reset(token)
            self._inflight.discard(record_id)
        return self.get(record_id)

    def _commit_history(self, record_id, message, simulated):
        with self.database.connect() as conn:
            row = conn.execute('SELECT history_message_id FROM outbound_deliveries WHERE id=?', (record_id,)).fetchone()
            if row[0] is not None:
                return
            cursor = conn.execute("INSERT INTO messages (session_id,role,content,history_kind) VALUES (?,'assistant',?,?)",
                                  (message.session_id, message.text, 'simulated' if simulated else 'real'))
            conn.execute('UPDATE outbound_deliveries SET history_message_id=? WHERE id=?', (cursor.lastrowid, record_id))
            receipt = conn.execute('SELECT platform_message_id FROM outbound_deliveries WHERE id=?', (record_id,)).fetchone()[0]
        if receipt and not simulated:
            # Dedup subsequent self echoes; the ACKed message is already in history.
            self.database.save_inbound_platform_event(PlatformMessage(message.platform, message.account_id, message.conversation_id,
                message.account_id, str(receipt), datetime.now(UTC), 'text', message.text, True))
        self.on_history(message, simulated)

    def recover_pending(self):
        """Process restart cannot establish whether a pending network call reached QQ."""
        with self.database.connect() as conn:
            conn.execute("UPDATE outbound_deliveries SET status='unknown',error_code='restart',"
                         "error_text='上次提交结果未知；请核对QQ记录，不要直接重发' WHERE status='pending'")
