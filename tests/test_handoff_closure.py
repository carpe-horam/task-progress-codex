"""Handoff acceptance and cancellation closure preserve original results."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import test_request_queue as fixtures

server, MAIN, SUB = fixtures.server, fixtures.MAIN, fixtures.SUB


class HandoffClosureTests(unittest.TestCase):
    setUp = fixtures.RequestQueueTests.setUp
    tearDown = fixtures.RequestQueueTests.tearDown
    p = fixtures.RequestQueueTests.p
    raw = fixtures.RequestQueueTests.raw
    route = fixtures.RequestQueueTests.route
    item = fixtures.RequestQueueTests.item
    node = fixtures.RequestQueueTests.node
    start = fixtures.RequestQueueTests.start
    finish = fixtures.RequestQueueTests.finish
    finish_a = fixtures.RequestQueueTests.finish_a
    assert_rejected = fixtures.RequestQueueTests.assert_rejected

    def source(self, rid='P', finish_root=True):
        self.route(rid, mode='parallel')
        iid = self.item(rid)['task_instance_id']
        server.decompose_task(self.sid, 'root', [
            {'id':'diagnose', 'title':'Diagnosis', 'assignment':SUB},
            {'id':'integrate', 'title':'Transferred integration', 'assignment':SUB},
        ], task_instance_id=iid)
        aid = self.start(iid, 'diagnose', SUB)
        server.bind_task_agent(self.sid, 'diagnose', aid, 'diagnostic-agent', task_instance_id=iid)
        self.finish(iid, 'diagnose', aid)
        server.review_task(self.sid, 'diagnose', aid, 'accept', 'Diagnosis checked', ['fixture://diagnosis'], MAIN, task_instance_id=iid)
        server.update_task(self.sid, 'integrate', 'cancelled', summary='Moved before dispatch', task_instance_id=iid)
        if finish_root:
            server.update_task(self.sid, 'root', 'finished', outcome='open', summary='Diagnosis done; integration transferred',
                               assignment=MAIN, evidence=['fixture://source'], task_instance_id=iid)
        return iid

    def mapping(self, source, target=None, node='same', attempt=None, epoch=0):
        target = target or self.a
        return {'source_task_id':'integrate', 'source_attempt_id':self.node(source, 'integrate')['attempt_id'],
                'target_task_instance_id':target, 'target_epoch':epoch, 'target_task_id':node,
                'target_attempt_id':attempt or self.node(target, node)['attempt_id']}

    def register(self, source, mapping, hid='handoff-one', **overrides):
        args = dict(session_id=self.sid, task_instance_id=self.a, request_id='P', handoff_id=hid,
                    source_task_instance_id=source, mappings=[mapping], summary='Integration explicitly transferred',
                    evidence=['fixture://transfer'], reviewer=MAIN, **self.p(source)['closure_review_binding'])
        args.update(overrides)
        return server.handoff_request(**args)

    def closure(self, iid, decision='accept', rid='closure-one', **overrides):
        args = dict(session_id=self.sid, task_instance_id=iid, task_id='root', review_id=rid,
                    decision=decision, summary='Cancelled work reviewed; ' + decision,
                    evidence=['fixture://closure-review'], reviewer=MAIN, **self.p(iid)['closure_review_binding'])
        args.update(overrides)
        return server.review_task_closure(**args)

    def test_pending_submission_review_triggers_handoff_archive_without_rewriting_nodes(self):
        source = self.source()
        original = deepcopy(self.raw()['session_work_state']['instances'][source]['nodes'])
        self.route('T', mode='parallel')
        target = self.item('T')['task_instance_id']
        server.decompose_task(self.sid, 'root', [{'id':'target', 'title':'Actual recovery', 'assignment':SUB}], task_instance_id=target)
        attempt = self.start(target, 'target', SUB)
        mapping = self.mapping(source, target, 'target', attempt)
        binding = self.p(source)['closure_review_binding']
        self.register(source, mapping)
        server.bind_task_agent(self.sid, 'target', attempt, 'recovery-agent', task_instance_id=target)
        self.finish(target, 'target', attempt)
        self.assertIn(source, [p['task_instance_id'] for p in self.p()['parallel_tasks']])
        server.review_task(self.sid, 'target', attempt, 'accept', 'Recovery independently accepted', ['fixture://recovery-accepted'], MAIN, task_instance_id=target)
        self.assertNotIn(source, [p['task_instance_id'] for p in self.p()['parallel_tasks']])
        archived = self.p(history=True)['task_history'][0]
        self.assertEqual(archived['status'], 'handed_off')
        self.assertEqual(archived['nodes'][0]['outcome'], 'open')
        self.assertEqual(original, self.raw()['session_work_state']['instances'][source]['nodes'])
        proof = archived['handoff_closure']['accepted_targets'][0]['accepted_result']
        self.assertEqual(proof['review']['evidence'], ['fixture://recovery-accepted'])
        self.assertEqual(len(self.p(source)['handoff_closures']), 1)
        self.register(source, mapping)
        self.assertEqual(self.p(history=True)['task_history'][0], archived)
        self.start(source, 'integrate', SUB, retry=True)
        self.assertIn(source, [p['task_instance_id'] for p in self.p()['parallel_tasks']])
        self.assertEqual(self.p(history=True)['task_history'][0], archived)
        self.register(source, mapping, **binding)  # Replay original bytes, not a new source binding.
        self.assertIsNone(self.p(source)['handoff_closure'])

    def test_historical_archived_epoch_target_and_null_cancelled_attempt(self):
        source = self.source()
        attempt = self.start(self.a, 'same')
        self.finish(self.a, 'same', attempt)
        old = deepcopy(self.raw()['nodes']['same'])
        server.rebuild_task_network(self.sid, 'New acceptance epoch', task_instance_id=self.a)
        root = self.p()['root_id']
        server.decompose_task(self.sid, root, [
            {'id':'new-work','title':'New work','assignment':MAIN},
            {'id':'new-check','title':'New check','assignment':MAIN},
        ], task_instance_id=self.a)
        for nid in ('new-work','new-check'):
            aid = self.start(self.a, nid)
            self.finish(self.a, nid, aid)
        self.finish(self.a, root)
        self.route('Q')
        previous = self.a
        server.activate_request(self.sid, previous, 'Q')
        self.a = self.p()['task_instance_id']
        archived_before = deepcopy(self.p(history=True)['task_history'])
        mapping = self.mapping(source, previous, attempt=attempt)
        self.assertIsNone(mapping['source_attempt_id'])
        self.register(source, mapping)
        self.assertEqual(self.p(history=True)['task_history'][:-1], archived_before)
        accepted = self.p(source)['handoff_closure']['accepted_targets'][0]['accepted_result']
        self.assertEqual(accepted['attempt_id'], old['attempt_id'])
        self.assertEqual(accepted['evidence'], old['evidence'])

    def test_incomplete_wrong_epoch_attempt_self_cycle_and_stale_source_rejected(self):
        source = self.source()
        attempt = self.start(self.a, 'same')
        mapping = self.mapping(source, attempt=attempt)
        for change in ({'target_epoch':99}, {'target_attempt_id':'obsolete'}, {'target_task_instance_id':'other-session'},
                       {'target_task_instance_id':source, 'target_task_id':'diagnose', 'target_attempt_id':self.node(source,'diagnose')['attempt_id']},
                       {'source_attempt_id':'never-ran'}, {'source_task_id':'diagnose'}):
            self.assert_rejected(lambda change=change: self.register(source, {**mapping, **change}))
        self.assert_rejected(lambda: self.register(source, mapping, source_fingerprint='old-result'))
        self.register(source, mapping)
        self.assert_rejected(lambda: self.register(source, mapping, summary='Conflicting replay'))
        self.finish(self.a, 'same', attempt)
        frozen = deepcopy(self.p(history=True)['task_history'])
        self.start(self.a, 'same', retry=True)
        self.assertEqual(self.p(history=True)['task_history'], frozen)

    def test_rejected_target_and_retried_old_target_cannot_close_source(self):
        source = self.source()
        self.route('T', mode='parallel')
        target = self.item('T')['task_instance_id']
        server.decompose_task(self.sid, 'root', [{'id':'target','title':'Target','assignment':SUB}], task_instance_id=target)
        aid = self.start(target, 'target', SUB)
        mapping = self.mapping(source,target,'target',aid)
        self.register(source,mapping)
        server.bind_task_agent(self.sid,'target',aid,'target-agent',task_instance_id=target)
        self.finish(target,'target',aid)
        server.review_task(self.sid,'target',aid,'reject','Still broken',['fixture://rejected'],MAIN,task_instance_id=target)
        self.assertIsNone(self.p(source)['handoff_closure'])
        new = self.start(target,'target',SUB,retry=True)
        server.bind_task_agent(self.sid,'target',new,'new-target-agent',task_instance_id=target)
        self.finish(target,'target',new)
        server.review_task(self.sid,'target',new,'accept','Now works',['fixture://accepted'],MAIN,task_instance_id=target)
        self.assertIsNone(self.p(source)['handoff_closure'])
        self.assert_rejected(lambda:self.register(source,mapping,hid='stale-target'))
        self.register(source,{**mapping,'target_attempt_id':new},hid='new-target')
        self.assertIsNotNone(self.p(source)['handoff_closure'])

    def test_two_resolved_one_cancelled_parent_waits_then_continue_or_accept(self):
        source = self.source(finish_root=False)
        # Add an independently resolved second leaf, retaining the cancelled leaf.
        server.decompose_task(self.sid,'root',[{'id':'second','title':'Second result','assignment':SUB}],task_instance_id=source)
        aid=self.start(source,'second',SUB)
        server.bind_task_agent(self.sid,'second',aid,'second-agent',task_instance_id=source)
        self.finish(source,'second',aid)
        server.review_task(self.sid,'second',aid,'accept','Second accepted',['fixture://second'],MAIN,task_instance_id=source)
        self.assertEqual(self.p(source)['status'],'waiting_synthesis')
        self.assertEqual(self.p(source)['resolved_leaf_count'],2)
        self.assertEqual(self.p(source)['total'],3)
        self.assertFalse(self.p()['session_work']['can_finish_session'])
        original=deepcopy(self.raw()['session_work_state']['instances'][source]['nodes'])
        self.closure(source,'continue')
        self.assertEqual(self.p(source)['status'],'open')
        self.assertTrue(any('继续' in action for action in self.p(source)['next_actions']))
        self.assertEqual(self.p(history=True)['task_history'],[])
        self.closure(source,'accept','final-closure')
        self.assertEqual(self.p(source)['status'],'closed')
        closed=deepcopy(self.raw()['session_work_state']['instances'][source]['nodes'])
        self.assertIsNotNone(closed['root']['started_at'])
        self.assertIsNotNone(closed['root']['finished_at'])
        self.assertEqual(closed['root']['time_boundary_sources']['finished_at'],'closure_fallback')
        for key in closed:
            for field in ('started_at','finished_at','time_boundary_sources'):
                if field in original[key]: closed[key][field]=original[key][field]
                else: closed[key].pop(field,None)
        self.assertEqual(closed,original)
        self.assertEqual(self.p(history=True)['task_history'][0]['resolved_leaf_count'],2)
        self.assertEqual(self.p(source)['closure_review']['decision'],'accept')
        self.start(source,'integrate',SUB,retry=True)
        self.assertEqual(self.node(source,'integrate')['execution_status'],'running')
        self.assertIsNone(self.p(source)['closure_review'])

    def test_existing_open_parent_append_review_and_stale_binding_rejected(self):
        source=self.source()
        original=deepcopy(self.raw()['session_work_state']['instances'][source]['nodes'])
        binding=self.p(source)['closure_review_binding']
        self.assert_rejected(lambda:self.closure(source,source_fingerprint='stale'))
        self.closure(source)
        archives=deepcopy(self.p(history=True)['task_history'])
        self.closure(source)
        self.assertEqual(self.p(history=True)['task_history'],archives)
        self.assertEqual(self.raw()['session_work_state']['instances'][source]['nodes'],original)
        self.start(source,'integrate',SUB,retry=True)
        server.update_task(self.sid,'integrate','cancelled',attempt_id=self.node(source,'integrate')['attempt_id'],summary='Cancelled again',task_instance_id=source)
        server.update_task(self.sid,'root','finished',outcome='open',summary='New unresolved result',assignment=MAIN,task_instance_id=source)
        self.assert_rejected(lambda:self.closure(source,rid='late-decision',**binding))
        self.assertEqual(self.p(history=True)['task_history'],archives)

    def test_wire_small_receipt_and_selective_audit_queries(self):
        source=self.source()
        self.closure(source)
        p=server.get_progress(self.sid,task_instance_id=source)['structuredContent']['progress']
        self.assertNotIn('closure_reviews',p)
        history=server.get_progress(self.sid,task_instance_id=source,view='history',history_kind='closure_reviews',limit=1)['structuredContent']['progress']
        self.assertEqual(history['pagination']['total'],1)
        args=dict(session_id=self.sid,task_instance_id=source,task_id='root',review_id='closure-one',decision='accept',
                  summary='Cancelled work reviewed; accept',evidence=['fixture://closure-review'],reviewer=MAIN,**self.p(source)['closure_review_binding'])
        result=server._wire_result('review_task_closure',args,server.review_task_closure(**args))['structuredContent']
        self.assertEqual(result['review_id'],'closure-one')
        self.assertNotIn('progress',result)
        self.assertNotIn('evidence',result)

    def test_uncovered_leaf_running_pending_review_and_dependency_do_not_bypass(self):
        source=self.source(finish_root=False)
        server.decompose_task(self.sid,'root',[{'id':'uncovered','title':'Extra unfinished obligation','assignment':SUB}],task_instance_id=source)
        aid=self.start(self.a,'same')
        mapping=self.mapping(source,attempt=aid)
        self.assert_rejected(lambda:self.register(source,mapping))
        self.assert_rejected(lambda:self.closure(source))
        pending=self.start(source,'uncovered',SUB)
        server.bind_task_agent(self.sid,'uncovered',pending,'pending-agent',task_instance_id=source)
        self.finish(source,'uncovered',pending)
        self.assert_rejected(lambda:self.register(source,mapping))
        self.assert_rejected(lambda:self.closure(source))
        server.review_task(self.sid,'uncovered',pending,'reject','Not accepted',['fixture://reject'],MAIN,task_instance_id=source)
        server.update_task(self.sid,'root','finished',outcome='open',summary='Two obligations remain',assignment=MAIN,task_instance_id=source)
        self.assert_rejected(lambda:self.register(source,mapping))

        # A closure acceptance cannot manufacture a resolved dependency result.
        server.decompose_task(self.sid,'root',[{'id':'group','title':'Group','assignment':MAIN},
            {'id':'consumer','title':'Requires success','assignment':MAIN,'depends_on':['group']}],task_instance_id=self.a)
        server.decompose_task(self.sid,'group',[{'id':'cancel-child','title':'Cancelled input','assignment':MAIN}],task_instance_id=self.a)
        server.update_task(self.sid,'cancel-child','cancelled',summary='Withdrawn',task_instance_id=self.a)
        node=self.node(self.a,'group')
        server.review_task_closure(self.sid,self.a,'group','group-close',**node['closure_review_binding'],decision='accept',summary='Cancellation reviewed',evidence=['fixture://group'],reviewer=MAIN)
        self.assertFalse(self.node(self.a,'consumer')['ready'])
        self.assert_rejected(lambda:self.start(self.a,'consumer'))

    def test_handoff_cycles_and_scope_changes_are_rejected(self):
        source=self.source()
        target=self.source(rid='Q')
        proof=self.node(target,'diagnose')['attempt_id']
        self.register(source,self.mapping(source,target,'diagnose',proof))
        args=dict(session_id=self.sid,task_instance_id=self.a,request_id='Q',handoff_id='cycle',source_task_instance_id=target,
                  mappings=[self.mapping(target,source,'diagnose',self.node(source,'diagnose')['attempt_id'])],summary='Circular handoff',
                  evidence=['fixture://cycle'],reviewer=MAIN,**self.p(target)['closure_review_binding'])
        self.assert_rejected(lambda:server.handoff_request(**args))
        binding=self.p(target)['closure_review_binding']
        server.update_task_context(self.sid,target,'Changed acceptance scope',[],['New delivery required'],'Resume changed scope','User scope changed')
        self.assert_rejected(lambda:self.closure(target,**binding))

    def test_all_resolved_pending_parent_keeps_success_synthesis_guidance(self):
        for nid in ('same','accept'):
            aid=self.start(self.a,nid)
            self.finish(self.a,nid,aid)
        pending=self.p()
        self.assertEqual(pending['status'],'waiting_synthesis')
        self.assertFalse(pending['closure_review_due'])
        self.assertTrue(any('综合验收 root' in action and 'outcome=resolved' in action for action in pending['next_actions']))
        self.assertFalse(any('review_task_closure' in action for action in pending['next_actions']))
        # An explicit open synthesis still needs a separate closure decision,
        # even if the individual leaf results were successful.
        server.update_task(self.sid,'root','finished',outcome='open',summary='Goal still open despite leaf results',assignment=MAIN,task_instance_id=self.a)
        self.assertTrue(self.p()['closure_review_due'])
        self.assertTrue(any('review_task_closure' in action for action in self.p()['next_actions']))
        self.assertEqual(self.node(self.a,'root')['outcome'],'open')

    def test_handoff_replay_across_seconds_preserves_first_metadata_and_retry(self):
        source=self.source()
        aid=self.start(self.a,'same')
        self.finish(self.a,'same',aid)
        mapping=self.mapping(source,attempt=aid)
        binding=self.p(source)['closure_review_binding']
        reviewer={**MAIN,'model':'reviewer-fixture','reasoning_effort':'high','codex_thread_id':'declared-thread'}
        with patch.object(server,'_now',return_value='2026-10-29T01:00:00Z'):
            self.register(source,mapping,reviewer=reviewer,**binding)
        first=deepcopy(self.raw()['session_work_state']['instances'][source]['handoffs'][0])
        self.assertEqual(first['reviewer']['metadata_provenance']['captured_at'],'2026-10-29T01:00:00Z')
        archived=deepcopy(self.p(history=True)['task_history'])
        with patch.object(server,'_now',return_value='2026-10-29T01:00:17Z'):
            before=server._session_file(self.sid).read_bytes()
            self.register(source,mapping,reviewer=reviewer,**binding)
            self.assertEqual(server._session_file(self.sid).read_bytes(),before)
            for changed in ({'model':'another-model'},{'reasoning_effort':'low'},{'codex_thread_id':'another-thread'},
                            {'self_execution_reason':'Different reviewer configuration'},{'agent_name':'another-reviewer'}):
                self.assert_rejected(lambda changed=changed:self.register(source,mapping,reviewer={**reviewer,**changed},**binding))
        self.start(source,'integrate',SUB,retry=True)
        with patch.object(server,'_now',return_value='2026-10-29T02:00:00Z'):
            before=server._session_file(self.sid).read_bytes()
            self.register(source,mapping,reviewer=reviewer,**binding)
            self.assertEqual(server._session_file(self.sid).read_bytes(),before)
        self.assertIsNone(self.p(source)['handoff_closure'])
        self.assertEqual(self.raw()['session_work_state']['instances'][source]['handoffs'][0],first)
        self.assertEqual(self.p(history=True)['task_history'],archived)

    def test_closure_replay_across_seconds_preserves_first_metadata_and_retry(self):
        source=self.source()
        binding=self.p(source)['closure_review_binding']
        reviewer={**MAIN,'model':'reviewer-fixture','reasoning_effort':'high','codex_turn_id':'declared-turn'}
        with patch.object(server,'_now',return_value='2026-10-29T03:00:00Z'):
            self.closure(source,reviewer=reviewer,**binding)
        first=deepcopy(self.raw()['session_work_state']['instances'][source]['closure_reviews'][0])
        self.assertEqual(first['reviewer']['metadata_provenance']['captured_at'],'2026-10-29T03:00:00Z')
        archived=deepcopy(self.p(history=True)['task_history'])
        with patch.object(server,'_now',return_value='2026-10-29T03:00:23Z'):
            before=server._session_file(self.sid).read_bytes()
            self.closure(source,reviewer=reviewer,**binding)
            self.assertEqual(server._session_file(self.sid).read_bytes(),before)
            for changed in ({'model':'another-model'},{'reasoning_effort':'low'},{'codex_turn_id':'another-turn'},
                            {'self_execution_reason':'Different reviewer configuration'},{'agent_name':'another-reviewer'}):
                self.assert_rejected(lambda changed=changed:self.closure(source,reviewer={**reviewer,**changed},**binding))
        self.start(source,'integrate',SUB,retry=True)
        with patch.object(server,'_now',return_value='2026-10-29T04:00:00Z'):
            before=server._session_file(self.sid).read_bytes()
            self.closure(source,reviewer=reviewer,**binding)
            self.assertEqual(server._session_file(self.sid).read_bytes(),before)
        self.assertIsNone(self.p(source)['closure_review'])
        self.assertEqual(self.raw()['session_work_state']['instances'][source]['closure_reviews'][0],first)
        self.assertEqual(self.p(history=True)['task_history'],archived)


if __name__=='__main__':
    unittest.main()
