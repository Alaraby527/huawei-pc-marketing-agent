"""Typed, same-library product evidence. Search relevance is never identity proof."""
import asyncio
import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, parse_qs

from pydantic import Field, model_validator
from .contracts import StrictModel, Fact, Source
from .ima import ImaClient, ImaError, image_type, read_resource
from .local_kb import default_client
from .retrieval import Document, normalize, model_from_document, extract_facts, conflicts, image_matches, image_score, store_asset
from .platforms import PLATFORM_RULES

SHANGHAI=ZoneInfo('Asia/Shanghai')


def now_local():
    return datetime.now(SHANGHAI)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def official_url(url):
    try:
        parsed=urlsplit(url)
        host=(parsed.hostname or '').lower()
        return parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443) and (
            host=='consumer.huawei.com' or host=='vmall.com' or host.endswith('.vmall.com'))
    except (ValueError,TypeError):
        return False


class Budget(StrictModel):
    minimum: float = Field(default=0,ge=0,le=10000000)
    maximum: float | None = Field(default=None,gt=0,le=10000000)
    unlimited: bool = False
    approximate: bool = False
    currency: str = 'CNY'

    @model_validator(mode='after')
    def consistent(self):
        if self.currency!='CNY' or (self.unlimited and self.maximum is not None):
            raise ValueError('预算需为人民币，且不限预算不能同时设上限')
        if not self.unlimited and self.maximum is None:
            raise ValueError('预算需要上限或明确不限预算')
        if self.maximum is not None and self.minimum>self.maximum:
            raise ValueError('预算下限不能高于上限')
        return self


class PriceRecord(StrictModel):
    """Only an explicit maintained ima record can establish a current SKU price."""
    model_name: str = Field(min_length=1,max_length=300)
    sku: str = Field(min_length=1,max_length=150)
    configuration: str = Field(min_length=1,max_length=600)
    amount: float = Field(gt=0,le=10000000)
    currency: str = 'CNY'
    price_type: str
    region: str = Field(min_length=1,max_length=100)
    channel: str = Field(min_length=1,max_length=100)
    verified_at: datetime
    valid_until: datetime
    source_url: str
    status: str
    conditions: str = Field(default='',max_length=1000)
    available: bool | None = None

    def blockers(self,model,region,channel,now=None):
        now=now or now_local()
        issues=[]
        if normalize(self.model_name)!=normalize(model): issues.append('价格型号不一致')
        if self.currency!='CNY' or self.price_type!='page_price': issues.append('不是人民币无条件页面价')
        if self.status!='verified' or self.conditions: issues.append('价格未核实或存在未自动核实的购买条件')
        if not official_url(self.source_url): issues.append('缺少官方销售来源')
        if self.available is not True: issues.append('当前可购状态未证实')
        if region is None or self.region!=region: issues.append('适用地区未确认或不一致')
        if channel is None or self.channel!=channel: issues.append('购买渠道未确认或不一致')
        if self.verified_at.tzinfo is None or self.valid_until.tzinfo is None:
            issues.append('价格时间缺少时区')
        elif (self.verified_at>now or self.valid_until<=now or self.valid_until<=self.verified_at
              or now-self.verified_at>timedelta(hours=24)
              or self.verified_at.astimezone(SHANGHAI).date()!=now.date()):
            issues.append('价格不是当日有效记录或已过期')
        if len(re.findall(r'\d+\s*(?:GB|TB)',self.configuration,re.I))<2:
            issues.append('配置不完整')
        if any(word in self.configuration for word in ['默认','未展开','待确认','起售价']): issues.append('不是精确款式')
        return issues


@dataclass
class ProductEvidence:
    model: str
    docs: list[Document] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    prices: list[tuple[PriceRecord,dict,str]] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    product_urls: set[str] = field(default_factory=set)

    @property
    def product_id(self): return 'P-'+digest(normalize(self.model))[:16]


@dataclass
class EvidenceCatalog:
    client: ImaClient
    connection: dict
    entries: list[dict]
    products: list[ProductEvidence]
    scene_sources: list[dict]
    image_index: list[dict]
    read_at: str
    scope_hash: str
    issues: list[str]


