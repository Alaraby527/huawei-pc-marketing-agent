#!/usr/bin/env python3
"""No UI or LLM key required. Live ima CLI and one-file handoff export."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.ima import ImaError
from app.local_kb import default_client
from app.intake import Intake
from app.retrieval import guide, get_asset


def write_private(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        stream.write(data)
    path.chmod(0o600)


async def main():
    parser=argparse.ArgumentParser(description='华为PC前段导购：需求澄清、ima事实和图片交接')
    parser.add_argument('command',choices=['libraries','connection','run','interactive'])
    parser.add_argument('--kb',help='知识库精确名称；默认读取IMA_KB_NAME，未设置时为赛事指定库')
    parser.add_argument('--task',type=Path,help='任务JSON；run命令必填')
    parser.add_argument('--output',type=Path,default=ROOT/'.runtime/last-handoff.json')
    args=parser.parse_args()
    if args.kb: os.environ['IMA_KB_NAME']=args.kb
    try:
        if args.command=='libraries':
            result=await default_client().libraries()
        elif args.command=='connection':
            result=await default_client().connect()
        else:
            if args.command=='interactive':
                print('输入任务，回车采用示例值。产品必须包含版本后缀；泛化型号会返回候选。')
                values={}
                for field,label,default in [
                    ('product_query','具体型号','HUAWEI MateBook 14（鸿蒙版）'),
                    ('audience','目标人群','大学新生'),('scenario','使用/营销场景','开学购机，课堂学习与校园携带'),
                    ('objective','内容目标','邀请到店体验'),('platform','平台：xiaohongshu/wechat_article/moments/poster','moments'),
                    ('instructions','额外要求','')]:
                    values[field]=input(f'{label} [{default}]：').strip() or default
            else:
                if not args.task: parser.error('run需要 --task 文件路径')
                values=json.loads(args.task.read_text(encoding='utf-8'))
            result=await guide(Intake.model_validate(values))
            bundle=result.get('materials')
            # CLI runs terminate: export retrieved images beside the handoff instead of dead server URLs.
            # This is a local handoff only. HTTP clients use live server asset URLs instead.
            if bundle:
                bundle['issues']=[x for x in bundle['issues'] if not x.startswith('图片地址为当前服务')]
                bundle['issues'].append('CLI已导出本地图片文件；接收方需复制assets并映射路径，不是云端公开URL')
                result['data_state']['reasons']=bundle['issues']
                for item in bundle['images']:
                    token=item['access_url'].rsplit('/',1)[-1]
                    asset=get_asset(token)
                    if asset:
                        extension={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}[asset[1]]
                        image_path=args.output.resolve().parent/'assets'/(item['image_id']+extension)
                        image_path.parent.mkdir(parents=True,exist_ok=True)
                        image_path.write_bytes(asset[0]); image_path.chmod(0o600)
                        item['access_url']=str(image_path)
                result['delivery']={'mode':'local_files','note':'将JSON与assets目录一起交接；接收方映射为自己的素材读取地址。不要公开发布整库。'}
            write_private(args.output,json.dumps(result,ensure_ascii=False,indent=2))
            print('已保存：'+str(args.output.resolve()),file=sys.stderr)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ImaError, ValueError, OSError) as error:
        # ValidationError contains user input; keep CLI errors non-echoing as well.
        print(json.dumps({'status':'unavailable','reason':str(error) if isinstance(error,ImaError) else '任务格式、配置或文件读写错误，请检查路径及必填字段'},ensure_ascii=False))
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
