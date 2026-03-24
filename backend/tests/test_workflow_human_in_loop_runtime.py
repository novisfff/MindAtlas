from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports
from tests._db import make_session


bootstrap_backend_imports()


class WorkflowHumanInLoopRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _create_pending_approval(
        self,
        *,
        field_schema: list[dict] | None = None,
        initial_values: dict | None = None,
        require_reject_comment: bool = True,
    ):
        from app.assistant_config.models import AssistantHumanApproval

        row = AssistantHumanApproval(
            run_id='run_hitl_1',
            channel_type='workflow_test',
            node_id='hitl_1',
            node_label='Confirm',
            status='pending',
            request_payload={
                'instruction': 'confirm create',
                'requireRejectComment': require_reject_comment,
            },
            field_schema=field_schema
            or [
                {'name': 'title', 'type': 'string', 'required': True},
                {'name': 'priority', 'type': 'integer', 'required': False},
            ],
            initial_values=initial_values or {'title': 'draft', 'priority': 1},
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def test_submit_human_approval_decision_updates_row(self) -> None:
        from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision

        row = self._create_pending_approval()
        payload = submit_human_approval_decision(
            self.db,
            approval_id=row.id,
            decision='approved',
            values={'title': 'final title', 'priority': '2'},
            comment='looks good',
            expected_run_id='run_hitl_1',
        )

        self.assertEqual(payload['status'], 'approved')
        self.assertEqual(payload['decision'], 'approved')
        self.assertEqual(payload['submittedValues']['title'], 'final title')
        self.assertEqual(payload['submittedValues']['priority'], 2)
        self.assertEqual(payload['comment'], 'looks good')
        self.assertFalse(payload.get('runtimeWaiting', True))

    def test_reject_requires_comment_when_configured(self) -> None:
        from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision

        row = self._create_pending_approval()

        with self.assertRaises(ValueError) as ctx:
            submit_human_approval_decision(
                self.db,
                approval_id=row.id,
                decision='rejected',
                values={'title': 'reject me'},
                comment='',
                expected_run_id='run_hitl_1',
            )

        self.assertIn('comment is required', str(ctx.exception))

    def test_list_pending_approvals_for_conversation(self) -> None:
        from app.assistant.models import Conversation
        from app.assistant_config.models import AssistantHumanApproval
        from app.assistant.workflow.human_approval_runtime import list_pending_approvals_for_conversation

        conversation = Conversation(title='hitl')
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        row = AssistantHumanApproval(
            run_id='run_hitl_conv',
            channel_type='assistant_chat',
            conversation_id=conversation.id,
            node_id='hitl_2',
            node_label='Confirm',
            status='pending',
            request_payload={'instruction': 'confirm message'},
            field_schema=[{'name': 'content', 'type': 'string', 'required': True}],
            initial_values={'content': 'draft'},
        )
        self.db.add(row)
        self.db.commit()

        pending = list_pending_approvals_for_conversation(self.db, conversation.id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['id'], str(row.id))
        self.assertEqual(pending[0]['status'], 'pending')

    def test_submit_select_rejects_unknown_option(self) -> None:
        from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision

        row = self._create_pending_approval(
            field_schema=[
                {'name': 'priority', 'type': 'integer', 'widget': 'select', 'options': ['1', '2'], 'required': True},
            ],
            initial_values={'priority': 1},
        )
        with self.assertRaises(ValueError) as ctx:
            submit_human_approval_decision(
                self.db,
                approval_id=row.id,
                decision='approved',
                values={'priority': 3},
                comment='',
                expected_run_id='run_hitl_1',
            )
        self.assertIn('configured options', str(ctx.exception))

    def test_submit_tag_selector_allows_custom_when_enabled(self) -> None:
        from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision

        row = self._create_pending_approval(
            field_schema=[
                {
                    'name': 'tags',
                    'type': 'array',
                    'widget': 'tag_selector',
                    'options': ['work', 'life'],
                    'allowCustom': True,
                    'required': False,
                },
            ],
            initial_values={'tags': ['work']},
        )
        payload = submit_human_approval_decision(
            self.db,
            approval_id=row.id,
            decision='approved',
            values={'tags': ['work', 'custom-tag']},
            comment='',
            expected_run_id='run_hitl_1',
        )
        self.assertEqual(payload['submittedValues']['tags'], ['work', 'custom-tag'])

    def test_submit_date_and_time_format_validation(self) -> None:
        from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision

        row = self._create_pending_approval(
            field_schema=[
                {'name': 'record_date', 'type': 'string', 'widget': 'date', 'required': True},
                {'name': 'record_time', 'type': 'string', 'widget': 'time', 'required': True},
            ],
            initial_values={'record_date': '2026-02-24', 'record_time': '23:59'},
        )
        with self.assertRaises(ValueError) as date_ctx:
            submit_human_approval_decision(
                self.db,
                approval_id=row.id,
                decision='approved',
                values={'record_date': '2026/02/24', 'record_time': '23:59'},
                comment='',
                expected_run_id='run_hitl_1',
            )
        self.assertIn('YYYY-MM-DD', str(date_ctx.exception))

        row2 = self._create_pending_approval(
            field_schema=[
                {'name': 'record_date', 'type': 'string', 'widget': 'date', 'required': True},
                {'name': 'record_time', 'type': 'string', 'widget': 'time', 'required': True},
            ],
            initial_values={'record_date': '2026-02-24', 'record_time': '23:59'},
        )
        with self.assertRaises(ValueError) as time_ctx:
            submit_human_approval_decision(
                self.db,
                approval_id=row2.id,
                decision='approved',
                values={'record_date': '2026-02-24', 'record_time': '24:01'},
                comment='',
                expected_run_id='run_hitl_1',
            )
        self.assertIn('HH:mm', str(time_ctx.exception))

    def test_submit_optional_date_time_can_be_cleared(self) -> None:
        from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision

        row = self._create_pending_approval(
            field_schema=[
                {'name': 'record_date', 'type': 'string', 'widget': 'date', 'required': False},
                {'name': 'record_time', 'type': 'string', 'widget': 'time', 'required': False},
            ],
            initial_values={'record_date': '2026-02-24', 'record_time': '23:59'},
        )

        payload = submit_human_approval_decision(
            self.db,
            approval_id=row.id,
            decision='approved',
            values={'record_date': '', 'record_time': ''},
            comment='',
            expected_run_id='run_hitl_1',
        )

        self.assertEqual(payload['submittedValues']['record_date'], '')
        self.assertEqual(payload['submittedValues']['record_time'], '')

    def test_cancel_pending_human_approvals_for_run_marks_cancelled(self) -> None:
        from app.assistant.workflow.human_approval_runtime import cancel_pending_human_approvals_for_run

        row = self._create_pending_approval()
        payloads = cancel_pending_human_approvals_for_run(self.db, run_id='run_hitl_1')
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]['id'], str(row.id))
        self.assertEqual(payloads[0]['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