def scene_names(profile,scenario):
    text=profile+' '+scenario
    routes=[(['学生','新生','学习','论文','网课'],'学生学习场景卡.md'),
            (['差旅','移动','出差','办公','会议'],'移动办公场景卡.md'),
            (['创作','设计','剪辑','摄影'],'内容创作场景卡.md'),
            (['家庭','家用','父母','换新'],'家庭换新场景卡.md'),
            (['企业','采购','部署'],'企业部署场景卡.md')]
    return [name for words,name in routes if any(word in text for word in words)]


def is_product_original(entry):
    title=entry.get('title','')
    if any(x in title for x in ['MC-','案例','价格','销售配置','场景','模板','索引']): return False
    return ('产品事实' in title or '规格' in title or bool(re.match(r'^\d+[-_]HUAWEI MateBook',title,re.I)))


async def load_catalog(profile,scenario,client=None):
    client=client or default_client()
    connection=await client.connect()
    # Enumerate the configured KB: even a rogue search hit cannot read another KB's media.
    entries=await client.catalog()
    by_id={x['media_id']:x for x in entries if x.get('media_id')}
    found=await client.search(profile+' '+scenario)
    client.trace.append({'step':'scope_check','status':'success',
                         'rejected_search_hits':sum(x.get('media_id') not in by_id for x in found)})
    ranked_ids=[x['media_id'] for x in found if x.get('media_id') in by_id]
    originals=[x for x in entries if is_product_original(x)]
    if len(originals)>48: raise ImaError('完整产品原文超过48份读取上限，请按PC产品目录整理后缩小范围')
    # Only fact originals enter the product index. Discovery cards may offer names, not fabricated specs.
    groups={}; issues=[]
    ordered=sorted(originals,key=lambda x: ranked_ids.index(x['media_id']) if x['media_id'] in ranked_ids else 999)
    semaphore=asyncio.Semaphore(4)
    async def read_original(entry):
        async with semaphore:
            try:
                return entry, await client.read_text(entry), None
            except ImaError as error:
                return entry, None, str(error)
    results=await asyncio.gather(*(read_original(entry) for entry in ordered))
    for entry,text,error in results:
        if error:
            issues.append('跳过无法读取的原文：'+entry.get('title','未命名')+'（'+error+'）')
            continue
        model=model_from_document(text)
        if not model: continue
        if '发现卡' in text and '## 官方规格摘录' not in text and '产品事实' not in entry.get('title',''): continue
        doc=Document(entry,text,model)
        facts,gaps=extract_facts(doc,'P-'+digest(normalize(model))[:16],source_kind=getattr(client,'source_kind','ima'))
        if not facts: continue
        for fact in facts:
            fact.source.locator=f"{client.kb_name} / {entry.get('_path',entry['title'])} / media:{entry['media_id']} / sha256:{digest(text)} / {fact.text.partition('：')[0]}"
        product=groups.setdefault(normalize(model),ProductEvidence(model))
        product.docs.append(doc); product.facts.extend(facts); product.issues.extend(gaps)
        product.product_urls.update(re.findall(r'https://[^\s)>]+',text))
    for product in groups.values():
        product.issues.extend(conflicts(product.facts))
        product.facts=list({re.sub(r'\s+','',f.text):f for f in product.facts}.values())
    # Explicit JSON records in an ima file; never take these records from chat slot updates.
    for entry in entries:
        title=entry.get('title','')
        if not any(word in title for word in ['价格卡','销售核验记录']): continue
        expected=re.sub(r'[-—](?:价格卡|销售核验记录)\.(?:md|txt)$','',title)
        product=groups.get(normalize(expected))
        if not product: continue
        text=await client.read_text(entry)
        identity=re.search(r'(?m)^(?:product_name:|[- ]*规范产品名[：:])\s*(.+)$',text)
        if identity and normalize(identity.group(1).strip())!=normalize(product.model):
            product.issues.append('价格文件名与原文产品身份冲突，停止使用该价格资料')
            continue
        product.product_urls.update(re.findall(r'https://[^\s)>]+',text))
        if 'public_price_snapshot' in text:
            product.history.append({'title':title,'status':'historical_only',
                'reason':'快照只证明原记录时点，不用于当前预算、活动、库存或赠品承诺',
                'locator':client.kb_name+' / '+entry.get('_path',title)})
        for match in re.finditer(r'```verified-price\s*\n(.*?)\n```',text,re.S):
            try:
                record=PriceRecord.model_validate(json.loads(match.group(1)))
                if normalize(record.model_name)!=normalize(product.model):
                    raise ValueError('identity')
                product.prices.append((record,entry,match.group(1)))
            except (ValueError,TypeError):
                product.issues.append('销售核验记录格式或产品身份有误，未采纳')
    scenes=[]
    for name in scene_names(profile,scenario):
        matches=[x for x in entries if x.get('title')==name]
        if len(matches)!=1:
            issues.append('没有唯一可核验场景资料：'+name)
            continue
        entry=matches[0]; text=await client.read_text(entry)
        heading=re.search(r'^#\s+(.+)',text,re.M)
        if not heading or (name.removesuffix('.md') not in heading.group(1) and not entry.get('_scene_verified')):
            issues.append('场景资料标题与原文不一致：'+name); continue
        scenes.append({'title':name,'locator':client.kb_name+' / '+entry.get('_path',name),
            'content_sha256':digest(text),'excerpt':text[:1200],
            'usage':'仅用于场景问题与策略；示例、案例和模板不得当产品参数、兼容证明或真实用户体验'})
    index=[]
    index_entries=[x for x in entries if x.get('title')=='图片索引.csv']
    if len(index_entries)==1:
        text=await client.read_text(index_entries[0])
        for row in csv.DictReader(io.StringIO(text)):
            row['_index_locator']=client.kb_name+' / '+index_entries[0].get('_path','图片索引.csv')
            row['_index_hash']=digest(text)
            index.append(row)
    elif len(index_entries)>1:
        issues.append('图片索引重名，无法唯一关联，停止图像交付')
    else: issues.append('缺少图片索引；不凭近似文件名确认产品图片')
    scope=digest(str(client.kb_id))
    return EvidenceCatalog(client,connection,entries,list(groups.values()),scenes,index,
                           now_local().isoformat(),scope,issues)


