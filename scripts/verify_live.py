#!/usr/bin/env python3
"""Opt-in genuine read-only ima smoke test. Exports private local evidence, never keys."""
import asyncio
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from app.conversation import ConversationService, Turn
from app.local_kb import default_client
from scripts.chat import export_result
from scripts.guide import write_private

async def main():
    service=ConversationService(default_client); records=[]
    result=None
    for message in ['大学新生','预算不限','主要用来写论文、上网课，便携优先','选1','门店朋友圈，希望邀请到店体验']:
        result=await service.turn(Turn(session_id=result['session_id'] if result else None,message=message))
        print(json.dumps({'input':message,'status':result['status'],'questions':result['questions'],
              'candidate_count':len(result['candidates']),'can_generate':result['can_generate'],'issues':result['issues']},ensure_ascii=False),flush=True)
        records.append({'input':message,'output':result})
        if result['status']=='unavailable': break
    export_result(result,ROOT/'.runtime/live-conversation-handoff.json')
    if result.get('handoff'):
        finite=await service.turn(Turn(session_id=result['session_id'],message='预算6000以内'))
        records.append({'input':'预算6000以内','output':finite})
        if finite['candidates']:
            finite=await service.turn(Turn(session_id=finite['session_id'],message='选1'))
            records.append({'input':'选1','output':finite})
        print(json.dumps({'finite_budget_status':finite['status'],'can_generate':finite['can_generate'],'issues':finite['issues']},ensure_ascii=False),flush=True)
        export_result(finite,ROOT/'.runtime/live-finite-budget.json')
    write_private(ROOT/'.runtime/live-conversation-record.json',json.dumps(records,ensure_ascii=False,indent=2))
    return 0 if result.get('can_generate') else 1

if __name__=='__main__': raise SystemExit(asyncio.run(main()))
