"""U06/U07: group request preservation, registered arrival-order media."""
import asyncio
from dataclasses import replace

from app.core.identity import conversation_key
from tests.ux_support import FakeImages, FakeModel, event, system


def test_unrelated_group_message_does_not_cancel_pending_mention(tmp_path):
    async def run():
        model = FakeModel()
        model.block = True
        s = system(tmp_path / 'db.sqlite', model=model, reply_group_ids=('90001',))
        pending = asyncio.create_task(s.adapter.handle_event(event('a', '请回答这个请求', group='90001', mention=True)))
        await asyncio.wait_for(model.started.wait(), 1)
        await s.adapter.handle_event(event('b', '普通背景闲聊', group='90001', sender='20003'))
        model.release.set()
        result = await pending
        assert result.reply == '合成回复' and len(s.sender.calls) == 1 and len(model.calls) == 1
        assert '请回答这个请求' in repr(model.calls[0])
        assert len(s.db.list_messages(conversation_key('qq', '10001', 'qq:group:90001'))) == 3
    asyncio.run(run())


def test_group_correction_keeps_request_and_regenerates(tmp_path):
    class Model(FakeModel):
        async def generate(self, messages):
            self.calls.append(tuple(messages))
            if len(self.calls) == 1:
                self.started.set()
                await asyncio.Event().wait()
            return '已按补充处理'
    async def run():
        model = Model()
        s = system(tmp_path / 'db.sqlite', model=model, reply_group_ids=('90001',))
        original = asyncio.create_task(s.adapter.handle_event(event('a', '算一下原问题', group='90001', mention=True)))
        await asyncio.wait_for(model.started.wait(), 1)
        await s.adapter.handle_event(event('b', '不是，是这个补充条件', group='90001'))
        await original
        assert len(s.sender.calls) == 1 and len(model.calls) == 2
        assert '算一下原问题' in repr(model.calls[-1]) and '这个补充条件' in repr(model.calls[-1])
    asyncio.run(run())


class SlowImages(FakeImages):
    def __init__(self, fail=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail
        self.calls = 0

    async def resolve(self, part):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise OSError('合成下载失败')
        return await super().resolve(part)


def test_slow_image_then_text_same_request_order_and_no_duplicate(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite')
        images = SlowImages()
        s.adapter._images = images
        image = asyncio.create_task(s.adapter.handle_event(event('pic', '', image=True)))
        await asyncio.wait_for(images.started.wait(), 1)
        text = asyncio.create_task(s.adapter.handle_event(event('text', '这是什么图')))
        await asyncio.sleep(0.02)
        assert s.model.calls == []
        # Another contact is not held by this image download.
        await s.adapter.handle_event(event('other', '其他联系人', sender='20003'))
        assert len(s.model.calls) == 1
        images.release.set()
        await asyncio.gather(image, text)
        assert len(s.model.calls) == 2
        call = s.model.calls[-1]
        assert not isinstance(call[-2].content, str) and call[-1].content == '这是什么图'
        await s.adapter.handle_event(event('pic', '', image=True))
        assert images.calls == 1 and len(s.model.calls) == 2
        stored = s.db.list_messages(conversation_key('qq', '10001', 'qq:private:20002'))
        assert stored[0].content.startswith('[图片') and stored[1].content == '这是什么图'
    asyncio.run(run())


def test_image_failure_and_bounded_timeout_do_not_late_reply(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite', image_wait_seconds=0.03)
        images = SlowImages()
        s.adapter._images = images
        pic = asyncio.create_task(s.adapter.handle_event(event('pic', '', image=True)))
        await images.started.wait()
        text = asyncio.create_task(s.adapter.handle_event(event('txt', '图片打不开也可以接话')))
        await asyncio.wait_for(asyncio.gather(pic, text), 1)
        assert len(s.model.calls) == 1 and len(s.sender.calls) == 1
        images.release.set()
        await asyncio.sleep(0.02)
        assert len(s.model.calls) == 1
        assert '图片打不开也可以接话' in repr(s.model.calls[0])
        assert not any(part.model_image_url for message in s.model.calls[0] if not isinstance(message.content, str) for part in message.content)
    asyncio.run(run())


def test_pure_unavailable_image_records_without_guess_or_model_call(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite', image_wait_seconds=0.01)
        s.adapter._images = SlowImages(fail=True)
        s.adapter._images.release.set()
        await s.adapter.handle_event(event('pic', '', image=True))
        assert s.model.calls == [] and s.sender.calls == []
    asyncio.run(run())
