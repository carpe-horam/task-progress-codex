/* Verify displayed results and timeline event ownership against the isolated backend ledger. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');
const out=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const hash=v=>createHash('sha256').update(v).digest('hex');
const main={execution_mode:'main_session',self_execution_reason:'主会话核对文件与提交结论。'};
const worker={execution_mode:'subagent',model:'fixture',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'核对来源记录与交付文件。'};
const pics=[];
async function prepare(host) {
  const r=await host.rawRpc('init_task_network',{task_title:'验收交付文件',tasks:[{id:'work',title:'核对交付清单',assignment:worker},{id:'ready',title:'待整理附录',assignment:main}]});
  const binding={session_id:r.structuredContent.session_id,task_instance_id:r.structuredContent.task_instance_id};
  const updates=(key,args)=>host.rawRpc(key,{...binding,task_id:'work',...args});
  for(let round=1;round<=2;round++){
    const start=await updates('update_task',{execution_status:'running',retry:round>1,assignment:worker});
    const attempt_id=start.structuredContent.attempt_id;
    await updates('bind_task_agent',{attempt_id,agent_name:'internal-binding-fixture-worker'});
    await updates('update_task',{attempt_id,execution_status:'finished',outcome:'resolved',summary:round===1?'已整理首版文件。':'已补齐归档签名。',evidence:[round===1?'交付清单初稿':'签名检查记录','https://example.org/evidence/'+round]});
    await updates('review_task',{attempt_id,decision:round===1?'reject':'accept',summary:round===1?'归档签名缺失。':'签名与原件一致。',evidence:[round===1?'缺件批注':'签名核对笔记'],reviewer:main});
  }
  return binding;
}
// These snapshots intentionally contain historical or inconsistent fields. Freeze the
// host at the supplied payload so background RPCs cannot replace it with live data.
async function snapshotPage(host) {
  const page=await host.page();
  await page.evaluate(()=>{
    const raw=window.hostRpc;
    window.hostRpc=(name,args)=>['get_progress_view','register_ui_view','check_ui_view'].includes(name) ? new Promise(()=>{}) : raw(name,args);
  });
  return page;
}
async function mainTest(){
  const host=await createHost();
  try {
    const binding=await prepare(host);
    const view=include_history=>host.rawRpc('get_progress_view',{...binding,include_history});
    const full=await view(true), compact=await view(false);
    const item=full.structuredContent.progress.nodes.find(x=>x.id==='work');
    assert.equal(item.attempts.length,2);
    const page=await host.page();await page.setViewportSize({width:390,height:780});
    await page.addStyleTag({content:'html,body{margin:0}iframe{width:100vw!important;height:100vh!important;border:none!important}'});
    await page.evaluate(()=>{
      const raw=window.hostRpc;window.readRecords={requestCount:0,fail:true};
      window.hostRpc=async(name,args)=>{
        if(name==='get_progress_view' && args.include_history){
          window.readRecords.requestCount++;
          if(window.readRecords.fail){await new Promise(done=>window.readRecords.release=done);throw new Error('测试历史记录已延迟');}
        }
        return raw(name,args);
      };
    });
    const frame=await host.mount(page,'tasks',compact,'fullscreen');
    const open=id=>frame.locator('#step-list [data-step-id="'+id+'"] .plan-title').click();
    const result=frame.locator('#detail-panel-result'),timeline=frame.locator('#detail-panel-records');
    await open('work');await result.waitFor();
    assert.match(await result.innerText(),/已补齐归档签名。/);assert.match(await result.innerText(),/签名与原件一致。/);
    assert.doesNotMatch(await result.innerText(),/缺件批注|归档签名缺失/);
    assert.equal(await result.getByText('签名检查记录',{exact:true}).count(),1);
    assert.equal(await result.locator('a').count(),1);
    assert.equal(await result.locator('a').getAttribute('href'),'https://example.org/evidence/2');
    assert.equal(await page.evaluate(()=>window.readRecords.requestCount),0);
    await frame.locator('#detail-tab-records').click();
    await timeline.getByText(/正在读取/).waitFor();
    await page.waitForFunction(()=>!!readRecords.release);
    await page.evaluate(()=>readRecords.release());
    await timeline.locator('[data-retry-detail-records]').waitFor();
    assert.equal(await page.evaluate(()=>readRecords.requestCount),1);
    await page.evaluate(()=>readRecords.fail=false);
    await timeline.locator('[data-retry-detail-records]').click();
    await timeline.locator('.detail-event[data-kind="accepted"]').waitFor();
    assert.equal(await page.evaluate(()=>readRecords.requestCount),2);
    for(const [index,attempt] of item.attempts.entries()){
      const events=timeline.locator('.detail-event[data-attempt-id="'+attempt.attempt_id+'"]');
      const actual=await events.evaluateAll(items=>items.map(e=>({kind:e.dataset.kind,time:e.querySelector('time')?.dateTime,round:e.querySelector('.detail-round-label')?.textContent})));
      assert.deepEqual(actual.map(e=>e.kind),['started','submitted',index?'accepted':'rejected']);
      assert.deepEqual(actual.map(e=>e.time),[attempt.started_at,attempt.submission.submitted_at,attempt.review.reviewed_at]);
      assert(actual.every(e=>e.round==='第 '+(index+1)+' 次'));
      if(index===0) {
        assert((await events.nth(1).innerText()).includes(attempt.submission.summary),'previous attempt remains readable');
        assert((await events.nth(2).innerText()).includes(attempt.review.summary));
        assert.equal(await events.nth(1).locator('a').getAttribute('href'),attempt.submission.evidence[1]);
      } else {
        assert(!(await events.nth(1).innerText()).includes(attempt.submission.summary),'current result stays in result tab');
        assert(!(await events.nth(2).innerText()).includes(attempt.review.summary));
        assert.equal(await events.nth(1).locator('a').count(),0);
      }
      assert.equal(await events.nth(0).locator('.detail-event-actor').innerText(),'子智能体');
      assert.equal(await events.nth(2).locator('.detail-event-actor').innerText(),'主会话');
      assert.doesNotMatch(await timeline.innerText(),/internal-binding-fixture-worker/);
      assert.equal(await events.nth(2).locator('a').count(),0);
    }
    if(out){
      fs.mkdirSync(out,{recursive:true});
      for(const [width,height,theme] of [[320,640,'light'],[390,780,'dark']]){
        await page.setViewportSize({width,height});await page.emulateMedia({colorScheme:theme});
        for(const tab of ['result','records']){
          await frame.locator('#detail-tab-'+tab).click();
          const file=tab+'-'+width+'-'+theme+'.png';
          const bytes=await page.screenshot({path:path.join(out,file)});pics.push({file,sha256:hash(bytes)});
        }
      }
    }
    await frame.locator('#step-detail-back').click();await open('ready');
    assert.equal(await result.innerText(),'暂无结果记录。');
    await frame.locator('#detail-tab-records').click();
    await timeline.getByText('暂无执行记录。').waitFor();assert.equal(await timeline.locator('time').count(),0);
    await page.close();
    const old=await host.rawRpc('init_progress',{task_title:'线性任务历史',steps:['归档附件'],legacy_linear:true});
    const oldBinding={session_id:old.structuredContent.session_id,task_instance_id:old.structuredContent.task_instance_id};
    const start=await host.rawRpc('update_step',{...oldBinding,step_index:0,status:'in_progress',assignment:main});
    await host.rawRpc('update_step',{...oldBinding,step_index:0,attempt_id:start.structuredContent.attempt_id,status:'completed',note:'归档附件已核对。'});
    const legacy=await host.page();
    const oldFrame=await host.mount(legacy,'old',await host.rawRpc('get_progress_view',{...oldBinding,include_history:false}),'fullscreen');
    await oldFrame.locator('#steps > li > details > summary').first().click();
    assert.match(await oldFrame.locator('#detail-panel-result').innerText(),/归档附件已核对/);
    await oldFrame.locator('#detail-tab-records').click();
    await oldFrame.locator('.detail-event[data-kind="finished"]').waitFor();
    assert.equal(await oldFrame.locator('.detail-event[data-kind="accepted"]').count(),0);
    await legacy.close();
    // A historical payload may have no attempt ledger, or a corrupt receipt from a different attempt.
    const mixed=structuredClone(full), odd=mixed.structuredContent.progress.nodes.find(n=>n.id==='work');
    odd.review={...odd.review,attempt_id:'older-unrelated',summary:'其他执行的验收'};
    odd.attempts[1].review=odd.review;
    const anomalous=await snapshotPage(host),foreign=await host.mount(anomalous,'other',mixed,'fullscreen');
    await foreign.locator('#step-list [data-step-id="work"] .plan-title').click();
    assert.doesNotMatch(await foreign.locator('#detail-panel-result').innerText(),/其他执行的验收|验收通过/);
    await foreign.locator('#detail-tab-records').click();
    assert.doesNotMatch(await foreign.locator('#detail-panel-records').innerText(),/其他执行的验收/);
    await anomalous.close();
    for (const variant of ['identical','different-evidence','different-summary','accepted-open-legacy']) {
      const data=structuredClone(full), leaf=data.structuredContent.progress.nodes.find(n=>n.id==='work');
      const submission=leaf.submission, review=leaf.review;
      review.summary=variant==='different-summary'?'验收补充结论':submission.summary;
      review.evidence=variant==='different-evidence'?['验收独有证据']:submission.evidence.slice();
      leaf.attempts[leaf.attempts.length-1].review=review;
      if (variant==='accepted-open-legacy') { leaf.outcome='open'; leaf.submission=null; leaf.summary=review.summary; leaf.evidence=review.evidence.slice(); }
      const candidate=await snapshotPage(host), candidateFrame=await host.mount(candidate,'dedupe',data,'fullscreen');
      await candidateFrame.locator('#step-list [data-step-id="work"] .plan-title').click();
      const result=candidateFrame.locator('#detail-panel-result');
      assert.equal(await result.getByText(review.summary,{exact:true}).count(),1,'identical summaries occur once');
      if(variant==='different-evidence') assert.equal(await result.getByText('验收独有证据',{exact:true}).count(),1,'distinct review evidence remains visible');
      if(variant==='different-summary') assert.equal(await result.getByText(submission.summary,{exact:true}).count(),1,'distinct submission summary remains visible');
      if(variant==='accepted-open-legacy') assert.match(await result.innerText(),/报告已接受，任务未解决/,'legacy accepted open remains unresolved');
      if(variant!=='accepted-open-legacy') {
        await candidateFrame.locator('#detail-tab-records').click();
        const events=candidateFrame.locator('#detail-panel-records .detail-event[data-attempt-id="'+leaf.attempt_id+'"]');
        await events.first().waitFor();
        assert.equal(await events.count(),3,'submission and acceptance remain separate timeline events');
        const accepted=events.filter({hasText:'验收通过'});
        if(variant==='identical') assert.doesNotMatch(await accepted.innerText(),/已补齐归档签名|签名检查记录/,'duplicate evidence and summary are not repeated');
        if(variant==='different-evidence') assert.doesNotMatch(await accepted.innerText(),/验收独有证据/);
        if(variant==='different-summary') assert.doesNotMatch(await accepted.innerText(),/验收补充结论/);
      }
      await candidate.close();
    }
    const missing=structuredClone(full), oldLeaf=missing.structuredContent.progress.nodes.find(n=>n.id==='work');
    Object.assign(oldLeaf,{attempt_id:null,attempt:0,attempts:[],summary:'历史执行失败',failure_reason:'历史执行失败',evidence:[],execution_status:'failed',submission:null,review:null,review_status:'legacy_unrecorded',started_at:null,finished_at:null,responsibility_version:null});
    const older=await snapshotPage(host),missingFrame=await host.mount(older,'legacy',missing,'fullscreen');
    await missingFrame.locator('#step-list [data-step-id="work"] .plan-title').click();
    assert.equal(await missingFrame.locator('#detail-panel-result').getByText('历史执行失败',{exact:true}).count(),1);
    await missingFrame.locator('#detail-tab-records').click();
    assert.equal(await missingFrame.locator('.detail-event[data-kind="failed"]').getByText('历史执行失败',{exact:true}).count(),0,'current failure is shown in result rather than duplicated');
    assert.equal(await missingFrame.locator('#detail-panel-records time').count(),0);
    assert.doesNotMatch(await missingFrame.locator('#detail-panel-records').innerText(),/验收通过|第 1 次/);
    await older.close();
    for(const field of ['review_record_count','invalidated_result_count']){
      const partial=structuredClone(compact), slim=partial.structuredContent.progress.nodes.find(n=>n.id==='work');
      delete slim.attempt_count;delete slim.review_record_count;delete slim.invalidated_result_count;slim[field]=1;
      const partialPage=await snapshotPage(host);
      const partialFrame=await host.mount(partialPage,'counts',partial,'fullscreen');
      await partialFrame.locator('#step-list [data-step-id="work"] .plan-title').click();
      await partialFrame.locator('#detail-tab-records').click();
      await partialFrame.locator('#detail-panel-records').getByText(/正在读取/).waitFor();
      await partialPage.close();
    }
    const group=await host.rawRpc('init_task_network',{task_title:'归档综合验收',tasks:[{id:'archive-file',title:'核对一个文件',assignment:worker},{id:'checksum',title:'核对校验和',assignment:main}]});
    const peer={session_id:group.structuredContent.session_id,task_instance_id:group.structuredContent.task_instance_id};
    const active=await host.rawRpc('update_task',{...peer,task_id:'archive-file',execution_status:'running',assignment:worker});
    await host.rawRpc('bind_task_agent',{...peer,task_id:'archive-file',attempt_id:active.structuredContent.attempt_id,agent_name:'archive-fixture-binding'});
    await host.rawRpc('update_task',{...peer,task_id:'archive-file',attempt_id:active.structuredContent.attempt_id,execution_status:'finished',outcome:'resolved',summary:'文件核对成功',evidence:['文件记录']});
    await host.rawRpc('review_task',{...peer,task_id:'archive-file',attempt_id:active.structuredContent.attempt_id,decision:'accept',summary:'独立核对原件',evidence:['核对记录'],reviewer:main});
    const checksum=await host.rawRpc('update_task',{...peer,task_id:'checksum',execution_status:'running',assignment:main});
    await host.rawRpc('update_task',{...peer,task_id:'checksum',attempt_id:checksum.structuredContent.attempt_id,execution_status:'finished',outcome:'resolved',summary:'校验和一致',evidence:['校验结果']});
    await host.rawRpc('update_task',{...peer,task_id:'root',execution_status:'finished',outcome:'resolved',summary:'综合检查通过',evidence:['归档复验记录'],assignment:main});
    // A completed foreground root is normally flattened. Replay its real root ledger
    // as a parallel snapshot to exercise the visible root-detail entry point.
    const verified=await host.rawRpc('get_progress_view',{...peer,include_history:true});
    const envelope=structuredClone(full);
    envelope.structuredContent.progress.parallel_tasks=[verified.structuredContent.progress];
    const groupPage=await snapshotPage(host),groupFrame=await host.mount(groupPage,'synthesis',envelope,'fullscreen');
    await groupFrame.locator('#step-list > .plan-branch[data-instance="'+peer.task_instance_id+'"] > .plan-branch-head .branch-title').click();
    assert.match(await groupFrame.locator('#detail-panel-result').innerText(),/综合验收通过/);
    await groupFrame.locator('#detail-tab-records').click();
    await groupFrame.locator('.detail-event').getByText('综合验收通过',{exact:true}).waitFor();
    await groupPage.close();
    assert.deepEqual(host.errors,[]);
    const report={source:hash(fs.readFileSync(path.join(__dirname,'../plugins/task-progress/app.html'))),served:hash(host.html),attempts:item.attempts.map(a=>({id:a.attempt_id,started:a.started_at,submitted:a.submission.submitted_at,reviewed:a.review.reviewed_at,decision:a.review.decision})),screenshots:pics};
    if(out)fs.writeFileSync(path.join(out,'timeline-facts.json'),JSON.stringify(report,null,2)+'\n');
    console.log('Two-attempt timeline, result evidence, deferred failure and retry passed. '+report.source);
  }finally{await host.close();}
}
mainTest().catch(error=>{console.error(error);process.exitCode=1;});
