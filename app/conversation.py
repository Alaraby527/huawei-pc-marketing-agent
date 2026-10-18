"""Store/operator dialogue. Customer statements are needs, never sales evidence."""
import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal
from pydantic import Field

from .contracts import StrictModel, Platform, Task, Product, MaterialBundle, ImageAsset, Fact
from .evidence import Budget, load_catalog, rank_products, verified_images, digest, now_local
from .ima import ImaClient, ImaError
from .intake import strategy
from .retrieval import input_risks
from .compose import compose_draft, revise_draft
from .compliance import audit_copy


class Slots(StrictModel):
    audience: str = Field(default='',max_length=300)
    budget: Budget | None = None
    scenario: str = Field(default='',max_length=600)
    preferences: list[str] = Field(default_factory=list,max_length=12)
    required_software: list[str] = Field(default_factory=list,max_length=12)
    other_constraints: list[str] = Field(default_factory=list,max_length=12)
    required_os: str | None = Field(default=None,max_length=100)
    max_weight_kg: float | None = Field(default=None,gt=0,le=10)
    region: str | None = Field(default=None,max_length=100)
    channel: str | None = Field(default=None,max_length=100)
    platform: Platform | None = Platform.XIAOHONGSHU  # locked to 小红书 for this MVP
    objective: str = Field(default='',max_length=300)
    purchase_stage: str = Field(default='待确认',max_length=100)
    instructions: str = Field(default='',max_length=2000)
    hotspot: str = Field(default='',max_length=300)


class Turn(StrictModel):
    session_id: str | None = Field(default=None,max_length=80)
    expected_revision: int | None = Field(default=None,ge=0)
    message: str = Field(default='',max_length=4000)
    slot_updates: Slots | None = None
    action: Literal['continue','skip_preferences','select','refresh','export',
                     'approve_draft','revise_draft','regenerate_draft'] = 'continue'
    candidate_id: str | None = Field(default=None,max_length=80)
    engine: Literal['rule','llm'] = 'rule'
    model: str | None = Field(default=None,max_length=100)
    revise_instruction: str = Field(default='',max_length=1000)


class DialogueError(Exception):
    def __init__(self,message,status=409): self.message=message; self.status=status


@dataclass
class Session:
    sid: str
    kb_name: str
    slots: Slots = field(default_factory=Slots)
    revision: int = 0
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    preferences_done: bool = False
    asked_preferences: bool = False
    last_questions: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    selected: dict | None = None
    catalog: object = None
    catalog_time: float = 0
    history: list = field(default_factory=list)
    handoff_cache: dict | None = None
    draft: dict | None = None
    draft_binding: str | None = None
    draft_approved: bool = False
    compliance_report: dict | None = None


