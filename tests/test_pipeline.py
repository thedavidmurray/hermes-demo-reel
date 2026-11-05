"""Orchestration fault injection with synthetic source/LLM/media boundaries.
These tests do not attest real providers or chat transport.
"""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pipeline
import store
import test_interview as fixtures
from schemas import ApprovalRequired, ContractError, MediaPolicy


class PipelineTests(unittest.TestCase):
    def setUp(self):
        helper = fixtures.Interviews()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.helper = helper
        self.owner = fixtures.OWNER
        boarded, _ = helper.boarded()
        self.job = helper.approve(boarded)
        clock = patch.object(pipeline, 'datetime', wraps=datetime)
        clock_mock = clock.start()
        self.addCleanup(clock.stop)
        clock_mock.now.return_value = fixtures.NOW

    def test_expired_approval_after_compose_never_becomes_ready(self):
        from contextlib import ExitStack
        from datetime import timedelta
        from dataclasses import dataclass
        @dataclass
        class FrozenFixture:
            render_snapshot_id: str = 'fixture'
        with ExitStack() as stack:
            for name in ('resolve_media_tools', 'preflight_terminal', 'make_title',
                         'plan_art', 'validate_delivery'):
                stack.enter_context(patch.object(pipeline, name))
            stack.enter_context(patch.object(pipeline, 'job_directory', return_value=self.helper.root))
            stack.enter_context(patch.object(pipeline, 'render_art_sequence', return_value=()))
            stack.enter_context(patch.object(pipeline, 'freeze_render', return_value=FrozenFixture()))
            clock = stack.enter_context(patch.object(pipeline, 'datetime', wraps=datetime))
            clock.now.side_effect = [fixtures.NOW,
                                     datetime.fromisoformat(self.job.expires_at) + timedelta(seconds=1)]
            stack.enter_context(patch.object(pipeline, 'compose', return_value={'fixture': True}))
            with self.assertRaisesRegex(ApprovalRequired, 'APPROVAL_EXPIRED'):
                pipeline.render_job(self.helper.ctx, self.owner, self.job.job_id,
                                    policy=MediaPolicy(network_allowed=True))
        current = store.load_job(self.owner, self.job.job_id)
        self.assertEqual((current.state, current.error_code), ('RENDERING', 'RENDER_UNKNOWN'))
        self.assertFalse((self.helper.root / 'bundle.json').exists())

    def test_wrong_owner_cannot_render_or_create_lease(self):
        with self.assertRaises(ContractError):
            pipeline.render_job(self.helper.ctx, fixtures.OTHER, self.job.job_id,
                                policy=MediaPolicy(network_allowed=True))
        conn = store.open_store()
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM render_leases').fetchone()[0], 0)
        finally:
            conn.close()

    def test_unapproved_job_does_not_dispatch(self):
        with patch.object(pipeline, 'resolve_media_tools') as resolve:
            with self.assertRaises(ApprovalRequired):
                pipeline.render_job(self.helper.ctx, self.owner, self.helper.ready().job_id,
                                    policy=MediaPolicy(network_allowed=True))
            resolve.assert_not_called()

    def test_lost_paid_result_cannot_repeat(self):
        with patch.object(pipeline, 'resolve_media_tools'), \
             patch.object(pipeline, 'job_directory', return_value=self.helper.root), \
             patch.object(pipeline, 'preflight_terminal'), \
             patch.object(pipeline, 'make_title', side_effect=RuntimeError('lost provider ACK')) as title:
            with self.assertRaises(RuntimeError):
                pipeline.render_job(self.helper.ctx, self.owner, self.job.job_id,
                                    policy=MediaPolicy(network_allowed=True))
            current = store.load_job(self.owner, self.job.job_id)
            self.assertEqual((current.state, current.error_code), ('RENDERING', 'RENDER_UNKNOWN'))
            with self.assertRaises(ApprovalRequired):
                pipeline.render_job(self.helper.ctx, self.owner, self.job.job_id,
                                    policy=MediaPolicy(network_allowed=True))
            self.assertEqual(title.call_count, 1)

    def test_delivery_needs_ready_job(self):
        ctx = SimpleNamespace(send_bundle=lambda *_: self.fail('must not send'))
        with self.assertRaisesRegex(ContractError, 'READY_RENDER_REQUIRED'):
            pipeline.deliver_job(ctx, self.owner, self.job.job_id)

    def ready_for_delivery(self):
        rendering = store.save_job(replace(self.job, state='RENDERING'),
                                   expected_job_version=self.job.job_version)
        ready = store.save_job(replace(rendering, state='READY', render_snapshot_id='render'),
                              expected_job_version=rendering.job_version)
        (self.helper.root / 'bundle.json').write_text('{}')
        assert ready.board is not None
        bundle = SimpleNamespace(render_snapshot_id='render', board_digest=ready.board.digest,
                                 mp4=SimpleNamespace(sha256='a' * 64))
        return ready, bundle

    def test_lost_delivery_ack_cannot_resend(self):
        from schemas import Conflict
        ready, bundle = self.ready_for_delivery()
        ctx = SimpleNamespace(send_bundle=Mock(side_effect=RuntimeError('lost ACK')))
        original_decode = store.decode
        with patch.object(pipeline, 'job_directory', return_value=self.helper.root), \
             patch.object(pipeline.store, 'decode', side_effect=lambda cls, payload:
                          bundle if cls is pipeline.RenderBundle else original_decode(cls, payload)), \
             patch.object(pipeline, 'validate_delivery'):
            with self.assertRaises(RuntimeError):
                pipeline.deliver_job(ctx, self.owner, ready.job_id)
            with self.assertRaises(Conflict):
                pipeline.deliver_job(ctx, self.owner, ready.job_id)
            self.assertEqual(ctx.send_bundle.call_count, 1)

    def test_delivery_receipt_persists_and_prevents_duplicate_send(self):
        ready, bundle = self.ready_for_delivery()
        ctx = SimpleNamespace(send_bundle=Mock(return_value='platform-message'))
        original_decode = store.decode
        with patch.object(pipeline, 'job_directory', return_value=self.helper.root), \
             patch.object(pipeline.store, 'decode', side_effect=lambda cls, payload:
                          bundle if cls is pipeline.RenderBundle else original_decode(cls, payload)), \
             patch.object(pipeline, 'validate_delivery'):
            first = pipeline.deliver_job(ctx, self.owner, ready.job_id)
            second = pipeline.deliver_job(ctx, self.owner, ready.job_id)
            self.assertEqual(first, second)
            self.assertEqual(first.platform_message_id, 'platform-message')
            self.assertEqual(ctx.send_bundle.call_count, 1)
        self.assertEqual(store.load_job(self.owner, ready.job_id).state, 'DELIVERED')

    def test_capability_preflight_failure_does_not_claim_paid_attempt(self):
        with patch.object(pipeline, 'resolve_media_tools', side_effect=ContractError('missing')):
            with self.assertRaises(ContractError):
                pipeline.render_job(self.helper.ctx, self.owner, self.job.job_id,
                                    policy=MediaPolicy(network_allowed=True))
        self.assertEqual(store.load_job(self.owner, self.job.job_id).state, 'APPROVED')
        conn = store.open_store()
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM render_leases').fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
