"""Transactional v3 task orchestration store and MCP tool handlers.

The SQLite database is the source of truth for v3 sessions.  Legacy JSON files
remain in place and are read by the compatibility layer in server.py.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class V3Error(ValueError):
    def __init__(self, code: str, message: str, recovery: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.recovery = recovery


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _uid() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, default: Any = None) -> Any:
    return json.loads(value) if value is not None else default


class V3Store:
    """Small, standard-library SQLite implementation of the v3 contract."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS sessions(
                  session_id TEXT PRIMARY KEY, current_task_id TEXT, generation INTEGER NOT NULL DEFAULT 0,
                  event_seq INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
                  binding_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_instances(
                  task_instance_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                  generation INTEGER NOT NULL, title TEXT NOT NULL, objective_json TEXT NOT NULL,
                  control_state TEXT NOT NULL, status TEXT NOT NULL, epoch INTEGER NOT NULL,
                  root_id TEXT NOT NULL, revision INTEGER NOT NULL, state_json TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT,
                  UNIQUE(session_id,generation));
                CREATE INDEX IF NOT EXISTS task_session_current ON task_instances(session_id, archived_at, generation);
                CREATE TABLE IF NOT EXISTS epochs(
                  task_instance_id TEXT NOT NULL REFERENCES task_instances(task_instance_id) ON DELETE CASCADE,
                  epoch INTEGER NOT NULL, root_id TEXT NOT NULL, reason TEXT, snapshot_json TEXT NOT NULL,
                  created_at TEXT NOT NULL, closed_at TEXT, PRIMARY KEY(task_instance_id,epoch));
                CREATE TABLE IF NOT EXISTS nodes(
                  task_instance_id TEXT NOT NULL, epoch INTEGER NOT NULL, node_id TEXT NOT NULL,
                  parent_id TEXT, title TEXT NOT NULL, planning_state TEXT NOT NULL,
                  execution_status TEXT NOT NULL, outcome TEXT, payload_json TEXT NOT NULL,
                  PRIMARY KEY(task_instance_id,epoch,node_id),
                  FOREIGN KEY(task_instance_id,epoch) REFERENCES epochs(task_instance_id,epoch) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS attempts(
                  attempt_id TEXT PRIMARY KEY, task_instance_id TEXT NOT NULL, epoch INTEGER NOT NULL,
                  node_id TEXT NOT NULL, kind TEXT NOT NULL, lifecycle TEXT NOT NULL,
                  input_revision INTEGER NOT NULL, payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL, finished_at TEXT);
                CREATE TABLE IF NOT EXISTS assignments(
                  assignment_id TEXT PRIMARY KEY, task_instance_id TEXT NOT NULL, epoch INTEGER NOT NULL,
                  node_id TEXT NOT NULL, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                  status TEXT NOT NULL, provider TEXT, execution_id TEXT, payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions(
                  action_id TEXT PRIMARY KEY, task_instance_id TEXT NOT NULL REFERENCES task_instances(task_instance_id),
                  epoch INTEGER NOT NULL, kind TEXT NOT NULL, node_id TEXT NOT NULL,
                  status TEXT NOT NULL, lease_token TEXT, lease_until TEXT, payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS actions_live ON actions(task_instance_id,status,created_at);
                CREATE TABLE IF NOT EXISTS results(
                  result_id TEXT PRIMARY KEY, task_instance_id TEXT NOT NULL, epoch INTEGER NOT NULL,
                  node_id TEXT NOT NULL, result_revision INTEGER NOT NULL, attempt_id TEXT,
                  outcome TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                  validity TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(
                  evidence_id TEXT PRIMARY KEY, task_instance_id TEXT NOT NULL, epoch INTEGER NOT NULL,
                  node_id TEXT NOT NULL, result_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                  session_id TEXT NOT NULL, event_seq INTEGER NOT NULL, task_instance_id TEXT,
                  revision INTEGER, kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                  PRIMARY KEY(session_id,event_seq));
                CREATE TABLE IF NOT EXISTS request_receipts(
                  session_id TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                  result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id,request_id));
                CREATE TABLE IF NOT EXISTS views(
                  session_id TEXT PRIMARY KEY, view_seq INTEGER NOT NULL, view_id TEXT NOT NULL,
                  updated_at TEXT NOT NULL);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=8, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=8000")
        return db

    @contextmanager
    def _tx(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db: sqlite3.Connection, session_id: str, task_id: str | None,
               revision: int | None, kind: str, payload: Any) -> int:
        db.execute("UPDATE sessions SET event_seq=event_seq+1,updated_at=? WHERE session_id=?", (_now(),session_id))
        seq = int(db.execute("SELECT event_seq FROM sessions WHERE session_id=?",(session_id,)).fetchone()[0])
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",(session_id,seq,task_id,revision,kind,_json(payload),_now()))
        return seq

    def _session(self, db: sqlite3.Connection, sid: str) -> sqlite3.Row:
        row=db.execute("SELECT * FROM sessions WHERE session_id=?",(sid,)).fetchone()
        if row is None: raise V3Error("SCOPE_STALE","没有这个 v3 会话。","核对 session_id；若它来自旧版进度记录，先显式迁移。首次创建任务请使用 task_open(mode=new)。")
        return row

    def _task(self, db: sqlite3.Connection, sid: str, task_id: str | None = None) -> tuple[sqlite3.Row,dict[str,Any]]:
        session=self._session(db,sid); tid=task_id or session["current_task_id"]
        if not tid: raise V3Error("SCOPE_STALE","会话当前没有任务。","用 task_open 创建任务。")
        row=db.execute("SELECT * FROM task_instances WHERE task_instance_id=? AND session_id=?",(tid,sid)).fetchone()
        if row is None: raise V3Error("SCOPE_STALE","任务不属于这个会话。","重新读取当前任务范围。")
        return row,_decode(row["state_json"],{})

    def _save(self, db: sqlite3.Connection, row: sqlite3.Row, state: dict[str,Any], kind: str) -> sqlite3.Row:
        revision=int(row["revision"])+1; now=_now(); state["updated_at"]=now
        status=self._root_status(state)
        db.execute("UPDATE task_instances SET state_json=?,revision=?,status=?,updated_at=? WHERE task_instance_id=?",
                   (_json(state),revision,status,now,row["task_instance_id"]))
        tid=row["task_instance_id"]; epoch=int(state["epoch"])
        db.execute("INSERT INTO epochs(task_instance_id,epoch,root_id,reason,snapshot_json,created_at,closed_at) VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(task_instance_id,epoch) DO UPDATE SET root_id=excluded.root_id,snapshot_json=excluded.snapshot_json,closed_at=excluded.closed_at",
                   (tid,epoch,state["root_id"],state.get("epoch_reason"),_json(state),state.get("created_at",now),state.get("closed_at")))
        db.execute("DELETE FROM nodes WHERE task_instance_id=? AND epoch=?",(tid,epoch))
        for node in state["nodes"].values():
            db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?)",(tid,epoch,node["id"],node.get("parent_id"),node["title"],node.get("planning_state","deferred"),node["execution_status"],node.get("outcome"),_json(node)))
        db.execute("UPDATE sessions SET revision=revision+1,updated_at=? WHERE session_id=?",(now,row["session_id"]))
        self._event(db,row["session_id"],tid,revision,kind,{"epoch":epoch,"root_id":state["root_id"]})
        return db.execute("SELECT * FROM task_instances WHERE task_instance_id=?",(tid,)).fetchone()

    @staticmethod
    def _root_status(state: dict[str,Any]) -> str:
        root=state["nodes"][state["root_id"]]
        if root.get("execution_status") in ("finished","failed","cancelled"):
            return root["outcome"] or root["execution_status"]
        if state.get("control_state")!="active": return state["control_state"]
        if any(n.get("execution_status")=="running" for n in state["nodes"].values()): return "running"
        if root.get("children") and all(V3Store._terminal(state["nodes"][i]) for i in root["children"]): return "waiting_synthesis"
        return "pending"

    @staticmethod
    def _terminal(node: dict[str,Any]) -> bool:
        return node.get("execution_status") in ("failed","cancelled") or (node.get("execution_status")=="finished" and node.get("outcome") in ("resolved","open"))

    @staticmethod
    def _node_ready(state: dict[str,Any], node: dict[str,Any]) -> bool:
        if node.get("children") or node.get("execution_status")!="pending": return False
        for edge in node.get("depends_on",[]):
            dep=state["nodes"].get(edge.get("node_id"))
            if dep is None:return False
            status=dep.get("execution_status")
            policy=edge.get("policy","resolved")
            ok=(status=="finished" and dep.get("outcome")=="resolved") if policy=="resolved" else (
                V3Store._terminal(dep) if policy in ("returned","legacy_terminal") else False)
            if not ok:return False
        return True

    def _progress(self, db: sqlite3.Connection, row: sqlite3.Row, state: dict[str,Any]) -> dict[str,Any]:
        nodes=list(state["nodes"].values()); leaves=[n for n in nodes if not n.get("children")]
        status_cache={}
        def display_status(node_id:str)->str:
            if node_id in status_cache:return status_cache[node_id]
            node=state["nodes"][node_id];status=node.get("execution_status")
            if status=="finished":value=node.get("outcome") or "finished"
            elif status in ("running","failed","cancelled"):value=status
            elif node.get("children"):
                child=[display_status(i) for i in node["children"]]
                value="running" if any(x=="running" for x in child) else "waiting_synthesis" if all(x in ("resolved","open","finished","failed","cancelled") for x in child) else "decomposing" if any(x in ("ready","decomposing") for x in child) else "planning" if any(x in ("deferred","planning") for x in child) else "blocked"
            elif node.get("planning_state")=="deferred":value="deferred"
            else:value="ready" if self._node_ready(state,node) else "blocked"
            status_cache[node_id]=value;return value
        for node in nodes:
            node["status"]=display_status(node["id"]);node["ready"]=self._node_ready(state,node)
        ended=sum(n.get("execution_status") in ("finished","failed","cancelled") for n in leaves)
        resolved=sum(n.get("execution_status")=="finished" and n.get("outcome")=="resolved" for n in leaves)
        ready=[n["id"] for n in leaves if self._node_ready(state,n) and n.get("planning_state")=="executable"]
        live=db.execute("SELECT task_instance_id,generation,title,status,revision,state_json,created_at,updated_at,archived_at FROM task_instances WHERE session_id=? AND task_instance_id<>? AND archived_at IS NOT NULL ORDER BY generation DESC LIMIT 20",(row["session_id"],row["task_instance_id"])).fetchall()
        history=[]
        for ep in db.execute("SELECT epoch,snapshot_json FROM epochs WHERE task_instance_id=? AND epoch<? ORDER BY epoch DESC LIMIT 20",(row["task_instance_id"],state["epoch"])).fetchall():
            snap=_decode(ep["snapshot_json"],{}); history.append({"epoch":ep["epoch"],"root_id":snap.get("root_id"),"task_title":row["title"],"reason":snap.get("epoch_reason"),"nodes":list(snap.get("nodes",{}).values()),"closed_at":snap.get("closed_at")})
        archived=[]
        for task in live:
            old=_decode(task["state_json"],{}); oldnodes=list(old.get("nodes",{}).values()); oldleaves=[n for n in oldnodes if not n.get("children")]
            total=len(oldleaves); complete=sum(n.get("execution_status") in ("finished","failed","cancelled") for n in oldleaves)
            archived.append({"schema_version":2,"api_version":3,"session_id":row["session_id"],"task_instance_id":task["task_instance_id"],"task_title":task["title"],"status":task["status"],"total":total,"completed":complete,"percent":round(complete/total*100) if total else 0,"created_at":task["created_at"],"updated_at":task["updated_at"],"archived_at":task["archived_at"],"epoch":old.get("epoch",0),"root_id":old.get("root_id"),"nodes":oldnodes,"history":self._history_rows(db,task["task_instance_id"],old.get("epoch",0))})
        return {"schema_version":2,"api_version":3,"session_id":row["session_id"],"task_instance_id":row["task_instance_id"],"generation":row["generation"],"revision":row["revision"],"event_seq":self._session(db,row["session_id"])["event_seq"],"epoch":state["epoch"],"task_title":row["title"],"objective":state.get("objective"),"requirements":state.get("requirements",[]),"control_state":state["control_state"],"status":row["status"],"completed":ended,"total":len(leaves),"percent":round(ended/len(leaves)*100) if leaves else 0,"resolved_leaf_count":resolved,"open_leaf_count":sum(n.get("outcome")=="open" for n in leaves),"ready_count":len(ready),"ready_tasks":ready,"blocked_count":sum(not self._node_ready(state,n) and n.get("execution_status")=="pending" for n in leaves),"executing_count":sum(n.get("execution_status")=="running" for n in leaves),"created_at":row["created_at"],"updated_at":row["updated_at"],"root_id":state["root_id"],"nodes":nodes,"history":history,"task_history":archived,"main_session":state.get("main_session"),"conclusion":state.get("conclusion"),"planning_state":state["nodes"][state["root_id"]].get("planning_state")}

    def _history_rows(self, db:sqlite3.Connection,tid:str,current:int)->list[dict[str,Any]]:
        out=[]
        for ep in db.execute("SELECT epoch,snapshot_json,closed_at FROM epochs WHERE task_instance_id=? AND epoch<? ORDER BY epoch DESC LIMIT 20",(tid,current)).fetchall():
            snap=_decode(ep["snapshot_json"],{}); out.append({"epoch":ep["epoch"],"root_id":snap.get("root_id"),"task_title":snap.get("title"),"reason":snap.get("epoch_reason"),"nodes":list(snap.get("nodes",{}).values()),"closed_at":ep["closed_at"]})
        return out

    @staticmethod
    def _response(progress:dict[str,Any]|None,sid:str,text:str="") -> dict[str,Any]:
        structured={"api_version":3,"session_id":sid,"progress":progress}
        return {"content":[{"type":"text","text":text or (progress.get("task_title","") if progress else "无当前任务")}],"structuredContent":structured}

    def _receipt(self,db:sqlite3.Connection,sid:str,request_id:str,args:dict[str,Any])->dict[str,Any]|None:
        if not isinstance(request_id,str) or not request_id.strip(): raise V3Error("PLAN_INVALID","写操作必须带 request_id。","生成 UUID 并重试。")
        digest=hashlib.sha256(_json(args).encode()).hexdigest(); old=db.execute("SELECT request_hash,result_json FROM request_receipts WHERE session_id=? AND request_id=?",(sid,request_id)).fetchone()
        if old:
            if old["request_hash"]!=digest: raise V3Error("IDEMPOTENCY_CONFLICT","相同 request_id 对应了不同参数。","使用新 request_id。")
            return _decode(old["result_json"],{})
        return None

    def _store_receipt(self,db:sqlite3.Connection,sid:str,rid:str,args:dict[str,Any],result:dict[str,Any])->None:
        digest=hashlib.sha256(_json(args).encode()).hexdigest()
        db.execute("INSERT INTO request_receipts VALUES(?,?,?,?,?)",(sid,rid,digest,_json(result),_now()))

    def _scope(self,db:sqlite3.Connection,args:dict[str,Any],write:bool=True)->tuple[sqlite3.Row,dict[str,Any]]:
        scope=args.get("scope") or {}; sid=args.get("session_id") or scope.get("session_id")
        if not isinstance(sid,str): raise V3Error("SCOPE_STALE","缺少 session_id。")
        row,state=self._task(db,sid,scope.get("task_instance_id"))
        if scope.get("task_instance_id") and scope["task_instance_id"]!=row["task_instance_id"]: raise V3Error("SCOPE_STALE","task_instance_id 已过期。","重新读取当前任务。")
        if scope.get("epoch") is not None and int(scope["epoch"])!=int(state["epoch"]): raise V3Error("SCOPE_STALE","计划轮次已变化。","重新读取当前计划。")
        expected=args.get("expected_revision")
        if write and expected is None: raise V3Error("REVISION_CONFLICT","写操作必须携带读取到的 expected_revision。","重新读取当前任务后再提交。")
        if write and expected is not None and int(expected)!=int(row["revision"]): raise V3Error("REVISION_CONFLICT","任务版本已变化。","读取最新任务并重新计算决策。")
        return row,state

    def call(self,name:str,args:dict[str,Any])->dict[str,Any]:
        method=getattr(self,"api_"+name,None)
        if not callable(method): raise V3Error("PLAN_INVALID",f"未知 v3 工具：{name}")
        return method(args)

    def api_task_open(self,a:dict[str,Any])->dict[str,Any]:
        mode=a.get("mode","ensure")
        if mode not in ("ensure","new","resume"): raise V3Error("PLAN_INVALID","mode 只能是 ensure、new 或 resume。")
        supplied_sid=a.get("session_id")
        if mode=="resume" and not supplied_sid:
            raise V3Error("SCOPE_STALE","恢复任务必须提供已有 session_id。","读取当前会话保存的 session_id；首次创建任务请使用 mode=new 或 ensure 并提供目标。")
        sid=supplied_sid or str(uuid.uuid5(uuid.NAMESPACE_URL,"task-progress:"+str(a.get("request_id") or _uid())))
        try: sid=str(uuid.UUID(sid))
        except (ValueError,TypeError): raise V3Error("SCOPE_STALE","session_id 必须是 UUID。")
        if mode in ("ensure","new") and not a.get("request_id"): raise V3Error("PLAN_INVALID","创建或归档任务必须带 request_id。","生成 UUID 并重试。")
        with self._tx() as db:
            session=db.execute("SELECT * FROM sessions WHERE session_id=?",(sid,)).fetchone()
            if session is None:
                if mode=="resume": raise V3Error("SCOPE_STALE","没有可恢复的 v3 会话。","核对 session_id；首次创建任务请使用 mode=new 或 ensure 并提供目标。")
                now=_now();db.execute("INSERT INTO sessions(session_id,created_at,updated_at,binding_json) VALUES(?,?,?,?)",(sid,now,now,_json(a.get("binding")) if a.get("binding") else None));session=db.execute("SELECT * FROM sessions WHERE session_id=?",(sid,)).fetchone()
            replay=self._receipt(db,sid,a.get("request_id"),a) if a.get("request_id") else None
            if replay:return replay
            current=None
            if session["current_task_id"]: current=db.execute("SELECT * FROM task_instances WHERE task_instance_id=?",(session["current_task_id"],)).fetchone()
            if mode in ("ensure","resume") and current:
                state=_decode(current["state_json"],{});progress=self._progress(db,current,state)
                response=self._response(progress,sid,"已恢复当前任务。")
                if a.get("request_id"):self._store_receipt(db,sid,a["request_id"],a,response)
                return response
            if mode=="resume": raise V3Error("SCOPE_STALE","当前会话没有可恢复的任务。")
            if mode=="new" and current:
                old_state=_decode(current["state_json"],{})
                live_assignment=any(a.get("status") in ("planned","dispatching","queued","running","unknown","stopping") for n in old_state.get("nodes",{}).values() for a in n.get("assignments",{}).values())
                live_action=db.execute("SELECT 1 FROM actions WHERE task_instance_id=? AND status='effect_started' LIMIT 1",(current["task_instance_id"],)).fetchone()
                if live_assignment or live_action or any(n.get("execution_status") in ("running","unknown") for n in old_state.get("nodes",{}).values()): raise V3Error("RESOURCE_CONFLICT","仍有运行或未知执行，不能归档并开始新任务。","先对账或结束在途执行。")
                db.execute("UPDATE task_instances SET archived_at=?,updated_at=? WHERE task_instance_id=?",(_now(),_now(),current["task_instance_id"]))
            objective=a.get("objective") or a.get("task_title")
            if not isinstance(objective,str) or not objective.strip(): raise V3Error("PLAN_INVALID","新任务需要 objective 或 task_title。")
            title=a.get("task_title") or objective
            requirements=a.get("requirements") or [{"id":"R1","text":objective,"required":True}]
            if not isinstance(requirements,list) or not requirements: raise V3Error("PLAN_INVALID","requirements 必须是非空列表。")
            normalized=[]
            for i,r in enumerate(requirements):
                if isinstance(r,str): r={"id":f"R{i+1}","text":r}
                if not isinstance(r,dict) or not isinstance(r.get("text"),str) or not r["text"].strip(): raise V3Error("PLAN_INVALID","每条需求都需要 text。")
                normalized.append({"id":r.get("id",f"R{i+1}"),"text":r["text"].strip(),"required":r.get("required",True)})
            task_id=_uid(); root_id="root"; now=_now(); generation=int(session["generation"])+1
            root={"id":root_id,"parent_id":None,"children":[],"title":str(title).strip(),"objective":objective.strip(),"requirement_ids":[r["id"] for r in normalized],"acceptance_criteria":a.get("acceptance_criteria",[]),"depends_on":[],"planning_state":"deferred","execution_status":"pending","activity":"idle","outcome":None,"summary":"","outputs":[],"unresolved":[],"checks":[],"evidence_refs":[],"assignment_ids":[],"attempt_id":None,"attempt":0,"result_revision":0,"priority":0,"resources":[]}
            state={"epoch":0,"root_id":root_id,"nodes":{root_id:root},"objective":objective.strip(),"requirements":normalized,"control_state":"active","main_session":a.get("main_session"),"history":[],"conclusion":None,"created_at":now,"updated_at":now}
            db.execute("UPDATE sessions SET current_task_id=?,generation=?,updated_at=? WHERE session_id=?",(task_id,generation,now,sid))
            db.execute("INSERT INTO task_instances(task_instance_id,session_id,generation,title,objective_json,control_state,status,epoch,root_id,revision,state_json,created_at,updated_at) VALUES(?,?,?,?,?,'active','pending',0,?,0,?,?,?)",(task_id,sid,generation,str(title).strip(),_json(objective.strip()),root_id,_json(state),now,now))
            row=db.execute("SELECT * FROM task_instances WHERE task_instance_id=?",(task_id,)).fetchone();self._save(db,row,state,"task.open")
            row=db.execute("SELECT * FROM task_instances WHERE task_instance_id=?",(task_id,)).fetchone();response=self._response(self._progress(db,row,state),sid,"已创建任务；根任务等待规划。\n准备 PLAN 动作后再决定直接执行或拆分。")
            if a.get("request_id"):self._store_receipt(db,sid,a["request_id"],a,response)
            return response

    def api_task_read(self,a:dict[str,Any])->dict[str,Any]:
        db=self._connect()
        try:
            row,state=self._scope(db,a,False);projection=a.get("projection","summary")
            progress=self._progress(db,row,state)
            if projection=="summary": result=progress
            elif projection=="node":
                node=state["nodes"].get(a.get("node_id"))
                if not node: raise V3Error("SCOPE_STALE","任务项不存在或属于旧计划。")
                result={"scope":{"session_id":row["session_id"],"task_instance_id":row["task_instance_id"],"epoch":state["epoch"]},"node":node}
            elif projection=="context":
                n=state["nodes"].get(a.get("node_id"));
                if not n: raise V3Error("SCOPE_STALE","任务项不存在。")
                result={"objective":state["objective"],"requirements":state["requirements"],"node":n,"ancestors":[state["nodes"][i] for i in self._ancestors(state,n)],"predecessors":[state["nodes"][e["node_id"]] for e in n.get("depends_on",[])],"remaining_budget":state.get("budget",{})}
            elif projection=="events":
                cursor=int(a.get("after_event_seq",0));limit=min(max(int(a.get("limit",50)),1),100)
                result={"events":[{**dict(e),"payload":_decode(e["payload_json"],{})} for e in db.execute("SELECT * FROM events WHERE session_id=? AND event_seq>? ORDER BY event_seq LIMIT ?",(row["session_id"],cursor,limit)).fetchall()]}
            else: raise V3Error("PLAN_INVALID","projection 只能是 summary、node、context 或 events。")
            return {"content":[{"type":"text","text":_json(result)}],"structuredContent":{"api_version":3,"session_id":row["session_id"],"progress":progress,"projection":projection,"data":result}}
        finally:
            db.close()

    @staticmethod
    def _ancestors(state:dict[str,Any],node:dict[str,Any])->list[str]:
        result=[];parent=node.get("parent_id")
        while parent and parent in state["nodes"]: result.append(parent);parent=state["nodes"][parent].get("parent_id")
        return list(reversed(result))

    def api_task_advance(self,a:dict[str,Any])->dict[str,Any]:
        mode=a.get("mode","preview");limit=min(max(int(a.get("max_actions",1)),1),8)
        if mode not in ("preview","claim"): raise V3Error("PLAN_INVALID","mode 只能是 preview 或 claim。")
        if mode=="preview":
            with self._connect() as db:
                row,state=self._scope(db,a,False);actions=self._candidates(state)
                return {"content":[{"type":"text","text":_json(actions[:limit])}],"structuredContent":{"api_version":3,"session_id":row["session_id"],"task_instance_id":row["task_instance_id"],"epoch":state["epoch"],"revision":row["revision"],"mode":"preview","actions":actions[:limit],"blocked_reasons":self._blocked(state)}}
        sid=(a.get("scope") or {}).get("session_id") or a.get("session_id");rid=a.get("request_id")
        with self._tx() as db:
            replay=self._receipt(db,sid,rid,a)
            if replay:return replay
            row,state=self._scope(db,a);candidates=self._candidates(state);claimed=[]
            for candidate in candidates:
                if len(claimed)>=limit:break
                active_rows=db.execute("SELECT payload_json FROM actions WHERE task_instance_id=? AND epoch=? AND kind=? AND node_id=? AND (status='effect_started' OR (status='leased' AND lease_until>?))",(row["task_instance_id"],state["epoch"],candidate["kind"],candidate["node_id"],_now())).fetchall()
                active=any(_decode(r["payload_json"],{}).get("assignment_id")==candidate.get("assignment_id") for r in active_rows) if candidate["kind"]=="DISPATCH" else bool(active_rows)
                if active:continue
                action_id=_uid();token=_uid();until=datetime.now().astimezone().timestamp()+60
                candidate.update({"action_id":action_id,"lease_token":token,"lease_seconds":60,"input_revision":row["revision"]})
                db.execute("INSERT INTO actions VALUES(?,?,?,?,?,'leased',?,?,?, ?,?)",(action_id,row["task_instance_id"],state["epoch"],candidate["kind"],candidate["node_id"],token,datetime.fromtimestamp(until).astimezone().isoformat(),_json(candidate),_now(),_now()))
                if candidate["kind"] in ("ORCHESTRATE","SYNTHESIZE"):
                    node=state["nodes"][candidate["node_id"]];attempt_id=_uid();node["attempt"]+=1;node["attempt_id"]=attempt_id;node["activity"]="prepared"
                    db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,'prepared',?,?,?,NULL)",(attempt_id,row["task_instance_id"],state["epoch"],node["id"],"synthesis" if candidate["kind"]=="SYNTHESIZE" else "execution",row["revision"],_json({"action_id":action_id,"input_revision":row["revision"]}),_now()))
                    candidate["attempt_id"]=attempt_id
                elif candidate["kind"]=="DISPATCH":
                    node=state["nodes"][candidate["node_id"]]
                    assignment=node.get("assignments",{}).get(candidate.get("assignment_id"))
                    if assignment:
                        assignment["dispatch_action"]={"action_id":action_id,"lease_token":token}
                        db.execute("UPDATE assignments SET payload_json=?,updated_at=? WHERE assignment_id=?",(_json(assignment),_now(),candidate["assignment_id"]))
                claimed.append(candidate)
            if claimed:
                row=self._save(db,row,state,"action.claim")
                result={"api_version":3,"session_id":sid,"task_instance_id":row["task_instance_id"],"epoch":state["epoch"],"revision":row["revision"],"actions":claimed,"blocked_reasons":self._blocked(state)}
            else: result={"api_version":3,"session_id":sid,"task_instance_id":row["task_instance_id"],"epoch":state["epoch"],"revision":row["revision"],"actions":[],"blocked_reasons":self._blocked(state)}
            self._store_receipt(db,sid,rid,a,result);return {"content":[{"type":"text","text":_json(result)}],"structuredContent":result}

    def _candidates(self,state:dict[str,Any])->list[dict[str,Any]]:
        if state["control_state"]!="active":return []
        out=[]
        for node in state["nodes"].values():
            ready=self._node_ready(state,node)
            assignments=[node.get("assignments",{}).get(aid,{}) for aid in node.get("assignment_ids",[])]
            in_flight=any(a.get("status") in ("planned","dispatching","queued","running","unknown","stopping") for a in assignments)
            if ready and node.get("planning_state")=="deferred":out.append({"kind":"PLAN","node_id":node["id"],"title":node["title"],"reason":"任务项等待规划"})
            elif ready and node.get("planning_state")=="executable" and not in_flight:out.append({"kind":"ORCHESTRATE","node_id":node["id"],"title":node["title"],"reason":"任务项可以开始执行"})
            elif node.get("execution_status")=="running" and assignments and not in_flight and any(a.get("report_final") or a.get("status") in ("finished","failed","cancelled") for a in assignments):out.append({"kind":"COLLECT","node_id":node["id"],"title":node["title"],"reason":"执行报告已返回，等待主会话验收"})
            if node.get("planning_state")=="expanded" and node.get("children") and all(self._terminal(state["nodes"][i]) for i in node["children"]) and node.get("execution_status")=="pending":out.append({"kind":"SYNTHESIZE","node_id":node["id"],"title":node["title"],"reason":"子任务已结束，等待本级综合验收"})
            for aid,ass in zip(node.get("assignment_ids",[]),assignments):
                if ass.get("status")=="planned":out.append({"kind":"DISPATCH","node_id":node["id"],"assignment_id":aid,"title":node["title"],"reason":"派发已获准，需由宿主执行"})
        return out

    def _blocked(self,state:dict[str,Any])->list[dict[str,str]]:
        result=[]
        for n in state["nodes"].values():
            if n.get("execution_status")!="pending" or n.get("children"):continue
            for edge in n.get("depends_on",[]):
                d=state["nodes"].get(edge["node_id"])
                if d and not self._node_ready(state,{**n,"depends_on":[edge]}):result.append({"node_id":n["id"],"reason":"依赖尚未满足","dependency_id":d["id"],"dependency_status":d.get("execution_status"),"dependency_outcome":d.get("outcome")})
        if state["control_state"]!="active":result.append({"reason":"任务控制状态为 "+state["control_state"]})
        return result

    def api_task_decide(self,a:dict[str,Any])->dict[str,Any]:
        sid=(a.get("scope") or {}).get("session_id") or a.get("session_id");rid=a.get("request_id")
        with self._tx() as db:
            replay=self._receipt(db,sid,rid,a)
            if replay:return replay
            row,state=self._scope(db,a);kind=a.get("kind");action_id=a.get("action_id");lease=a.get("lease_token")
            action=None
            if action_id:
                action=db.execute("SELECT * FROM actions WHERE action_id=? AND task_instance_id=?",(action_id,row["task_instance_id"])).fetchone()
                if not action or action["status"]!="leased" or action["lease_token"]!=lease or (action["lease_until"] and action["lease_until"]<=_now()):raise V3Error("LEASE_STALE","动作认领已失效。","重新读取并认领动作。")
                if int(action["epoch"])!=state["epoch"]:raise V3Error("SCOPE_STALE","动作属于旧计划。")
            node=state["nodes"].get(a.get("node_id") or (action["node_id"] if action else ""))
            result_data={"kind":kind,"node_id":node["id"] if node else a.get("node_id")}
            if kind in ("plan.execute","plan.decompose"):
                if not node or not self._node_ready(state,node) or node.get("planning_state")!="deferred":raise V3Error("NODE_NOT_READY","该任务项当前不能规划。")
                if action and action["kind"]!="PLAN":raise V3Error("LEASE_STALE","动作类型与规划决策不符。")
                if kind=="plan.execute":
                    rationale=a.get("rationale")
                    if not isinstance(rationale,str) or not rationale.strip():raise V3Error("PLAN_INVALID","直接执行决定必须写 rationale。")
                    node["planning_state"]="executable";node["planning_rationale"]=rationale.strip()
                else:self._install_plan(state,node,a)
            elif kind in ("orchestrate.dispatch","orchestrate.wait","orchestrate.finish"):
                if not node:raise V3Error("SCOPE_STALE","任务项不存在。")
                if action and action["kind"] not in ("ORCHESTRATE","COLLECT","SYNTHESIZE"):raise V3Error("LEASE_STALE","动作类型与协调决策不符。")
                if kind=="orchestrate.dispatch":
                    if not node.get("attempt_id") or not a.get("role") or not a.get("instructions") or not a.get("deliverables"):raise V3Error("PLAN_INVALID","派发决定需要 role、完整 instructions 和 deliverables。")
                    if a.get("execution_mode")!="subagent" or not a.get("model") or not a.get("reasoning_effort"):
                        raise V3Error("PLAN_INVALID","子智能体派发需要记录实际 execution_mode、model 和 reasoning_effort。")
                    profile=a.get("profile","standard")
                    if profile not in ("cheap","standard","heavy"):raise V3Error("PLAN_INVALID","未知执行 profile。")
                    assignment_id=_uid(); assignment={"assignment_id":assignment_id,"attempt_id":node["attempt_id"],"role":a["role"],"instructions":a["instructions"],"deliverables":a["deliverables"],"resources":a.get("resources",[]),"execution_mode":a["execution_mode"],"model":a["model"],"reasoning_effort":a["reasoning_effort"],"profile":profile,"status":"planned","provider_ref":None,"created_at":_now()}
                    node.setdefault("assignments",{})[assignment_id]=assignment;node.setdefault("assignment_ids",[]).append(assignment_id);node["activity"]="waiting_workers"
                    if action: assignment["dispatch_action"]={"action_id":action_id,"lease_token":lease}
                    db.execute("INSERT INTO assignments VALUES(?,?,?,?,?,'planned',NULL,NULL,?,?,?)",(assignment_id,row["task_instance_id"],state["epoch"],node["id"],node["attempt_id"],_json(assignment),_now(),_now()))
                    result_data={"assignment":assignment,"note":"插件只登记派发动作；Codex 执行驱动仍须实际调用宿主工具。"}
                elif kind=="orchestrate.wait":
                    wait_for=a.get("wait_for")
                    if not wait_for or not any(x.get("assignment_id")==wait_for for x in node.get("assignments",{}).values()):raise V3Error("PLAN_INVALID","等待必须指向本任务项的真实 assignment。")
                    node["activity"]="waiting_workers";node["wait_for"]=wait_for;result_data={"waiting_for":wait_for}
                else:
                    if any(x.get("status") in ("planned","dispatching","queued","running","unknown","stopping") for x in node.get("assignments",{}).values()):raise V3Error("DISPATCH_UNKNOWN","仍有未结算派发，不能验收。","先收集报告或核对外部状态。")
                    self._finish_node(db,row,state,node,a,kind)
                    result_data={"result_id":node["result_id"],"outcome":node["outcome"],"result_revision":node["result_revision"]}
            elif kind=="rebuild":
                if any(n.get("execution_status") in ("running","unknown") for n in state["nodes"].values()):raise V3Error("RESOURCE_CONFLICT","仍有在途或未知执行，暂不能重建。")
                reason=a.get("reason")
                if not isinstance(reason,str) or not reason.strip():raise V3Error("PLAN_INVALID","重建必须给出原因。")
                db.execute("UPDATE epochs SET closed_at=? WHERE task_instance_id=? AND epoch=?",(_now(),row["task_instance_id"],state["epoch"]))
                state["epoch"]+=1;state["epoch_reason"]=reason.strip();root={"id":"root-"+str(state["epoch"]),"parent_id":None,"children":[],"title":state["objective"],"objective":state["objective"],"requirement_ids":[r["id"] for r in state["requirements"]],"acceptance_criteria":[],"depends_on":[],"planning_state":"deferred","execution_status":"pending","activity":"idle","outcome":None,"summary":"","outputs":[],"unresolved":[],"checks":[],"evidence_refs":[],"assignment_ids":[],"attempt":0,"attempt_id":None,"result_revision":0,"priority":0,"resources":[]};state["root_id"]=root["id"];state["nodes"]={root["id"]:root};result_data={"epoch":state["epoch"],"root_id":state["root_id"]}
            else:raise V3Error("PLAN_INVALID",f"不支持的决策种类：{kind}")
            if action:db.execute("UPDATE actions SET status='applied',updated_at=? WHERE action_id=?",(_now(),action_id))
            row=self._save(db,row,state,"decision."+str(kind));progress=self._progress(db,row,state)
            response={"api_version":3,"session_id":sid,"scope":{"session_id":sid,"task_instance_id":row["task_instance_id"],"epoch":state["epoch"]},"revision":row["revision"],"decision":result_data,"progress":progress}
            self._store_receipt(db,sid,rid,a,response);return {"content":[{"type":"text","text":_json(response)}],"structuredContent":response}

    def _install_plan(self,state:dict[str,Any],parent:dict[str,Any],a:dict[str,Any])->None:
        tasks=a.get("tasks");rationale=a.get("rationale");
        if not isinstance(rationale,str) or not rationale.strip():raise V3Error("PLAN_INVALID","分解决定必须说明原因。")
        if not isinstance(tasks,list) or len(tasks)<2:raise V3Error("PLAN_INVALID","分解至少需要两个有独立价值的子任务。")
        requirements={x["id"] for x in state["requirements"]}; covered=set(); ids=set(state["nodes"]);new=[]
        for t in tasks:
            if not isinstance(t,dict) or not isinstance(t.get("id"),str) or not t["id"].strip() or t["id"] in ids:raise V3Error("PLAN_INVALID","子任务 ID 缺失或重复。")
            ids.add(t["id"]); title=t.get("title");objective=t.get("objective",title);covers=t.get("requirement_ids",[]);accept=t.get("acceptance_criteria",[])
            if not isinstance(title,str) or not title.strip() or not isinstance(objective,str) or not objective.strip() or not isinstance(accept,list) or not accept:raise V3Error("PLAN_INVALID","每个子任务需要 title、objective 和 acceptance_criteria。")
            if not isinstance(covers,list) or not set(covers)<=requirements:raise V3Error("PLAN_INVALID","requirement_ids 引用了未知需求。")
            covered.update(covers);deps=t.get("depends_on",[])
            if not isinstance(deps,list):raise V3Error("PLAN_INVALID","depends_on 必须是数组。")
            new.append({"id":t["id"],"parent_id":parent["id"],"children":[],"title":title.strip(),"objective":objective.strip(),"requirement_ids":covers,"acceptance_criteria":accept,"depends_on":[{"node_id":d.get("node_id") if isinstance(d,dict) else d,"policy":d.get("policy","resolved") if isinstance(d,dict) else t.get("dependency_policy","resolved")} for d in deps],"planning_state":"deferred","execution_status":"pending","activity":"idle","outcome":None,"summary":"","outputs":[],"unresolved":[],"checks":[],"evidence_refs":[],"assignment_ids":[],"attempt":0,"attempt_id":None,"result_revision":0,"priority":int(t.get("priority",0)),"resources":t.get("resources",[])})
        if requirements-covered:raise V3Error("PLAN_INVALID","子任务覆盖不完整："+", ".join(sorted(requirements-covered)))
        sib={n["id"] for n in new}
        for n in new:
            if len(n["depends_on"])!=len({e["node_id"] for e in n["depends_on"]}) or any(e["node_id"] not in sib for e in n["depends_on"]):raise V3Error("PLAN_INVALID","任务依赖只能引用同批兄弟节点。")
            for e in n["depends_on"]:
                if e["policy"] not in ("resolved","returned"):raise V3Error("PLAN_INVALID","新任务依赖策略只能是 resolved 或 returned。")
        edges={n["id"]:[e["node_id"] for e in n["depends_on"]] for n in new};visiting=set();visited=set()
        def visit(i):
            if i in visiting:raise V3Error("PLAN_INVALID","兄弟任务依赖中存在循环。")
            if i in visited:return
            visiting.add(i)
            for d in edges[i]:visit(d)
            visiting.remove(i);visited.add(i)
        for i in edges:visit(i)
        parent["children"]=[n["id"] for n in new];parent["planning_state"]="expanded";parent["planning_rationale"]=rationale.strip()
        for n in new:state["nodes"][n["id"]]=n

    def _finish_node(self,db,row,state,node,a,kind):
        outcome=a.get("outcome");summary=a.get("summary");checks=a.get("requirement_results",[]);outputs=a.get("outputs",[]);unresolved=a.get("unresolved",[]);evidence=a.get("evidence_refs",[])
        if outcome not in ("resolved","open") or not isinstance(summary,str) or not summary.strip():raise V3Error("PLAN_INVALID","综合需要 outcome 和 summary。")
        expected=set(node.get("requirement_ids",[]));reported={x.get("requirement_id") for x in checks if isinstance(x,dict)}
        if expected and expected-reported:raise V3Error("PLAN_INVALID","有需求没有逐项验收："+", ".join(sorted(expected-reported)))
        if outcome=="resolved" and (unresolved or any(x.get("status")!="satisfied" for x in checks)):raise V3Error("PLAN_INVALID","仍有未解决需求，不能提交 resolved。")
        if not isinstance(outputs,list) or not isinstance(evidence,list) or not isinstance(unresolved,list):raise V3Error("PLAN_INVALID","outputs、evidence_refs、unresolved 必须为数组。")
        node.update({"execution_status":"finished","outcome":outcome,"activity":"idle","summary":summary.strip(),"outputs":outputs,"unresolved":unresolved,"checks":checks,"evidence_refs":evidence,"finished_at":_now(),"result_revision":int(node.get("result_revision",0))+1})
        result_id=_uid();node["result_id"]=result_id
        db.execute("UPDATE attempts SET lifecycle='finished',finished_at=?,payload_json=? WHERE attempt_id=?",(_now(),_json({"summary":summary,"outcome":outcome,"checks":checks}),node.get("attempt_id")))
        db.execute("INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?,'current')",(result_id,row["task_instance_id"],state["epoch"],node["id"],node["result_revision"],node.get("attempt_id"),outcome,_json({"summary":summary,"outputs":outputs,"unresolved":unresolved,"requirement_results":checks,"evidence_refs":evidence}),_now()))
        for item in evidence:
            if not isinstance(item,dict) or not item.get("id"):raise V3Error("PLAN_INVALID","证据必须有稳定 id 和来源。")
            db.execute("INSERT OR REPLACE INTO evidence VALUES(?,?,?,?,?,?,?)",(item["id"],row["task_instance_id"],state["epoch"],node["id"],result_id,_json(item),_now()))
        if node["id"]==state["root_id"]:state["conclusion"]={"outcome":outcome,"summary":summary.strip(),"result_id":result_id,"unresolved":unresolved}

    def api_task_report(self,a:dict[str,Any])->dict[str,Any]:
        sid=(a.get("scope") or {}).get("session_id") or a.get("session_id");rid=a.get("request_id");event=a.get("event_kind")
        with self._tx() as db:
            replay=self._receipt(db,sid,rid,a)
            if replay:return replay
            row,state=self._scope(db,a);aid=a.get("assignment_id");node=None;assignment=None
            for n in state["nodes"].values():
                if aid in n.get("assignments",{}):node=n;assignment=n["assignments"][aid];break
            if event not in ("dispatch_begin","dispatch_confirmed","dispatch_failed","execution_observed","worker_report","reconcile_result","stop_observed","main_session_transition","lease_renew"):raise V3Error("PLAN_INVALID","未知 report event_kind。")
            if not node and event!="main_session_transition":raise V3Error("SCOPE_STALE","assignment 不属于当前任务轮次。")
            if event=="dispatch_begin":
                if assignment["status"]!="planned":raise V3Error("DISPATCH_UNKNOWN","该派发已开始或已结束，不能重复 begin。")
                dispatch_action=assignment.get("dispatch_action") or {}
                action=db.execute("SELECT * FROM actions WHERE action_id=? AND task_instance_id=? AND epoch=?",(a.get("action_id"),row["task_instance_id"],state["epoch"])).fetchone()
                if not action or action["kind"]!="DISPATCH" or action["node_id"]!=node["id"] or action["lease_token"]!=a.get("lease_token") or action["status"]!="leased" or action["lease_until"]<=_now() or dispatch_action.get("action_id")!=action["action_id"] or dispatch_action.get("lease_token")!=a.get("lease_token"):
                    raise V3Error("LEASE_STALE","派发动作认领已失效或不属于此 assignment。","重新读取并认领派发动作。")
                assignment["status"]="dispatching";assignment["dispatch_began_at"]=_now();node["activity"]="active"
                db.execute("UPDATE assignments SET status='dispatching',payload_json=?,updated_at=? WHERE assignment_id=?",(_json(assignment),_now(),aid))
                db.execute("UPDATE actions SET status='effect_started',updated_at=? WHERE action_id=?",(_now(),action["action_id"]))
            elif event=="dispatch_confirmed":
                provider_ref=a.get("provider_ref")
                if not isinstance(provider_ref,dict) or not isinstance(provider_ref.get("provider"),str) or not isinstance(provider_ref.get("execution_id"),str):raise V3Error("PLAN_INVALID","确认派发必须带真实 provider 与 execution_id。")
                observed=a.get("observed_status","running")
                if observed not in ("queued","running"):raise V3Error("PLAN_INVALID","dispatch_confirmed 只能确认为 queued 或 running。")
                assignment.update({"status":observed,"provider_ref":provider_ref,"observed_at":a.get("observed_at") or _now(),"source_identity":a.get("source_identity")})
                node["execution_status"]="running";node["activity"]="active"
                db.execute("UPDATE assignments SET status=?,provider=?,execution_id=?,payload_json=?,updated_at=? WHERE assignment_id=?",(observed,provider_ref["provider"],provider_ref["execution_id"],_json(assignment),_now(),aid))
                db.execute("UPDATE attempts SET lifecycle='running' WHERE attempt_id=?",(assignment["attempt_id"],))
            elif event=="dispatch_failed":
                confirmed_not_created=a.get("not_created") is True and bool(a.get("evidence"))
                assignment.update({"status":"failed" if confirmed_not_created else "unknown","error":a.get("error"),"reconcile_evidence":a.get("evidence",[])})
                node["execution_status"]="pending" if confirmed_not_created else "running";node["activity"]="idle" if confirmed_not_created else "waiting_external"
                db.execute("UPDATE assignments SET status=?,payload_json=?,updated_at=? WHERE assignment_id=?",(assignment["status"],_json(assignment),_now(),aid))
            elif event=="execution_observed":
                observed=a.get("observed_status")
                if observed not in ("queued","running","finished","failed","cancelled","unknown"):raise V3Error("PLAN_INVALID","执行观察状态无效。")
                prior=assignment.get("observed_status")
                if prior in ("finished","failed","cancelled") and observed in ("queued","running"):assignment.setdefault("late_observations",[]).append(a)
                else:assignment.update({"status":observed,"observed_status":observed,"observed_at":a.get("observed_at") or _now(),"source_identity":a.get("source_identity")})
                if observed in ("finished","failed","cancelled") and assignment.get("report_final"):
                    assignment["status"]=observed
                    if observed!="finished":node["execution_status"]=observed;node["activity"]="idle"
                else:
                    if observed in ("unknown","queued","running"):assignment["status"]=observed;node["execution_status"]="running";node["activity"]="waiting_external" if observed in ("unknown","queued") else "active"
                db.execute("UPDATE assignments SET status=?,payload_json=?,updated_at=? WHERE assignment_id=?",(assignment["status"],_json(assignment),_now(),aid))
            elif event=="worker_report":
                seq=int(a.get("report_seq",0));reports=assignment.setdefault("reports",[])
                if any(x["report_seq"]==seq for x in reports):raise V3Error("IDEMPOTENCY_CONFLICT","report_seq 重复。")
                if seq<=max([x["report_seq"] for x in reports],default=0):raise V3Error("SCOPE_STALE","报告序号必须递增。")
                final=bool(a.get("final"));reports.append({"report_seq":seq,"final":final,"summary":a.get("summary",""),"outputs":a.get("outputs",[]),"evidence_refs":a.get("evidence_refs",[]),"unresolved":a.get("unresolved",[]),"created_at":_now()})
                assignment["report_final"]=final;node["summary"]=a.get("summary",node.get("summary",""));node["outputs"].extend(a.get("outputs",[]));node["evidence_refs"].extend(a.get("evidence_refs",[]));node["unresolved"].extend(a.get("unresolved",[]))
                if final and assignment.get("observed_status") in ("finished","failed","cancelled"):
                    assignment["status"]=assignment["observed_status"]
                    if assignment["status"]!="finished":node["execution_status"]=assignment["status"];node["activity"]="idle"
                db.execute("UPDATE assignments SET status=?,payload_json=?,updated_at=? WHERE assignment_id=?",(assignment["status"],_json(assignment),_now(),aid))
            elif event in ("reconcile_result","stop_observed"):
                observed=a.get("observed_status") or ("not_created" if a.get("found") is False else "unknown")
                if observed not in ("found","not_created","running","queued","finished","failed","cancelled","stopped","unknown"):raise V3Error("PLAN_INVALID","对账结果不明确。")
                if observed in ("not_created",) and not a.get("evidence"):raise V3Error("PLAN_INVALID","判定未创建需要充分对账证据。")
                assignment.setdefault("reconciliations",[]).append({"observed_status":observed,"evidence":a.get("evidence",[]),"source_identity":a.get("source_identity"),"created_at":_now()})
                mapping={"found":"unknown","not_created":"failed","stopped":"cancelled"};assignment["status"]=mapping.get(observed,observed)
                if assignment["status"] in ("failed","cancelled"):node["activity"]="idle"
                else:node["execution_status"]="running";node["activity"]="waiting_external"
                db.execute("UPDATE assignments SET status=?,payload_json=?,updated_at=? WHERE assignment_id=?",(assignment["status"],_json(assignment),_now(),aid))
            elif event=="lease_renew":
                action=db.execute("SELECT * FROM actions WHERE action_id=? AND lease_token=?",(a.get("action_id"),a.get("lease_token"))).fetchone()
                if not action or action["status"] not in ("leased","effect_started"):raise V3Error("LEASE_STALE","动作 lease 已过期。")
                until=datetime.fromtimestamp(datetime.now().astimezone().timestamp()+60).astimezone().isoformat();db.execute("UPDATE actions SET lease_until=?,updated_at=? WHERE action_id=?",(until,_now(),action["action_id"]))
            else:
                # Fast path still records a real observed start and result under one transaction.
                target=state["nodes"].get(a.get("node_id"));
                if not target or not self._node_ready(state,target) or target.get("planning_state")!="executable":raise V3Error("NODE_NOT_READY","主会话快速路径只能开始已规划且依赖满足的叶任务。")
                assignment_meta=a.get("assignment")
                if not isinstance(assignment_meta,dict) or assignment_meta.get("execution_mode")!="main_session" or not assignment_meta.get("model") or not assignment_meta.get("reasoning_effort"):raise V3Error("PLAN_INVALID","快速路径需记录主会话实际执行身份。")
                attempt_id=_uid();target["attempt"]+=1;target["attempt_id"]=attempt_id;target["assignment"]=assignment_meta;target["execution_status"]="running";target["activity"]="active";target["started_at"]=_now()
                db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,'running',?,?,?,NULL)",(attempt_id,row["task_instance_id"],state["epoch"],target["id"],"execution",row["revision"],_json(assignment_meta),_now()))
                if a.get("outcome"):
                    a={**a,"attempt_id":attempt_id};self._finish_node(db,row,state,target,a,"main_session_transition")
                    db.execute("UPDATE attempts SET lifecycle='finished',finished_at=? WHERE attempt_id=?",(_now(),attempt_id))
                result_data={"attempt_id":attempt_id,"node_id":target["id"]}
            row=self._save(db,row,state,"report."+event);progress=self._progress(db,row,state);response={"api_version":3,"accepted":True,"session_id":sid,"task_instance_id":row["task_instance_id"],"epoch":state["epoch"],"revision":row["revision"],"report":result_data if event=="main_session_transition" else {"event_kind":event,"assignment_id":aid},"progress":progress}
            self._store_receipt(db,sid,rid,a,response);return {"content":[{"type":"text","text":_json(response)}],"structuredContent":response}

    def api_task_control(self,a:dict[str,Any])->dict[str,Any]:
        sid=(a.get("scope") or {}).get("session_id") or a.get("session_id");rid=a.get("request_id")
        with self._tx() as db:
            replay=self._receipt(db,sid,rid,a)
            if replay:return replay
            row,state=self._scope(db,a);op=a.get("operation")
            if op=="pause":state["control_state"]="pause_requested"
            elif op=="resume":
                if state["control_state"] not in ("paused","pause_requested","active"):raise V3Error("PLAN_INVALID","当前任务状态不能继续。")
                state["control_state"]="active"
            elif op=="cancel":state["control_state"]="cancel_requested"
            elif op=="stop_confirmed":
                if state["control_state"]!="cancel_requested":raise V3Error("PLAN_INVALID","没有待确认的停止请求。")
                state["control_state"]="cancelled"
            elif op=="pause_confirmed":state["control_state"]="paused"
            elif op=="answer_input":
                pending=state.get("pending_input")
                if not pending or pending.get("input_request_id")!=a.get("input_request_id") or pending.get("version")!=a.get("input_version"):raise V3Error("SCOPE_STALE","输入请求已过期。")
                if "answer" not in a:raise V3Error("PLAN_INVALID","需要明确提交 answer。")
                pending["answer"]={"value":a["answer"],"source":a.get("source","user"),"answered_at":_now()};pending["status"]="answered"
            elif op=="reopen_branch":
                target=state["nodes"].get(a.get("node_id"))
                if not target or target.get("execution_status") not in ("finished","failed"):raise V3Error("PLAN_INVALID","只能重开已结束任务项。")
                descendants=self._descendants(state,target["id"]);affected={target["id"],*descendants}
                changed=True
                while changed:
                    changed=False
                    for n in state["nodes"].values():
                        if n["id"] not in affected and any((e.get("node_id") if isinstance(e,dict) else e) in affected for e in n.get("depends_on",[])):
                            affected.add(n["id"]);affected.update(self._descendants(state,n["id"]));changed=True
                if any(state["nodes"][i].get("execution_status")=="running" for i in affected):raise V3Error("RESOURCE_CONFLICT","受影响分支有在途执行。")
                for i in affected:
                    n=state["nodes"][i]
                    if n.get("result_id"):db.execute("UPDATE results SET validity='stale' WHERE result_id=?",(n["result_id"],))
                    n.update({"execution_status":"pending","outcome":None,"summary":"","outputs":[],"checks":[],"evidence_refs":[],"unresolved":[],"activity":"idle","result_id":None})
                state["reopened_nodes"]=sorted(affected)
            else:raise V3Error("PLAN_INVALID","不支持的任务控制操作。")
            row=self._save(db,row,state,"control."+str(op));progress=self._progress(db,row,state);response={"api_version":3,"session_id":sid,"task_instance_id":row["task_instance_id"],"epoch":state["epoch"],"revision":row["revision"],"operation":op,"progress":progress}
            self._store_receipt(db,sid,rid,a,response);return {"content":[{"type":"text","text":_json(response)}],"structuredContent":response}

    @staticmethod
    def _descendants(state:dict[str,Any],node_id:str)->set[str]:
        out=set();stack=list(state["nodes"][node_id].get("children",[]))
        while stack:
            item=stack.pop()
            if item in out:continue
            out.add(item);stack.extend(state["nodes"][item].get("children",[]))
        return out

    def api_task_history(self,a:dict[str,Any])->dict[str,Any]:
        sid=a.get("session_id") or (a.get("scope") or {}).get("session_id");kind=a.get("type");limit=min(max(int(a.get("limit",20)),1),100);cursor=a.get("cursor");offset=0
        if cursor:
            try:
                raw=base64.urlsafe_b64decode(cursor.encode()+b"="*(-len(cursor)%4)).decode();data=json.loads(raw)
                if data["session_id"]!=sid or data["type"]!=kind or data.get("task_instance_id")!=a.get("task_instance_id"):raise ValueError
                offset=int(data["offset"])
            except Exception:raise V3Error("SCOPE_STALE","历史游标过期或范围不匹配。","从第一页重新读取。")
        with self._connect() as db:
            self._session(db,sid)
            if kind=="task":
                rows=db.execute("SELECT * FROM task_instances WHERE session_id=? AND archived_at IS NOT NULL ORDER BY generation DESC LIMIT ? OFFSET ?",(sid,limit+1,offset)).fetchall();items=[]
                for r in rows:
                    st=_decode(r["state_json"],{});items.append({"task_instance_id":r["task_instance_id"],"generation":r["generation"],"title":r["title"],"status":r["status"],"archived_at":r["archived_at"],"progress":self._progress(db,r,st)})
            elif kind=="epoch":
                tid=a.get("task_instance_id");task=db.execute("SELECT epoch FROM task_instances WHERE task_instance_id=? AND session_id=?",(tid,sid)).fetchone()
                if not task:raise V3Error("SCOPE_STALE","历史计划所属任务不在此会话中。")
                rows=db.execute("SELECT epoch,snapshot_json,closed_at FROM epochs WHERE task_instance_id=? AND epoch<? ORDER BY epoch DESC LIMIT ? OFFSET ?",(tid,task["epoch"],limit+1,offset)).fetchall();items=[{"epoch":r["epoch"],"closed_at":r["closed_at"],"snapshot":_decode(r["snapshot_json"],{})} for r in rows]
            elif kind=="attempt":
                tid=a.get("task_instance_id");node=a.get("node_id")
                if not db.execute("SELECT 1 FROM task_instances WHERE task_instance_id=? AND session_id=?",(tid,sid)).fetchone():raise V3Error("SCOPE_STALE","历史执行所属任务不在此会话中。")
                rows=db.execute("SELECT * FROM attempts WHERE task_instance_id=? AND node_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",(tid,node,limit+1,offset)).fetchall();items=[{**dict(r),"payload":_decode(r["payload_json"],{})} for r in rows]
            else:raise V3Error("PLAN_INVALID","历史 type 必须是 task、epoch 或 attempt。")
            more=len(items)>limit;items=items[:limit];next_cursor=None
            if more:next_cursor=base64.urlsafe_b64encode(_json({"session_id":sid,"type":kind,"task_instance_id":a.get("task_instance_id"),"offset":offset+limit}).encode()).decode().rstrip("=")
            result={"api_version":3,"session_id":sid,"type":kind,"items":items,"next_cursor":next_cursor};return {"content":[{"type":"text","text":_json(result)}],"structuredContent":result}

    def has_session(self,sid:str)->bool:
        with self._connect() as db:return db.execute("SELECT 1 FROM sessions WHERE session_id=?",(sid,)).fetchone() is not None

    def read_progress(self,sid:str)->dict[str,Any]|None:
        with self._connect() as db:
            session=db.execute("SELECT * FROM sessions WHERE session_id=?",(sid,)).fetchone()
            if not session or not session["current_task_id"]:return None
            row,state=self._task(db,sid);return self._progress(db,row,state)

    def clear_session(self,sid:str)->bool:
        with self._tx() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE session_id=?",(sid,)).fetchone():return False
            task_ids=[r[0] for r in db.execute("SELECT task_instance_id FROM task_instances WHERE session_id=?",(sid,)).fetchall()]
            for tid in task_ids:
                db.execute("DELETE FROM evidence WHERE task_instance_id=?",(tid,))
                db.execute("DELETE FROM results WHERE task_instance_id=?",(tid,))
                db.execute("DELETE FROM assignments WHERE task_instance_id=?",(tid,))
                db.execute("DELETE FROM attempts WHERE task_instance_id=?",(tid,))
                db.execute("DELETE FROM actions WHERE task_instance_id=?",(tid,))
                db.execute("DELETE FROM epochs WHERE task_instance_id=?",(tid,))
            db.execute("DELETE FROM events WHERE session_id=?",(sid,))
            db.execute("DELETE FROM request_receipts WHERE session_id=?",(sid,))
            db.execute("DELETE FROM views WHERE session_id=?",(sid,))
            db.execute("DELETE FROM task_instances WHERE session_id=?",(sid,))
            db.execute("DELETE FROM sessions WHERE session_id=?",(sid,));return True

    def register_view(self,sid:str,view_id:str)->dict[str,Any]:
        with self._tx() as db:
            self._session(db,sid);old=db.execute("SELECT * FROM views WHERE session_id=?",(sid,)).fetchone()
            if old and old["view_id"]==view_id:return {"session_id":sid,"view_id":view_id,"view_seq":old["view_seq"],"status":"current"}
            seq=(old["view_seq"]+1) if old else 1;db.execute("INSERT INTO views VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET view_seq=excluded.view_seq,view_id=excluded.view_id,updated_at=excluded.updated_at",(sid,seq,view_id,_now()))
            return {"session_id":sid,"view_id":view_id,"view_seq":seq,"status":"current"}

    def check_view(self,sid:str,view_id:str)->dict[str,Any]:
        with self._connect() as db:
            self._session(db,sid);r=db.execute("SELECT * FROM views WHERE session_id=?",(sid,)).fetchone();status="unregistered" if not r else "current" if r["view_id"]==view_id else "superseded"
            return {"session_id":sid,"view_id":view_id,"status":status,"view_seq":r["view_seq"] if r else 0}

