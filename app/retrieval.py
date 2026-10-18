"""Part A: live ima evidence -> versioned materials; no model invents product facts."""
import asyncio
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from urllib.parse import urlsplit

from .contracts import Task, MaterialBundle, Product, Fact, Source, ImageAsset
from .ima import ImaClient, ImaError, OFFICIAL_KB, read_resource, image_type
from .local_kb import default_client
from .intake import Intake, strategy
from .platforms import PLATFORM_RULES


def normalize(text):
    text = text.casefold().replace('华为', '').replace('huawei', '')
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text)


def title_model(title):
    title = re.sub(r'^(?:MC-\d+[_-]|\d+[-_])', '', title)
    title = re.sub(r'\.(?:md|txt)$', '', title, flags=re.I)
    title = re.split(r'\s*[-—]\s*(?:产品事实|价格卡)', title)[0]
    return title.strip() if re.match(r'(?:HUAWEI|华为)\s*(?:MateBook|MatePad|擎云)', title, re.I) else None


def model_from_document(text):
    heading = re.search(r'^#\s+(.+)', text, re.M)
    return title_model(heading.group(1)) if heading else None


FACT_SECTIONS = {'操作系统','尺寸与重量','屏幕','处理器','存储','内存','无线连接',
                 '电池','接口','键盘和触控板','摄像头','音频','包装清单','产品参数',
                 '规格参数','核心参数','功能','适用条件','限制条件'}


@dataclass
class Document:
    entry: dict
    text: str
    model: str

    @property
    def aliases(self):
        # Model slugs come from this document's official links, not a similar product.
        aliases = set()
        source=re.search(r'(?ms)^## 来源\s*\n(.*?)(?=^## |\Z)',self.text)
        if not source:
            return aliases
        for url in re.findall(r'https://consumer\.huawei\.com/[^\s)>]+', source.group(1)):
            for part in urlsplit(url.rstrip('/')).path.split('/'):
                if part.startswith('matebook-'):
                    aliases.add(part.removesuffix('-version'))
        return aliases if len(aliases)==1 else set()


def extract_facts(doc, product_id, source_kind='ima'):
    """Keep whole specification sections and footnotes, never split a qualifier away."""
    prefix = doc.text.split('\n## ',1)[0]
    conditions = '；'.join(line.strip('> ').strip() for line in prefix.splitlines() if line.startswith('>'))
    conditions = conditions or '型号级资料；不同销售版本可能不同，不推断容量、颜色、键盘与屏幕的组合。'
    facts, issues = [], []
    for section in re.split(r'(?m)^## +', doc.text)[1:]:
        heading, _, content = section.partition('\n')
        heading = heading.strip()
        if heading not in FACT_SECTIONS:
            continue
        content = content.split('即将离开consumer.huawei.com')[0].strip()
        if not content:
            continue
        if len(content)>1700 or len(conditions)>900:
            issues.append('规格章节过长需人工拆分并保留脚注：'+heading)
            continue
        excerpt = '## '+heading+'\n\n'+content
        fingerprint = hashlib.sha256((doc.entry['media_id']+heading+content).encode()).hexdigest()[:14]
        facts.append(Fact(fact_id='F-'+fingerprint, product_id=product_id,
            text=heading+'：\n'+content, conditions=conditions,
            source=Source(kind=source_kind, title=doc.entry.get('title') or doc.model,
                          locator=doc.model+' / '+heading+' / evidence-'+fingerprint,
                          excerpt=excerpt)))
    return facts, issues


def conflicts(facts):
    by_section = {}
    result = []
    for fact in facts:
        section = fact.text.partition('：')[0]
        value = re.sub(r'\s+', '', fact.text).casefold()
        if section in by_section and by_section[section] != value:
            result.append('同型号「'+section+'」存在不同原文，停止交付并人工确认配置/版本')
        by_section[section] = value
    return result


# Capability URLs keep signatures out of handoffs. Restart or TTL expires images.
ASSETS = {}
ASSET_TTL = 1800


def store_asset(body, mime):
    now = time.monotonic()
    for token in list(ASSETS):
        if ASSETS[token][2] < now:
            del ASSETS[token]
    while ASSETS and (len(ASSETS)>=60 or sum(len(v[0]) for v in ASSETS.values())+len(body)>80_000_000):
        del ASSETS[next(iter(ASSETS))]
    token = secrets.token_urlsafe(24)
    ASSETS[token] = (body,mime,now+ASSET_TTL)
    return '/api/v1/assets/'+token


def get_asset(token):
    asset = ASSETS.get(token)
    if asset and asset[2] >= time.monotonic():
        return asset[0],asset[1]
    ASSETS.pop(token,None)
    return None


def image_matches(entry, doc):
    title = entry.get('title','').casefold()
    return any(re.search(r'(?:^|_)'+re.escape(alias)+r'_', title) for alias in doc.aliases)