def rank_products(catalog,slots):
    budget=Budget.model_validate(slots['budget'])
    results=[]; rejected=[]
    scenario=slots['scenario']
    required=slots.get('required_software',[])
    preferred=' '.join(slots.get('preferences',[]))
    for product in catalog.products:
        if product.issues:
            rejected.append({'model_name':product.model,'reasons':product.issues}); continue
        matched=[]; reasons=[]; unknowns=[]
        by_section={f.text.partition('：')[0]:f for f in product.facts}
        # Match task dimensions to source sections; do not assert software or performance suitability.
        priorities=['操作系统','屏幕','接口']
        if any(word in scenario+preferred for word in ['携带','便携','轻薄','出差','通勤']): priorities=['尺寸与重量','电池','接口','屏幕']
        if any(word in scenario for word in ['创作','剪辑','设计']): priorities=['屏幕','处理器','内存','存储','接口']
        for i,section in enumerate(priorities):
            if section in by_section:
                matched.append(by_section[section].fact_id)
                reasons.append({'dimension':section,'fact_id':by_section[section].fact_id,
                                'reason':'该任务需关注此维度；已找到对应规格原文，不等于实测通过'})
        if not matched: continue
        hard_unknown=[]
        if required:
            hard_unknown.append('必用软件/版本适配未取得对应官方支持证据：'+'、'.join(required))
        if slots.get('other_constraints'):
            hard_unknown.append('其他明确硬性条件尚无逐项核验：'+'、'.join(slots['other_constraints']))
        os_required=slots.get('required_os')
        if os_required:
            os_fact=by_section.get('操作系统')
            if not os_fact or os_required.casefold() not in os_fact.text.casefold():
                rejected.append({'model_name':product.model,'reasons':['不满足或无法证实指定操作系统：'+os_required]}); continue
        weight_limit=slots.get('max_weight_kg')
        if weight_limit:
            weight=by_section.get('尺寸与重量')
            numbers=[float(n) for n in re.findall(r'(\d+(?:\.\d+)?)\s*kg',weight.text,re.I)] if weight else []
            if not numbers or max(numbers)>weight_limit:
                rejected.append({'model_name':product.model,'reasons':['不满足或无法证实重量上限（按全部未区分款式保守判断）']}); continue
        price=None; price_issues=[]; valid=[]
        for record,entry,excerpt in product.prices:
            problems=record.blockers(product.model,slots.get('region'),slots.get('channel'))
            if problems: price_issues.extend(problems)
            else: valid.append((record,entry,excerpt))
        # Different prices for one SKU+scope are conflicts even if one would fit budget.
        by_sku={}
        price_conflict=False
        for record,_,_ in valid:
            key=(record.sku,record.region,record.channel)
            value=(record.amount,record.configuration)
            if key in by_sku and by_sku[key]!=value: price_conflict=True
            by_sku[key]=value
        if price_conflict:
            rejected.append({'model_name':product.model,'reasons':['同SKU有效价格/配置冲突']}); continue
        if budget.unlimited:
            budget_status='not_limited'
        else:
            suitable=[x for x in valid if x[0].amount<=budget.maximum and x[0].amount>=budget.minimum]
            if suitable:
                price,entry,excerpt=min(suitable,key=lambda x:x[0].amount)
                budget_status='verified'
            elif valid:
                rejected.append({'model_name':product.model,'reasons':['有效价格不在预算范围']}); continue
            else:
                budget_status='unverified'
                hard_unknown.append('未取得当日、同配置、同地区/渠道的有效价格，不能声称预算内可买')
        if budget.approximate: hard_unknown.append('预算为近似值，需要确认可接受的明确上限')
        score=sum(10-i for i,section in enumerate(priorities) if section in by_section)
        weights=by_section.get('尺寸与重量')
        values=[float(n) for n in re.findall(r'(\d+(?:\.\d+)?)\s*kg',weights.text,re.I)] if weights else []
        if values and any(w in preferred for w in ['便携','轻薄','重量']): score+=max(0,3-max(values))
        candidate={'candidate_id':product.product_id,'model_name':product.model,
            'rank_score':round(score,2),'ranking_explanation':'规格证据覆盖与明确偏好排序；不以价格越高越优或搜索排名替代适配',
            'reasons':reasons,'fact_ids':matched,'budget_status':budget_status,
            'current_price':price.model_dump(mode='json') if price else None,
            'configuration':price.configuration if price else '型号级规格；未确认销售SKU，不拼接不同款式参数',
            'price_evidence':({'title':entry['title'],'excerpt':excerpt,'locator':catalog.client.kb_name+' / '+entry.get('_path',entry['title'])} if price else None),
            'history':product.history,'tradeoffs':['不同屏幕、键盘与容量的条件需保留；未证明的实际体验不能作为优点'],
            'unknowns':hard_unknown+unknowns,'eligibility':'verified' if not hard_unknown else 'pending_verification',
            'price_gaps':sorted(set(price_issues))}
        results.append(candidate)
    results.sort(key=lambda x:(x['eligibility']=='verified',x['rank_score']),reverse=True)
    return results[:3],rejected


