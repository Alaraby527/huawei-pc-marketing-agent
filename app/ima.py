"""Read-only official ima Skill adapter. Never returns credentials or access headers."""
import asyncio
import json
import os
import shutil
import re
import urllib.request
import urllib.parse
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / 'vendor/ima-skill/ima_api.cjs'
OFFICIAL_KB = '任德耀新建华为PC知识库'

class ImaError(Exception):
    pass

def configured():
    root = Path.home() / '.config/ima'
    return bool((os.getenv('IMA_OPENAPI_CLIENTID') or os.getenv('IMA_CLIENT_ID') or (root/'client_id').is_file()) and
                (os.getenv('IMA_OPENAPI_APIKEY') or os.getenv('IMA_API_KEY') or (root/'api_key').is_file()))

def node_binary():
    return os.getenv('IMA_NODE_BIN') or shutil.which('node') or next((str(p) for p in [
        Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node',
        Path('/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node')
    ] if p.is_file()), None)

class ImaClient:
    def __init__(self, kb_name=None):
        self.trace = []
        self.kb_name = kb_name or os.getenv('IMA_KB_NAME', OFFICIAL_KB)
        self.kb_id = None

    async def call(self, endpoint, body):
        if endpoint not in {'search_knowledge_base','get_knowledge_list','search_knowledge','get_media_info'}:
            raise ImaError('此适配器只允许只读知识库接口')
        if not configured():
            raise ImaError('未配置IMA凭证，请在服务端环境变量或本机 ~/.config/ima 中配置')
        node = node_binary()
        if not node:
            raise ImaError('未找到Node 18+，请安装或设置IMA_NODE_BIN')
        if not SCRIPT.is_file():
            raise ImaError('缺少官方ima_api.cjs；部署时需一并复制vendor目录')
        env = os.environ.copy()
        # Official update state stays outside source. baseUrl cannot be redirected by environment.
        env.pop('IMA_BASE_URL', None)
        env['IMA_LAST_CHECK_FILE'] = str(Path(os.getenv('TMPDIR','/tmp'))/'huawei-ima-last-update-check')
        try:
            proc = await asyncio.create_subprocess_exec(node, str(SCRIPT), 'openapi/wiki/v1/'+endpoint,
                        json.dumps(body,ensure_ascii=False), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError:
            raise ImaError('Node运行时无法启动，请检查IMA_NODE_BIN路径与执行权限') from None
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            proc.kill()
            await proc.communicate()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise ImaError('IMA调用超时，请稍后重试')
        if proc.returncode:
            try: code = json.loads(err).get('code')
            except (ValueError,AttributeError): code = None
            raise ImaError('官方IMA Skill需要更新后重试' if code == -200 else 'IMA调用失败，请检查凭证、Node运行时或网络')
        try: result = json.loads(out)
        except ValueError: raise ImaError('IMA返回非JSON响应')
        if result.get('code') != 0:
            # Do not reflect arbitrary upstream text that might contain secret-bearing URLs.
            raise ImaError('IMA接口错误，code='+str(result.get('code'))+'；请核对权限、参数或配额')
        self.trace.append({'step':endpoint,'status':'success'})
        return result.get('data') or {}

    async def pages(self, endpoint, body, key, limit=60):
        items, cursor, seen = [], '', set()
        for _ in range(limit):
            data = await self.call(endpoint,{**body,'cursor':cursor})
            items.extend(data.get(key) or [])
            if len(items)>3000:
                raise ImaError('单目录超过3000条安全上限，请按产品分目录整理')
            cursor = data.get('next_cursor')
            if data.get('is_end',True):
                return items
            if not cursor or cursor in seen:
                raise ImaError('IMA分页游标缺失或重复，不能将不完整目录当完整结果')
            seen.add(cursor)
        raise ImaError('目录分页超过本次读取上限，请缩小检索范围')

    async def connect(self):
        items = await self.pages('search_knowledge_base',{'query':self.kb_name,'limit':20},'info_list')
        found = [x for x in items if (x.get('kb_name') or x.get('name')) == self.kb_name]
        if len(found) != 1:
            raise ImaError('当前账号找不到唯一的知识库「'+self.kb_name+'」，请确认同一账号已加入且有读取权限')
        self.kb_id = found[0].get('kb_id') or found[0].get('id')
        if not self.kb_id: raise ImaError('知识库响应缺少标识')
        return {'knowledge_base_name':self.kb_name,'connection':'verified',
                'official_library':self.kb_name==OFFICIAL_KB,
                'account_verification':'已用当前凭证实际调用；API未返回个人账号身份，不冒充已完成账号实名识别'}

    async def search(self, query):
        return await self.pages('search_knowledge',{'query':query,'knowledge_base_id':self.kb_id},'info_list')

    async def libraries(self):
        rows = await self.pages('search_knowledge_base', {'query':'','limit':20}, 'info_list')
        return [{'name':x.get('kb_name') or x.get('name'),
                 'description':x.get('description',''), 'role':x.get('role_type',''),
                 'type':x.get('base_type','')} for x in rows]

    async def media(self, media_id):
        return await self.call('get_media_info', {'media_id':media_id})

    async def read_text(self, entry):
        data = await self.media(entry['media_id'])
        csv_file=data.get('media_type')==5 and entry.get('title','').lower().endswith('.csv')
        if data.get('media_type') not in (7,13) and not csv_file:
            raise ImaError('当前自动提取Markdown/TXT和CSV索引；其他类型请在ima查看并补充可读材料')
        info = data.get('url_info') or {}
        if not info.get('url'):
            raise ImaError('原文没有可读取链接，请在ima客户端查看')
        raw, _ = await asyncio.to_thread(read_resource, info['url'], info.get('headers'), 2_000_000)
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ImaError('原文不是UTF-8编码，停止提取以避免参数乱码')
        self.trace.append({'step':'read_original','status':'success','title':entry.get('title','')})
        return content

    async def catalog(self):
        # Breadth-first directory fallback, bounded. Never modifies or mirrors library content.
        queue, visited, items = [(None,'')], set(), []
        while queue:
            folder,path=queue.pop(0)
            if folder in visited: continue
            visited.add(folder)
            if len(visited)>35: raise ImaError('目录层级过多，请缩小型号范围')
            body={'knowledge_base_id':self.kb_id,'limit':50}
            if folder: body['folder_id']=folder
            entries=await self.pages('get_knowledge_list',body,'knowledge_list')
            for x in entries:
                title=x.get('title') or x.get('name') or ''
                is_folder=x.get('folder_id') or x.get('media_type') in (0,10) or ('file_number' in x)
                if is_folder:
                    fid=x.get('folder_id') or x.get('media_id')
                    if fid: queue.append((fid,path+'/'+title))
                else: items.append({**x,'_path':path+'/'+title})
        return list({x['media_id']:x for x in items if x.get('media_id')}.values())


def safe_resource_url(url):
    """Only fetch known ima storage / official Huawei assets, never arbitrary task URLs."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None,443) and
                (host == 'ima.qq.com' or host.endswith('.ima.myqcloud.com')
                 or host == 'consumer.huawei.com' or re.fullmatch(r'res\d*\.vmallres\.com',host) is not None))
    except (ValueError,TypeError):
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_resource(url, headers=None, max_bytes=12_000_000):
    if not safe_resource_url(url):
        raise ImaError('素材链接不在受支持的ima/华为资源域名内，请人工核对来源')
    # Never forward ima account credentials. Resource signatures stay on their original host.
    safe_headers = {k:v for k,v in (headers or {}).items()
                    if k.lower().startswith('x-ima-') or k.lower() == 'authorization'}
    if urllib.parse.urlsplit(url).hostname not in {'ima.qq.com','pkb.ima.myqcloud.com'}:
        # API-provided resource authentication must not cross over to a linked Huawei page.
        safe_headers = {}
    try:
        req = urllib.request.Request(url, headers=safe_headers)
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as response:
            body = response.read(max_bytes+1)
            content_type = response.headers.get('content-type','').split(';')[0].lower()
        if len(body)>max_bytes:
            raise ImaError('原文或素材超过单次读取上限')
        return body, content_type
    except ImaError:
        raise
    except Exception:
        raise ImaError('ima原文/图片读取失败或链接已过期，请重新检索；不使用未验证链接兜底') from None


def image_type(body):
    if body.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if body.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if body[:4] == b'RIFF' and body[8:12] == b'WEBP': return 'image/webp'
    return None