def image_score(entry, priorities):
    title=entry.get('title','')
    score = 30 if any(x in title for x in ['主视觉','正面产品','精致产品外观']) else 0
    for i,section in enumerate(priorities):
        words={'尺寸与重量':['轻薄','侧面'], '电池':['续航','快充'], '屏幕':['屏幕','OLED'],
               '接口':['接口'],'摄像头':['摄像','会议'],'处理器':['性能']}.get(section,[section])
        if any(word in title for word in words): score+=20-i
    return score


def input_risks(task):
    # Negative user requirements already prohibit the claim; do not report them as demands.
    clauses=re.split(r'[，。；\n]',task.instructions+'；'+task.objective)
    text=' '.join(x for x in clauses if not re.match(r'^\s*(?:不|禁止|避免|勿)',x))
    risks=[]
    for phrase in ['全网最低','最好','第一','保证不卡','永久免费','秒杀所有','吊打','绝对']:
        if phrase in text:
            risks.append({'type':'compliance','request':phrase,'action':'omit',
                          'reason':'不支持绝对化宣传、虚假承诺或无依据比较'})
    if any(x in text for x in ['价格','补贴','优惠','赠品','库存','到手价','国补']) or re.search(r'\d+\s*元',text):
        risks.append({'type':'dynamic_claim','action':'omit_until_verified',
                      'reason':'未取得当日、同SKU、同地区权益证据，禁止补写价格/补贴/赠品/库存'})
    return risks


async def _prepare_materials(task: Task, client=None):
    client = client or default_client()
    started = datetime.now(timezone.utc).isoformat()
    request_id = 'guide-'+secrets.token_hex(10)
    issues=[]
    context=strategy(task)
    result={'request_id':request_id,'strategy':context,'risks':input_risks(task),'candidates':[],
            'questions':[], 'connection':{}, 'trace':client.trace,
            'data_state':{'read_at':started,'status':'unavailable','reasons':[]},
            'competition_ready':False,'can_generate':False,'can_publish':False}

    def finish(status, product=None, facts=None, images=None):
        bundle=MaterialBundle(request_id=request_id,data_mode='live',status=status,task=task,
                              product=product,facts=facts or [],images=images or [],issues=list(dict.fromkeys(issues)))
        result['materials']=bundle.model_dump(mode='json')
        result['data_state'].update(status=status,reasons=bundle.issues)
        result['can_generate']=status=='ready'
        result['competition_ready']=status=='ready' and result['connection'].get('official_library',False)
        result['trace']=list(client.trace)
        return result

    try:
        result['connection']=await client.connect()
        if client.kb_name != OFFICIAL_KB:
            issues.append('个人知识库真实联调；不是主办方指定共享库，不代表通过赛事来源要求')
        rows=await client.search(task.product_query+' '+task.scenario)
        if not rows:
            client.trace.append({'step':'directory_fallback','status':'started'})
            rows=await client.catalog()
        else:
            precise=await client.search(task.product_query)
            rows=list({x['media_id']:x for x in [*rows,*precise] if x.get('media_id')}.values())
        text_entries=[x for x in rows if x.get('media_type') in (7,13,20) or x.get('title','').endswith(('.md','.txt'))]
        names=sorted({m for x in text_entries if (m:=title_model(x.get('title','')))})
        # Full fact files can have generic filenames. Read and validate their own model headings.
        extra=await client.search('产品事实')
        primary=[x for x in [*text_entries,*extra] if '产品事实' in x.get('title','')]
        if not primary:
            client.trace.append({'step':'directory_fallback','status':'started'})
            catalog=await client.catalog()
            primary=[x for x in catalog if '产品事实' in x.get('title','')]
        primary=list({x['media_id']:x for x in primary if x.get('media_id')}.values())
        if len(primary)>16:
            narrowed=[x for x in primary if normalize(task.product_query) in normalize(x.get('_path',x.get('title','')))]
            if not narrowed:
                issues.append('产品事实候选过多，请提供精确型号或整理型号目录后重试')
                return finish('needs_input')
            primary=narrowed
        docs=[]
        for entry in primary[:16]:
            original=await client.read_text(entry)
            model=model_from_document(original)
            if model:
                docs.append(Document(entry,original,model))
                names.append(model)
        names=sorted(set(names))
        result['candidates']=[{'model_name':name,'confirmation_required':True} for name in names]
        matched=[d for d in docs if normalize(d.model)==normalize(task.product_query)]
        if not matched:
            issues.append('没有与输入精确一致且有完整规格原文的型号；不使用相近型号或发现卡替代')
            result['questions']=[{'field':'product_query','question':'请从候选中确认完整型号（含鸿蒙版/年份等后缀）；若已精确指定，请补充该型号产品事实原文。'}]
            return finish('needs_input')
        model=matched[0].model
        pid='P-'+hashlib.sha256(normalize(model).encode()).hexdigest()[:16]
        product=Product(product_id=pid,model_name=model,confirmed=True,
            configuration='型号级规格，非单一销售SKU；屏幕/键盘/内存/存储分支见各事实条件，不推断组合')
        facts=[]
        for doc in matched:
            extracted,gaps=extract_facts(doc,pid,source_kind=getattr(client,'source_kind','ima')); facts.extend(extracted); issues.extend(gaps)
        problems=conflicts(facts)
        if problems:
            issues.extend(problems)
            return finish('conflict', product)
        facts=list({re.sub(r'\s+','',f.text):f for f in facts}.values())
        priorities=context['fact_priority_sections']
        facts.sort(key=lambda f: priorities.index(f.text.partition('：')[0]) if f.text.partition('：')[0] in priorities else 99)
        if not facts:
            issues.append('原文中没有可安全提取的完整规格章节，禁止凭常识补齐')
            return finish('unavailable', product)
        candidates=[x for x in rows if x.get('media_type')==9 and image_matches(x,matched[0])]
        if not candidates:
            for alias in sorted(matched[0].aliases):
                more=await client.search(alias)
                candidates.extend(x for x in more if x.get('media_type')==9 and image_matches(x,matched[0]))
        candidates=list({x['media_id']:x for x in candidates}.values())
        candidates.sort(key=lambda x:image_score(x,priorities),reverse=True)
        images=[]
        rule=PLATFORM_RULES[task.platform.value]
        source_kind=getattr(client,'source_kind','ima')
        for entry in candidates[:rule['image_max']+3]:
            try:
                if hasattr(client,'read_media_bytes'):
                    body,mime=await client.read_media_bytes(entry)
                    if not mime: raise ImaError('图片实际签名不是PNG/JPEG/WebP')
                    excerpt='本地官方知识库图片索引与型号目录一致；未与官网原图字节比对：'+entry['title']
                else:
                    media=await client.media(entry['media_id']); info=media.get('url_info') or {}
                    if not info.get('url'): raise ImaError('图片没有可读链接')
                    body,_=await asyncio.to_thread(read_resource,info['url'],info.get('headers'),8_000_000)
                    mime=image_type(body)
                    if not mime: raise ImaError('图片实际签名不是PNG/JPEG/WebP')
                    excerpt='ima文件名与产品原文官方URL型号slug严格一致：'+entry['title']
                url=store_asset(body,mime)
                fid=hashlib.sha256(entry['media_id'].encode()).hexdigest()[:14]
                images.append(ImageAsset(image_id='I-'+fid,product_id=pid,access_url=url,
                    caption=entry['title'],model_verified=True,
                    source=Source(kind=source_kind,title=entry['title'],locator=model+' / image-'+fid,
                        excerpt=excerpt)))
                if len(images)>=rule['image_max']: break
            except ImaError as error:
                issues.append('图片读取失败：'+str(error))
        issues.append(('本地官方知识库' if source_kind!='ima' else 'ima')+'图片身份按文件名与规格原文slug核对，未做像素级外观/颜色/SKU鉴定；参数不得由图片推断')
        issues.append('图片地址为当前服务30分钟临时读取地址；跨机器须加服务基址，失效或重启后重新检索')
        if len(images)<rule['image_min']:
            issues.append(f"平台至少需要{rule['image_min']}张已核实图片，当前{len(images)}张")
            return finish('needs_input',product,facts,images)
        result['image_plan']=[{'image_id':im.image_id,'position':('封面/开头' if i==0 else f'第{i}个场景/卖点段后'),
            'reason':'与目标产品型号一致；按当前场景优先级选择，图片不作为数值参数证据'} for i,im in enumerate(images)]
        result['guidance']={'recommendation':'已确认输入型号，提供有证据的场景卖点；不是全库最优或预算内在售承诺',
            'priority_fact_ids':[f.fact_id for f in facts[:3]],'next_step':context['cta'],
            'unknowns':['具体销售SKU及配置组合','常用软件和外设兼容性','当前价格、权益与库存']}
        return finish('ready',product,facts,images)
    except ImaError as error:
        issues.append(str(error))
        client.trace.append({'step':'retrieval','status':'failed','reason':str(error)})
        return finish('unavailable')