async def verified_images(catalog,product,platform,priorities):
    rule=PLATFORM_RULES[platform]
    entries={x.get('title'):[] for x in catalog.entries}
    for entry in catalog.entries: entries[entry.get('title')].append(entry)
    index={}
    for row in catalog.image_index:
        name=row.get('文件名','')
        index.setdefault(name,[]).append(row)
    # Cross-check filename slug and index model/official product URL from the same KB.
    selected=[]; issues=[]
    doc=product.docs[0]
    for name,matches in entries.items():
        if len(matches)!=1 or not image_matches(matches[0],doc): continue
        entry=matches[0]
        if entry.get('media_type')!=9: continue
        rows=index.get(name,[])
        if len(rows)!=1: continue
        row=rows[0]
        declared=row.get('产品型号')
        if declared and normalize(declared)!=normalize(product.model):
            issues.append('图片索引型号与文件名冲突：'+name); continue
        original=row.get('原始图片URL','')
        origin_host=(urlsplit(original).hostname or '').lower()
        if urlsplit(original).scheme!='https' or not (origin_host.endswith('.vmallres.com') or origin_host=='consumer.huawei.com'):
            continue
        product_url=row.get('商品页URL','') or row.get('产品页URL','')
        if not official_url(product_url): continue
        product_code=parse_qs(urlsplit(product_url).query).get('prdId')
        code_matches=product_code and any(parse_qs(urlsplit(url).query).get('prdId')==product_code
                                            for url in product.product_urls if official_url(url))
        exact_source=any(urlsplit(url)._replace(query='',fragment='')==urlsplit(product_url)._replace(query='',fragment='')
                         for url in product.product_urls if official_url(url) and urlsplit(url).hostname=='consumer.huawei.com')
        if not (code_matches or exact_source):
            issues.append('图片索引商品身份未在本型号资料中找到对应：'+name); continue
        selected.append((entry,row))
    selected.sort(key=lambda pair:image_score(pair[0],priorities),reverse=True)
    images=[]
    local=getattr(catalog.client,'source_kind',None)=='local_official_export'
    for entry,row in selected[:rule['image_max']+3]:
        try:
            if local:
                body,mime=await catalog.client.read_media_bytes(entry)
                if not mime: raise ImaError('图片签名错误，不能使用失效页或非图片资源')
                method='local_official_export_index'
                excerpt='本地官方知识库图片索引+型号目录一致；未与官网原图字节比对；'+entry['title']
                limitations='本地官方知识库导出；未证明颜色/键盘/屏幕/SKU一致；下游不得写成所选SKU实拍或推断参数'
            else:
                data=await catalog.client.media(entry['media_id']); info=data.get('url_info') or {}
                if not info.get('url'): raise ImaError('图片没有读取地址')
                body,_=await asyncio.to_thread(read_resource,info['url'],info.get('headers'),8_000_000)
                mime=image_type(body)
                if not mime: raise ImaError('图片签名错误，不能使用失效页或非图片资源')
                # Original source must match actual ima bytes; filename/index alone is insufficient.
                original,_=await asyncio.to_thread(read_resource,row['原始图片URL'],None,8_000_000)
                if hashlib.sha256(body).digest()!=hashlib.sha256(original).digest():
                    raise ImaError('ima图片字节与索引官方原图不一致，停止使用此图')
                method='same_library_index_slug_and_original_sha256'
                excerpt='同库图片索引+本型号官方slug+官方原图与ima下载字节SHA256一致；'+entry['title']
                limitations='未证明颜色/键盘/屏幕/SKU一致；下游不得写成所选SKU实拍或推断参数'
            token=store_asset(body,mime)
            fid='I-'+hashlib.sha256(body).hexdigest()[:16]
            if any(x['image_id']==fid for x in images): continue
            images.append({'image_id':fid,'product_id':product.product_id,'access_url':token,
                'caption':row.get('画面说明') or entry['title'],'model_verified':True,
                'source':{'kind':getattr(catalog.client,'source_kind','ima'),'title':entry['title'],
                    'locator':row['_index_locator']+' / '+entry['title'],
                    'excerpt':excerpt},
                'verification':{'method':method,
                    'sha256':hashlib.sha256(body).hexdigest(),'identity_scope':'model_only',
                    'configuration_verified':False,'original_url':row['原始图片URL'],
                    'index_sha256':row['_index_hash'],'source_date':row.get('抓取日期'),
                    'limitations':limitations},
                'expires_in_seconds':1800})
            if len(images)>=rule['image_max']: break
        except ImaError as error: issues.append(entry['title']+'：'+str(error))
    if local:
        issues.append('图片身份按本地官方知识库索引与型号目录核对；未与官网原图字节比对')
    if len(images)<rule['image_min']:
        issues.append(f"经来源核实的不同图片只有{len(images)}张，平台需要至少{rule['image_min']}张")
    return images,issues