DIGITS={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
UNITS={'十':10,'百':100,'千':1000}


def _chinese_number(value):
    # Colloquial trailing digit: 一万二=12000, 两千五=2500, 一百二=120.
    if len(value)>=2 and value[-1] in DIGITS and value[-2] in '万千百':
        return _chinese_number(value[:-1])+DIGITS[value[-1]]*{'万':1000,'千':100,'百':10}[value[-2]]
    total=section=number=0
    for ch in value:
        if ch in DIGITS:
            number=DIGITS[ch]
        elif ch in UNITS:
            section+=(number or 1)*UNITS[ch]; number=0
        elif ch=='万':
            section=(section+number)*10000; total+=section; section=0; number=0
        else:
            raise ValueError('预算表达无法识别')
    return total+section+number


def _arabic_number(value):
    match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*([万千])?\s*(\d*)',value)
    if not match: raise ValueError('预算表达无法识别')
    main=float(match.group(1)); unit=match.group(2); rest=match.group(3)
    if not unit:
        if rest: raise ValueError('预算表达无法识别')
        return main
    scale={'万':10000,'千':1000}[unit]
    if rest:
        # 1万2=12000, 2千5=2500; multi-digit suffix is already an absolute amount.
        return main*scale+(int(rest)*(scale//10) if len(rest)==1 else int(rest))
    return main*scale


def money(value):
    value=value.strip().replace(',','').replace('，','')
    if re.fullmatch(r'[零一二两三四五六七八九十百千万]+',value):
        return float(_chinese_number(value))
    if re.fullmatch(r'\d+(?:\.\d+)?\s*[万千]?\s*\d*',value):
        return _arabic_number(value)
    raise ValueError('预算表达无法识别')


def parse_message(message,slots,last_questions):
    """Conservative deterministic extraction; ambiguous statements remain explicit questions."""
    patch={}; text=message.strip()
    # Single-platform MVP: 小红书 is the only supported destination. Mentions of
    # other platforms are kept in `instructions` as context, not acted on, so the
    # pipeline never silently re-targets or re-triggers a full re-selection cycle.
    audience=re.search(r'(?:画像|客户|顾客|面向|人群|对象)[是为：:]*\s*([^，。；\n]{1,60})',text)
    if audience: patch['audience']=audience.group(1)
    else:
        for word in ['大学新生','大学生','研究生','高中生','学生','设计师','摄影师','创作者','上班族','商务人士','程序员','企业采购','家庭用户','父母']:
            if word in text: patch['audience']=word; break
    # Occupation alone does not establish what the PC will be used for.
    clauses=re.split(r'[，。；\n]',text)
    scene=[c.strip() for c in clauses if any(w in c for w in ['用途','场景','用来','用于','主要用','写论文','写作业','上网课','做表格','视频剪辑','编程','差旅','出差','文档办公','日常办公','课堂学习','校园携带','家庭娱乐'])]
    if scene: patch['scenario']='；'.join(scene)[:600]
    if re.search(r'(?:预算|价格)(?:不设上限|不限|无限制|没有上限|没有限制)|不限预算|没有预算限制|无预算限制',text):
        patch['budget']=Budget(unlimited=True).model_dump()
    else:
        number=r'(?:[0-9]+(?:\.[0-9]+)?[万千][0-9]*|[0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百千万]+)'
        span=re.search(r'预算\s*[是为：:]?\s*('+number+r')\s*(?:元)?\s*[-~到至]\s*('+number+r')\s*(?:元)?',text)
        if not span: span=re.fullmatch(r'('+number+r')\s*(?:元)?\s*[-~到至]\s*('+number+r')\s*元?',text)
        amount=re.search(r'预算\s*(?:不超过|最多|最高|至多|上限为?|在)?\s*[是为：:]?\s*('+number+r')\s*(?:元)?',text)
        if not amount: amount=re.search(r'('+number+r')\s*(?:元)?\s*(?:以内|以下|封顶)',text)
        if not amount and re.fullmatch(number+r'\s*元?',text): amount=re.match('('+number+')',text)
        if span:
            low,high=money(span.group(1)),money(span.group(2))
            patch['budget']=Budget(minimum=low,maximum=high,approximate='左右' in text).model_dump()
        elif amount:
            patch['budget']=Budget(maximum=money(amount.group(1)),approximate=any(w in text for w in ['左右','大约','差不多'])).model_dump()
    pref=[w for w in ['轻薄','便携','大屏','长续航','高分辨率','触控','安静','接口丰富'] if w in text]
    if pref: patch['preferences']=list(dict.fromkeys(slots.preferences+pref))
    # Explicit dependencies must never be downgraded to soft preferences.
    software=[]
    for c in clauses:
        if re.search(r'不(?:需要|用|要)|无需|不要|不用',c): continue
        if re.search(r'必用|必须用|需要运行|需要用|(?<!主)要用(?!来)|常用软件|软件要求|必须连接|外设要求',c):
            software.append(c.strip()[:150])
    if software: patch['required_software']=list(dict.fromkeys(slots.required_software+software))[:12]
    for c in clauses:
        # A negated OS mention ("不需要Windows") is not an OS requirement.
        if re.search(r'不(?:需要|用|要)|无需|不要|不用|不是',c): continue
        for name in ['Windows','HarmonyOS','Linux','macOS']:
            if re.search(r'(?:必须|只要|系统要求|需要|要用)[^，。；]{0,12}'+name,c,re.I):
                patch['required_os']=name
                break
    weight=re.search(r'(?:不超过|最多|上限|低于)\s*(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)',text,re.I)
    if weight: patch['max_weight_kg']=float(weight.group(1))
    others=[c.strip()[:150] for c in clauses if re.search(r'必须|不接受|硬性要求',c)
            and not re.search(r'无硬性要求|没有硬性要求',c) and c.strip()[:150] not in software
            and not any(name.casefold() in c.casefold() for name in ['Windows','HarmonyOS','Linux','macOS'])
            and not re.search(r'(?:kg|公斤|千克)',c,re.I)]
    if others: patch['other_constraints']=list(dict.fromkeys(slots.other_constraints+others))[:12]
    for phrase in ['到店体验','到店','预约','咨询','私信','比较选型','产品介绍','了解产品']:
        if phrase in text: patch['objective']=phrase; break
    for name,pattern in [('region',r'地区[：:是为 ]+([^，。；\n]{1,60})'),('channel',r'渠道[：:是为 ]+([^，。；\n]{1,60})')]:
        match=re.search(pattern,text)
        if match: patch[name]=match.group(1)
    hotspot=re.search(r'(?:参考热点|热点|话题)[：:]\s*([^，。；\n]{1,100})',text)
    if hotspot: patch['hotspot']=hotspot.group(1).strip()
    # If a single narrative field remains, a plain answer is unambiguous.
    if len(last_questions)==1 and not patch and text and text not in ['跳过','继续','确认','刷新','导出']:
        key=last_questions[0]['field']
        if key in ['audience','scenario','objective']: patch[key]=text[:300]
    if text: patch['instructions']=(slots.instructions+'\n'+text).strip()[-2000:]
    return patch


CORE_QUESTIONS={
    'audience':{
        'question':'这位客户或目标客群是什么画像？可以直接选一个方向，也可以自己描述。',
        'options':[
            {'label':'大学新生','message':'客户画像是大学新生'},
            {'label':'职场办公','message':'客户画像是职场办公者'},
            {'label':'内容创作者','message':'客户画像是内容创作者'},
            {'label':'家庭用户','message':'客户画像是家庭用户'},
            {'label':'企业采购','message':'客户画像是企业采购者'}]},
    'budget':{
        'question':'客户预算上限是多少？可以选区间、说不限，或直接输入金额。',
        'options':[
            {'label':'4000–5000','message':'预算4000到5000'},
            {'label':'5000–6000','message':'预算5000到6000'},
            {'label':'6000–8000','message':'预算6000到8000'},
            {'label':'8000以上','message':'预算8000到10000'},
            {'label':'预算不限','message':'预算不限'}]},
    'scenario':{
        'question':'客户主要用电脑完成什么任务？可选一个方向，或补充具体软件/课程。',
        'options':[
            {'label':'课堂学习','message':'主要用途是课堂学习、写论文、上网课'},
            {'label':'移动办公','message':'主要用途是差旅移动办公、会议和文档处理'},
            {'label':'内容创作','message':'主要用途是视频剪辑、设计和摄影素材处理'},
            {'label':'家庭换新','message':'主要用途是家庭日常使用和旧机换新'},
            {'label':'企业部署','message':'主要用途是企业办公、团队部署和外设连接'}]},
}
PREFERENCE_QUESTIONS={
    'preferences':{
        'question':'客户对便携、屏幕大小或续航有哪些偏好？没有可回复“跳过”。',
        'options':[
            {'label':'轻薄便携','message':'偏好轻薄便携'},
            {'label':'大屏高刷','message':'偏好大屏'},
            {'label':'长续航','message':'偏好长续航'},
            {'label':'接口丰富','message':'偏好接口丰富'},
            {'label':'没有特别偏好','message':'跳过'}]},
    'required_software':{
        'question':'还有必须使用的软件/版本、操作系统或外设吗？没有可说“无硬性要求”。',
        'options':[
            {'label':'无硬性要求','message':'无硬性要求'},
            {'label':'办公三件套','message':'必须用Office办公软件'},
            {'label':'设计/剪辑软件','message':'必须用Adobe设计或视频剪辑软件'},
            {'label':'编程/开发环境','message':'必须用编程开发环境'},
            {'label':'必须 Windows','message':'必须用Windows'},
            {'label':'必须鸿蒙/国产系统','message':'必须用HarmonyOS'}]},
}
OBJECTIVE_OPTIONS={
    'question':'希望内容促成什么行动？可以选一个方向。',
    'options':[
        {'label':'到店体验','message':'希望邀请到店体验'},
        {'label':'私信咨询','message':'希望引导私信咨询'},
        {'label':'比较选型','message':'希望帮助比较选型'},
        {'label':'了解产品','message':'希望让读者了解产品'}]}
DRAFT_OPTIONS=[
    {'label':'通过，可以发布','message':'通过'},
    {'label':'再短一点','message':'再短一点'},
    {'label':'换一版','message':'换一版'}]

def ask(field, **extra):
    data={**CORE_QUESTIONS,**PREFERENCE_QUESTIONS}.get(field, {'question':'','options':[]})
    item={'field':field,'question':data.get('question',''),'options':data.get('options',[])}
    item.update(extra)
    return item


class ConversationService:
    def __init__(self,client_factory=ImaClient,ttl=1800):
        self.sessions={}; self.client_factory=client_factory; self.ttl=ttl

    @staticmethod
    def handoff_binding(handoff):
        """Stable digest of everything a draft is allowed to be based on."""
        materials=handoff.get('materials') or {}
        product=materials.get('product') or {}
        data_state=handoff.get('data_state') or {}
        task=materials.get('task') or {}
        recommendation=handoff.get('recommendation') or {}
        payload={
            'product_id':product.get('product_id'),
            'configuration':product.get('configuration'),
            'scope':data_state.get('scope_sha256'),
            'evidence':data_state.get('evidence_sha256'),
            'task':{key:task.get(key) for key in ('platform','audience','scenario','objective','purchase_stage')},
            'fact_ids':sorted(f.get('fact_id','') for f in materials.get('facts') or []),
            'image_ids':sorted(i.get('image_id','') for i in materials.get('images') or []),
            'price':recommendation.get('current_price'),
        }
        return digest(json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str))

    @staticmethod
    def invalidate_draft(session,clear_handoff=True):
        session.draft=None
        session.draft_binding=None
        session.draft_approved=False
        session.compliance_report=None
        if clear_handoff: session.handoff_cache=None

    def publishable(self,session,handoff=None):
        """Publication needs an approved draft bound to the current handoff."""
        current=handoff if handoff is not None else session.handoff_cache
        report=session.compliance_report or {}
        return bool(session.draft and session.draft_binding and session.draft_approved
                    and report.get('passed') and current
                    and current.get('data_state',{}).get('can_generate')
                    and session.draft_binding==self.handoff_binding(current))

    def session(self,request):
        now=time.monotonic()
        for key,s in list(self.sessions.items()):
            if now-s.touched>self.ttl and not s.lock.locked(): del self.sessions[key]
        if request.session_id:
            if request.session_id not in self.sessions: raise DialogueError('会话不存在或已过期，请重新开始',404)
            return self.sessions[request.session_id]
        if len(self.sessions)>=100: raise DialogueError('会话数已达上限，请稍后重试',503)
        sid=secrets.token_urlsafe(24)
        s=Session(sid,self.client_factory().kb_name); self.sessions[sid]=s
        return s

    async def turn(self,request):
        s=self.session(request)
        async with s.lock:
            if request.expected_revision is not None and request.expected_revision!=s.revision:
                raise DialogueError('会话已更新，请使用最新revision重试')
            s.revision+=1; s.touched=time.monotonic()
            try:
                return await asyncio.wait_for(self.advance(s,request),180)
            except (ImaError,asyncio.TimeoutError) as error:
                # A failed read must not leave a previously approved draft publishable.
                self.invalidate_draft(s)
                return self.response(s,'unavailable',message=str(error) if isinstance(error,ImaError) else '知识库读取超时，请输入“刷新”重试',issues=['此次读取失败，禁止沿用上次结果作为当前有效材料'])

    def response(self,s,status,questions=None,message='',issues=None,handoff=None,rejected=None):
        questions=questions or []; s.last_questions=questions
        if message: s.history.append({'revision':s.revision,'role':'assistant','text':message,'questions':questions})
        s.history=s.history[-80:]
        can_publish=self.publishable(s,handoff)
        return {'schema_version':'2.0','session_id':s.sid,'revision':s.revision,'status':status,
            'message':message,'questions':questions,'slots':s.slots.model_dump(mode='json'),
            'candidates':s.candidates,'selected':s.selected,'rejected':rejected or [],
            'knowledge_base':s.kb_name,'can_generate':bool(handoff and handoff['data_state']['can_generate']),
            'can_publish':can_publish,'competition_ready':bool(handoff and handoff['data_state']['competition_ready']),
            'issues':issues or [],'handoff':handoff,
            'draft':s.draft,'draft_approved':s.draft_approved,'compliance_report':s.compliance_report}

    async def catalog(self,s,force=False):
        if force or s.catalog is None or time.monotonic()-s.catalog_time>300:
            client=self.client_factory(); client.kb_name=s.kb_name
            fresh=await load_catalog(s.slots.audience,s.slots.scenario,client)
            if s.catalog and fresh.scope_hash!=s.catalog.scope_hash:
                raise ImaError('同名知识库标识发生变化，停止使用；请新建会话核实')
            s.catalog=fresh; s.catalog_time=time.monotonic()
        return s.catalog

    async def advance(self,s,request):
        old=s.slots.model_dump(mode='json')
        # A draft is only valid for the exact handoff it was generated from.
        if s.draft and (not s.handoff_cache or s.draft_binding!=self.handoff_binding(s.handoff_cache)):
            self.invalidate_draft(s)
        text=request.message.strip()
        if request.action=='continue' and text=='刷新':
            request.action='refresh'
        selection_text=bool(re.fullmatch(r'(?:选择?|确认)?\s*第?\s*([123])\s*(?:款|个)?(?:型号级)?',text))
        patch=parse_message(request.message,s.slots,s.last_questions)
        if request.slot_updates: patch.update(request.slot_updates.model_dump(mode='json',exclude_unset=True))
        requirement_fields=['audience','budget','scenario','preferences','required_software','other_constraints','required_os','max_weight_kg','region','channel','platform','objective','hotspot']
        patch_touches_requirements=any(key in patch for key in requirement_fields)
        # Once a draft exists and this turn carries no requirement change, route
        # straight to the draft gate. Refresh and explicit selection must always
        # re-enter the selection pipeline so they cannot be silently swallowed.
        if (s.handoff_cache and (s.draft is not None or request.action in
                ('approve_draft','revise_draft','regenerate_draft'))
                and request.action not in ('refresh','select')
                and not patch_touches_requirements and not selection_text):
            if text: s.history.append({'revision':s.revision,'role':'operator','text':text})
            return await self.draft_turn(s,request,s.handoff_cache)
        s.slots=Slots.model_validate({**old,**patch})
        new_slots=s.slots.model_dump(mode='json')
        if text: s.history.append({'revision':s.revision,'role':'operator','text':text})
        # Any structured change other than free-form instructions invalidates the draft.
        draft_fields=['audience','budget','scenario','preferences','required_software','other_constraints',
                      'required_os','max_weight_kg','region','channel','platform','objective','purchase_stage','hotspot']
        if any(old.get(key)!=new_slots.get(key) for key in draft_fields):
            self.invalidate_draft(s)
        selection_fields=['audience','budget','scenario','preferences','required_software','other_constraints','required_os','max_weight_kg','region','channel']
        changed=any(old[key]!=new_slots[key] for key in selection_fields)
        old_selected=s.selected
        if changed:
            s.selected=None; s.candidates=[]
            if old['audience']!=s.slots.audience or old['scenario']!=s.slots.scenario: s.catalog=None
        missing=[ask(key) for key in CORE_QUESTIONS if not getattr(s.slots,key)]
        if missing:
            self.invalidate_draft(s)
            return self.response(s,'needs_input',missing[:2],'先确认客户需求；已提供的信息会保留。')
        if request.action=='skip_preferences' or text in ['跳过','跳过偏好','没有偏好','无偏好','直接推荐']:
            s.preferences_done=True
        if s.slots.preferences: s.preferences_done=True
        if s.asked_preferences and text and not missing: s.preferences_done=True
        if not s.preferences_done:
            s.asked_preferences=True
            self.invalidate_draft(s)
            return self.response(s,'needs_input',[ask('preferences'),ask('required_software')],
                '画像、预算和用途已记录，再确认两点；偏好可以跳过，必用软件属于硬性条件。')
        catalog=await self.catalog(s,request.action=='refresh' or text=='刷新')
        candidates,rejected=rank_products(catalog,s.slots.model_dump(mode='json'))
        previous=s.candidates
        s.candidates=candidates
        if not candidates:
            s.selected=None
            self.invalidate_draft(s)
            return self.response(s,'needs_input',message='当前库没有能通过约束核验的候选，请检查下方原因或补充对应产品证据。',issues=catalog.issues,rejected=rejected)
        candidate_id=request.candidate_id
        match=re.fullmatch(r'(?:选择?|确认)?\s*第?\s*([123])\s*(?:款|个)?(?:型号级)?',text)
        if match:
            index=int(match.group(1))-1
            if index<len(previous): candidate_id=previous[index]['candidate_id']
        if candidate_id:
            if changed and previous==[]:
                # Updated hard constraints must be reviewed before confirming a new ranking.
                self.invalidate_draft(s)
                return self.response(s,'candidates',message='需求已更新，请先查看新候选，再确认产品。',issues=catalog.issues,rejected=rejected)
            selected=next((c for c in candidates if c['candidate_id']==candidate_id),None)
            if not selected:
                self.invalidate_draft(s)
                return self.response(s,'needs_input',message='该候选不在本轮有效范围，请重新选择。',rejected=rejected)
            s.selected=selected.copy()
        if s.selected:
            current=next((c for c in candidates if c['candidate_id']==s.selected['candidate_id']),None)
            if not current or current['configuration']!=s.selected['configuration']:
                s.selected=None
                self.invalidate_draft(s)
                return self.response(s,'candidates',message='产品或销售配置证据已变化，请重新确认。',rejected=rejected)
            s.selected=current.copy()
        if s.selected!=old_selected:
            self.invalidate_draft(s)
        if not s.selected:
            self.invalidate_draft(s)
            candidate_options=[{'label':f'{i}. {c["model_name"]}','message':f'选{i}'}
                               for i,c in enumerate(candidates,1)]
            return self.response(s,'candidates',[{'field':'candidate_id','question':'请确认候选编号，如“选1”。未核实价格或软件适配的候选会明确标为待核实，不能承诺适合购买。','options':candidate_options}],
                '以下按场景相关规格与偏好排序。仅展示本库有完整规格原文的型号，可能不足三款；未确认销售SKU时，只能制作附条件的型号介绍。',issues=catalog.issues,rejected=rejected)
        if s.selected['unknowns']:
            questions=[]
            if s.slots.budget.approximate: questions.append({'field':'budget','question':'请明确可接受的最高预算，例如“预算6000以内”。'})
            if s.selected['current_price'] is None and not s.slots.budget.unlimited:
                for key,q in [('region','客户购买地区是什么？'),('channel','实际购买渠道是什么？')]:
                    if not getattr(s.slots,key): questions.append({'field':key,'question':q})
            self.invalidate_draft(s)
            return self.response(s,'pending_verification',questions[:2],
                '已记录意向型号，但以下硬性条件尚未证实，不能交给下游当作推荐成立。请补充同库核验资料后刷新。',issues=s.selected['unknowns'],rejected=rejected)
        missing=[]
        if not s.slots.platform: s.slots.platform=Platform.XIAOHONGSHU  # single-platform MVP: never ask
        if not s.slots.objective: missing.append(ask('objective',**OBJECTIVE_OPTIONS))
        if missing:
            self.invalidate_draft(s)
            return self.response(s,'needs_input',missing,'产品已确认，接下来补齐给内容生成环节的发布任务。')
        # Fresh read on every export: old candidate evidence is not automatically current.
        before=self.fingerprint(catalog,s.selected['candidate_id'])
        fresh=await self.catalog(s,True)
        if before!=self.fingerprint(fresh,s.selected['candidate_id']):
            s.selected=None; s.candidates=[]
            self.invalidate_draft(s)
            return self.response(s,'needs_input',message='交接前检测到产品证据变化，请重新查看候选并确认。')
        current,_=rank_products(fresh,s.slots.model_dump(mode='json'))
        chosen=next((c for c in current if c['candidate_id']==s.selected['candidate_id']),None)
        if not chosen or chosen['unknowns'] or chosen['configuration']!=s.selected['configuration']:
            s.selected=None
            self.invalidate_draft(s)
            return self.response(s,'pending_verification',message='交接前价格或硬约束核验已变化，请重新确认。')
        s.selected=chosen
        handoff=await self.handoff(s,fresh)
        s.handoff_cache=handoff
        if not handoff['data_state']['can_generate']:
            # An approved draft must never survive a handoff that no longer qualifies.
            self.invalidate_draft(s)
            return self.response(s,handoff['data_state']['status'],message='交接证据不足，请查看风险与缺口。',issues=handoff['data_state']['reasons'],handoff=handoff)
        return await self.draft_turn(s,request,handoff)

    async def draft_turn(self,s,request,handoff):
        """Second confirmation gate: generate/revise platform copy, then require
        explicit operator approval before can_publish is ever set true."""
        binding=self.handoff_binding(handoff)
        if s.draft and s.draft_binding!=binding:
            # Evidence, product or task changed under an existing draft.
            self.invalidate_draft(s,clear_handoff=False)
        s.handoff_cache=handoff
        facts=[Fact.model_validate(f) for f in handoff['facts']]
        images=[ImageAsset.model_validate(
                    {k:v for k,v in i.items() if k not in ('verification','expires_in_seconds')})
                for i in handoff['images']]
        task=Task.model_validate(handoff['materials']['task'])
        strategy=handoff['strategy']; risks=handoff['risks_and_gaps']['input_risks']
        product=handoff['materials'].get('product') or {}
        allowed_text=f"{product.get('model_name','')} {product.get('configuration','')}"
        text=request.message.strip()
        action=request.action
        def audit_current(draft):
            return audit_copy(draft['title'],draft['body'],draft.get('tags',[]),draft.get('cta',''),
                              handoff['facts'],risks,draft.get('pages'),allowed_text)
        if s.draft and action=='continue':
            # Natural-language confirmation/edit requests are accepted at the draft gate too.
            if text in ['通过','确认','可以','没问题','可以发布','发布']: action='approve_draft'
            elif re.search(r'重新生成|换一版|再生成一次|用模型|换模型|模型重新',text): action='regenerate_draft'
            elif text: action='revise_draft'

        if action=='approve_draft' and s.draft:
            report=audit_current(s.draft)
            s.compliance_report=report
            s.draft_approved=bool(report['passed'])
            s.draft_binding=binding
            msg='已通过合规核验，可以复制或导出。' if report['passed'] else '合规核验未通过，请先按下方问题修改后再确认。'
            return self.response(s,'ready' if report['passed'] else 'needs_revision',message=msg,issues=report['errors']+report['warnings'],handoff=handoff)

        if action=='revise_draft' and s.draft:
            s.draft=await asyncio.to_thread(revise_draft,s.draft,request.revise_instruction or request.message,
                                            facts,images,strategy,risks,task,engine=request.engine)
            s.draft_binding=binding
            s.draft_approved=False; s.compliance_report=None
            report=audit_current(s.draft)
            s.compliance_report=report
            return self.response(s,'draft_ready',message='已按要求修改，请核对后再确认是否可发布。',issues=report['errors']+report['warnings'],handoff=handoff)

        if action=='regenerate_draft' or s.draft is None:
            s.draft=await asyncio.to_thread(compose_draft,task,facts,images,strategy,risks,engine=request.engine,model=request.model)
            report=audit_current(s.draft)
            fallback_note=''
            if request.engine=='llm' and not report['passed']:
                model_errors=report['errors'][:2]
                fallback=await asyncio.to_thread(compose_draft,task,facts,images,strategy,risks,engine='rule')
                fallback_report=audit_current(fallback)
                if fallback_report['passed']:
                    reason='；'.join(model_errors)[:120]
                    fallback['engine_meta']={**fallback.get('engine_meta',{}),
                                             'provider':'rule-fallback-compliance',
                                             'fallback_reason':reason}
                    s.draft=fallback; report=fallback_report
                    fallback_note=f'模型稿未通过规则扫描（{reason}），已切换规则版；请核对后确认。'
            s.draft_binding=binding
            s.draft_approved=False; s.compliance_report=report
            return self.response(s,'draft_ready',[{'field':'draft_decision','question':'图文方案已生成，是否满足发布要求？可回复“通过”，或说明要怎么改（例如“再短一点”“换个人群”）。','options':DRAFT_OPTIONS}],
                fallback_note or '已基于本次核验材料生成图文方案，发布前需要你确认。',issues=report['errors']+report['warnings'],handoff=handoff)

        # draft already exists and no explicit action — surface it for confirmation again.
        return self.response(s,'draft_ready',[{'field':'draft_decision','question':'当前图文方案是否可以发布？可回复“通过”或说明修改要求。','options':DRAFT_OPTIONS}],
            '上一版图文方案仍待确认。',issues=(s.compliance_report or {}).get('errors',[])+(s.compliance_report or {}).get('warnings',[]),handoff=handoff)

    @staticmethod
    def fingerprint(catalog,pid):
        return digest('\n'.join(sorted(digest(d.text) for p in catalog.products if p.product_id==pid for d in p.docs)))

    async def handoff(self,s,catalog):
        candidate=s.selected
        product=next(p for p in catalog.products if p.product_id==candidate['candidate_id'])
        task=Task(product_query=product.model,audience=s.slots.audience,scenario=s.slots.scenario,
                  objective=s.slots.objective,platform=s.slots.platform,
                  purchase_stage=s.slots.purchase_stage,instructions=s.slots.instructions,
                  hotspot=s.slots.hotspot)
        context=strategy(task)
        images,image_issues=await verified_images(catalog,product,task.platform.value,context['fact_priority_sections'])
        # Individual image identity skips are warnings; only insufficient usable
        # images should block the handoff.
        image_blockers=[issue for issue in image_issues if '不同图片只有' in issue or '至少' in issue]
        blocking=list(product.issues)+image_blockers
        status='ready' if not blocking else 'unavailable'
        risks=input_risks(task)
        issues=list(catalog.issues)+list(product.issues)+list(image_issues)
        if not candidate['current_price']: issues.append('未交付当前价格或销售SKU；不得推断不同容量/屏幕/键盘组合')
        issues.append('活动、补贴、赠品与库存未核验，全部不交付；不得从历史价格卡或用户输入补写')
        issues.append('图片只证明型号级来源；颜色、屏幕与键盘未证明匹配特定SKU，不得标为配置实拍')
        connection=catalog.connection
        if not connection.get('official_library'):
            if connection.get('source_kind')=='local_official_export':
                issues.append('本地官方知识库 v7 导出：不是实时ima调用，赛事指定库要求仍不算满足')
            else:
                issues.append('个人库联调：尚未满足赛事指定共享知识库要求')
        bundle=MaterialBundle(request_id='task-'+secrets.token_hex(10),data_mode='live',status=status,task=task,
            product=Product(product_id=product.product_id,model_name=product.model,configuration=candidate['configuration'],confirmed=True),
            facts=product.facts,images=[ImageAsset.model_validate({k:v for k,v in i.items() if k not in ['verification','expires_in_seconds']}) for i in images],issues=issues)
        read_at=now_local().isoformat()
        return {'schema_version':'2.0',
            'task_context':{'task_id':bundle.request_id,'operator_role':'门店导购/内容运营','customer':s.slots.model_dump(mode='json'),
                            'content_scope':'型号级附条件介绍；未验证SKU不得写SKU级承诺'},
            'product_identity':{**bundle.product.model_dump(),'sku':candidate['current_price']['sku'] if candidate['current_price'] else None,
                                'sku_confirmed':bool(candidate['current_price']),'operator_confirmed':True},
            'facts':[f.model_dump() for f in product.facts],
            'images':images,
            'risks_and_gaps':{'blocking':blocking,'warnings':issues,'input_risks':risks,'activities':[],
                             'current_price':candidate['current_price'],'price_evidence':candidate['price_evidence'],
                             'historical_sources_excluded':product.history},
            'data_state':{'read_at':read_at,'catalog_read_at':catalog.read_at,'status':status,
                'can_generate':status=='ready','can_publish':False,'competition_ready':status=='ready' and catalog.connection.get('official_library',False),
                'knowledge_source':catalog.connection.get('source_kind','ima'),
                'reasons':issues,'knowledge_base':s.kb_name,'scope_sha256':catalog.scope_hash,
                'evidence_sha256':self.fingerprint(catalog,product.product_id),'image_address_ttl_seconds':1800,
                'refresh_policy':'开始生成前检查状态与有效期；再次发布前重新核验；读库时间不代表资料更新时间'},
            'recommendation':candidate,'strategy':context,'scenario_sources':catalog.scene_sources,
            'image_plan':[{'image_id':i['image_id'],'caption':i['caption'],'purpose':'按平台结构排版原始产品素材，禁止重画或推断产品外观'} for i in images],
            'priority_fact_ids':[f.fact_id for f in product.facts if f.text.partition('：')[0] in context['fact_priority_sections']],
            'materials':bundle.model_dump(mode='json'),'trace':catalog.client.trace}


service=ConversationService()
