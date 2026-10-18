#!/usr/bin/env python3
"""Actual multi-turn dialogue; no sample values are filled on Enter."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.conversation import ConversationService, Turn, DialogueError
from app.local_kb import default_client
from app.retrieval import get_asset
from scripts.guide import write_private


def export_result(result,path):
    handoff=result.get('handoff')
    if handoff:
        for image in handoff['images']:
            url=image.get('access_url','')
            # Re-exporting an already-exported handoff must not treat local paths as dead tokens.
            if url and not url.startswith('/api/v1/assets/') and Path(url).is_file():
                continue
            content=get_asset(url.rsplit('/',1)[-1]) if url else None
            if not content:
                handoff['data_state'].update(status='unavailable',can_generate=False)
                result['can_generate']=False
                handoff['data_state']['reasons'].append('导出时图片失效，需要重新检索')
                continue
            suffix={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}.get(content[1],'.bin')
            dest=path.parent/'assets'/(image['image_id']+suffix)
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(content[0]); dest.chmod(0o600)
            image['access_url']=str(dest.resolve())
            for nested in handoff['materials']['images']:
                if nested['image_id']==image['image_id']: nested['access_url']=str(dest.resolve())
        handoff['delivery']={'mode':'local_files','note':'JSON与assets目录一起交接；迁移后重映射路径并核验SHA256'}
    write_private(path,json.dumps(result,ensure_ascii=False,indent=2))


def display(result):
    print('\n助手：'+result['message'])
    for q in result['questions']: print('  · '+q['question'])
    for i,c in enumerate(result['candidates'],1):
        price=c['current_price']
        print(f"  {i}. {c['model_name']}｜{c['eligibility']}｜预算：{c['budget_status']}")
        if price: print(f"     {price['configuration']}；当前核验价 ¥{price['amount']:g}")
        for reason in c['reasons'][:3]: print('     '+reason['dimension']+'：'+reason['reason'])
        for gap in c['unknowns']: print('     待核实：'+gap)
    for issue in result['issues']: print('  注意：'+issue)
    for rejected in result['rejected']: print('  已排除：'+rejected['model_name']+'；'+'；'.join(rejected['reasons']))
    if result.get('handoff'):
        h=result['handoff']
        print(f"  下游交接：{len(h['facts'])}组事实，{len(h['images'])}张核验图片；可生成={result['can_generate']}；赛事来源就绪={result['competition_ready']}")


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--kb',default=os.getenv('IMA_KB_NAME','Alaraby的知识库'))
    parser.add_argument('--output',type=Path,default=ROOT/'.runtime/conversation-handoff.json')
    args=parser.parse_args(); os.environ['IMA_KB_NAME']=args.kb
    service=ConversationService(default_client)
    print('华为PC · 门店/运营前段助手（真实知识库：'+args.kb+'）')
    print('请描述客户或目标客群，可以先提供一条信息。命令：跳过、选1、刷新、导出、退出。')
    print('硬性条件缺证据时会停在待核实；历史价格不会自动当成当前报价。')
    result=await service.turn(Turn()); display(result)
    while True:
        try: message=await asyncio.to_thread(input,'\n门店/运营 > ')
        except (EOFError,KeyboardInterrupt): break
        message=message.strip()
        if message in ['退出','exit','quit']: break
        if not message: continue
        try:
            result=await service.turn(Turn(session_id=result['session_id'],expected_revision=result['revision'],message=message,
                action='refresh' if message=='刷新' else 'export' if message=='导出' else 'continue'))
            display(result); export_result(result,args.output)
            session=service.sessions[result['session_id']]
            write_private(args.output.parent/'conversation-transcript.json',json.dumps(session.history,ensure_ascii=False,indent=2))
            if result.get('handoff'): print('  已保存：'+str(args.output.resolve()))
        except DialogueError as error: print('会话不可用：'+str(error.message))
        except ValueError: print('信息格式无法确认，请明确预算或使用用途；不会自动填默认值。')
    print('本次会话结束；已有输出位于 '+str(args.output.parent.resolve()))


if __name__=='__main__': asyncio.run(main())