async def prepare_materials(task: Task, client=None):
    client=client or default_client()
    try:
        return await asyncio.wait_for(_prepare_materials(task,client),timeout=150)
    except asyncio.TimeoutError:
        bundle=MaterialBundle(request_id='guide-'+secrets.token_hex(10),data_mode='live',
            status='unavailable',task=task,issues=['前段总读取时间超过150秒，停止交付；请缩小检索范围后重试'])
        return {'request_id':bundle.request_id,'materials':bundle.model_dump(mode='json'),
            'strategy':strategy(task),'risks':input_risks(task),'questions':[],'candidates':[],
            'trace':list(client.trace),'connection':{},'can_generate':False,
            'competition_ready':False,'can_publish':False,
            'data_state':{'read_at':datetime.now(timezone.utc).isoformat(),
                          'status':'unavailable','reasons':bundle.issues}}


async def retrieve_materials(task: Task) -> MaterialBundle:
    return MaterialBundle.model_validate((await prepare_materials(task))['materials'])


async def guide(intake: Intake):
    questions=intake.questions()
    if questions:
        return {'status':'needs_input','questions':questions,'next_question':questions[0],
                'slots':intake.model_dump(mode='json'),'materials':None,
                'can_generate':False,'competition_ready':False,'can_publish':False}
    result=await prepare_materials(intake.as_task())
    return {'status':result['materials']['status'],**result}
