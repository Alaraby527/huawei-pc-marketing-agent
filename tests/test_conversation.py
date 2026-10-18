"""Synthetic PC/source fixtures; these are not claimed as live competition results."""
import asyncio
import base64
import json
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
import app.conversation as conversation
from app.conversation import ConversationService, Turn, DialogueError, Slots, parse_message
from app.evidence import (Budget, PriceRecord, ProductEvidence, EvidenceCatalog, load_catalog,
                          rank_products, verified_images, now_local, digest)
from app.main import app as fastapi_app, service as global_service
from app.retrieval import Document, extract_facts
from app.ima import ImaError

MODEL='HUAWEI MateBook Test（测试版）'
TEXT='''# HUAWEI MateBook Test（测试版） - 产品事实
> 合成规格；各配置条件不得合并。
## 来源
https://consumer.huawei.com/cn/test/matebook-test-version/specs/
## 操作系统
TEST OS
## 尺寸与重量
约1.33 kg，依配置不同。
## 屏幕
标准450 nits；柔光500 nits，各版本独立。
## 内存
24GB/32GB
## 存储
512GB/1TB
'''
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==')

class Fake:
    kb_name='测试个人库'; kb_id='kb1'
    def __init__(self): self.trace=[]; self.read=[]; self.bad_scene=False
    async def connect(self): return {'official_library':False,'connection':'verified'}
    async def catalog(self): return [
        {'media_id':'facts','title':'产品事实.md','media_type':7},
        {'media_id':'scene','title':'学生学习场景卡.md','media_type':7},
        {'media_id':'index','title':'图片索引.csv','media_type':5},
        {'media_id':'image','title':'vmall_matebook-test_主视觉.png','media_type':9},
        {'media_id':'image2','title':'vmall_matebook-test_侧面细节.png','media_type':9},
        {'media_id':'image3','title':'vmall_matebook-test_屏幕展示.png','media_type':9}]
    async def search(self,q): return [{'media_id':'foreign','title':'其他库资料'}]
    async def read_text(self,e):
        self.read.append(e['media_id'])
        if e['media_id']=='facts': return TEXT
        if e['media_id']=='scene': return '# '+('家庭换新场景卡' if self.bad_scene else '学生学习场景卡')+'\n虚构教学案例，不是真实软件适配'
        if e['media_id']=='index': return ('文件名,画面说明,原始图片URL,商品页URL\n'
            'vmall_matebook-test_主视觉.png,测试图片,https://res.vmallres.com/test.png,https://consumer.huawei.com/cn/test/matebook-test-version/specs/\n'
            'vmall_matebook-test_侧面细节.png,测试图片侧面,https://res.vmallres.com/test2.png,https://consumer.huawei.com/cn/test/matebook-test-version/specs/\n'
            'vmall_matebook-test_屏幕展示.png,测试图片屏幕,https://res.vmallres.com/test3.png,https://consumer.huawei.com/cn/test/matebook-test-version/specs/\n')
        raise AssertionError('不得读取未枚举对象')
    async def media(self,mid):
        suffix={'image':'','image2':'2','image3':'3'}.get(mid,'')
        return {'url_info':{'url':f'https://pkb.ima.myqcloud.com/test{suffix}.png','headers':{'X-IMA-Sign':'SECRET'}}}


def catalog(): return asyncio.run(load_catalog('大学生','课堂学习',Fake()))

def slots(**kw):
    return Slots(audience='大学生',scenario='课堂学习',budget=Budget(unlimited=True),**kw).model_dump(mode='json')


def price(**kw):
    now=now_local()
    base=dict(model_name=MODEL,sku='test-sku',configuration='24GB/512GB/测试款',amount=5500,
        price_type='page_price',region='中国大陆',channel='官方商城',verified_at=now-timedelta(minutes=1),
        valid_until=now+timedelta(hours=1),source_url='https://www.vmall.com/test',status='verified',available=True)
    base.update(kw); return PriceRecord(**base)


def attach_price(c,p): c.products[0].prices.append((p,{'title':'销售核验记录.md'},'synthetic verified record'))

@pytest.fixture
def mocked(monkeypatch):
    c=catalog()
    async def load(*args): return c
    monkeypatch.setattr('app.conversation.load_catalog',load)
    def _distinct_png(url,*args):
        # Different (but each internally consistent) bytes per test asset URL so that
        # verified_images' content-hash dedup does not collapse 3 fixture images into 1.
        import re
        suffix=re.search(r'test(\d*)\.png',url)
        tag=(suffix.group(1) if suffix else '').encode()
        return PNG+b'\x00'*len(tag)+tag, 'image/png'
    monkeypatch.setattr('app.evidence.read_resource',_distinct_png)
    return c

@pytest.mark.parametrize('message,missing',[
    ('大学生',{'budget','scenario'}),('预算6000以内',{'audience','scenario'}),('想买电脑',{'audience','budget'}),
    ('大学生，预算6000以内',{'scenario'}),('给大学生写一篇小红书',{'budget','scenario'})])
def test_single_information_questions_and_no_network(message,missing):
    service=ConversationService(Fake)
    r=asyncio.run(service.turn(Turn(message=message)))
    assert {q['field'] for q in r['questions']}==missing
    assert len(r['questions'])<=2 and not r['candidates'] and not r['can_generate']


def test_missing_core_never_filled_after_many_turns():
    async def run():
        service=ConversationService(Fake); r=await service.turn(Turn(message='大学生'))
        for _ in range(5): r=await service.turn(Turn(session_id=r['session_id'],message='继续'))
        assert r['slots']['budget'] is None and not r['slots']['scenario']
    asyncio.run(run())


def test_budget_parse_is_not_parameter_or_activity():
    for text in ['内存32GB，存储512GB','国补2000元','价格5899元']:
        assert 'budget' not in parse_message(text,Slots(),[])
    for text,amount in [('预算六千以内',6000),('预算1.2万',12000),('6000',6000),
                        ('预算不超过6000',6000),('预算最多6000',6000),
                        ('预算一万二',12000),('预算1万2',12000),('预算1万2000',12000),
                        ('预算两千五',2500)]:
        assert parse_message(text,Slots(),[])['budget']['maximum']==amount
    assert parse_message('预算5000到6000',Slots(),[])['budget']['minimum']==5000
    for text in ['预算不限','没有预算限制','预算没有上限']:
        assert parse_message(text,Slots(),[])['budget']['unlimited'] is True


@pytest.mark.parametrize('text',['不需要Windows','不用Windows','不要Windows','无需Windows'])
def test_negated_os_is_not_a_requirement(text):
    assert 'required_os' not in parse_message(text,Slots(),[])
    assert parse_message('必须用Windows',Slots(),[])['required_os']=='Windows'


def test_scenario_phrase_is_not_software_requirement():
    patch = parse_message('主要用途是课堂学习、写论文、上网课', Slots(), [])
    assert patch.get('scenario')
    assert 'required_software' not in patch


def test_guided_questions_expose_options():
    from app.conversation import ask, DRAFT_OPTIONS
    for field in ['audience', 'budget', 'scenario', 'preferences', 'required_software']:
        question = ask(field)
        assert question['options'] and all(option.get('label') and option.get('message') for option in question['options'])
    assert len(DRAFT_OPTIONS) == 3


async def approve_current_draft(service):
    r=await service.turn(Turn(message='大学生'))
    sid=r['session_id']
    for message in ['预算不限，主要用来写论文','跳过','选1','到店体验','通过']:
        r=await service.turn(Turn(session_id=sid,message=message))
    return sid,r


def test_approval_is_invalidated_when_a_requirement_is_removed(mocked):
    async def run():
        service=ConversationService(Fake)
        sid,r=await approve_current_draft(service)
        assert r['can_publish'] is True
        r=await service.turn(Turn(session_id=sid,slot_updates=Slots(budget=None)))
        assert r['status']=='needs_input'
        assert r['can_publish'] is False and r['draft_approved'] is False
        assert r['draft'] is None and r['handoff'] is None
    asyncio.run(run())


def test_export_rejects_after_requirements_invalidate_approval(mocked):
    global_service.client_factory=Fake
    client=TestClient(fastapi_app)
    r=client.post('/api/v1/conversations/turn',json={'message':'大学生'}).json()
    sid=r['session_id']
    for message in ['预算不限，主要用来写论文','跳过','选1','到店体验','通过']:
        r=client.post('/api/v1/conversations/turn',json={'session_id':sid,'message':message}).json()
    assert r['can_publish'] is True
    r=client.post('/api/v1/conversations/turn',json={'session_id':sid,'slot_updates':{'budget':None}}).json()
    assert r['status']=='needs_input' and r['can_publish'] is False
    response=client.get(f'/api/v1/conversations/{sid}/export?format=markdown')
    assert response.status_code==409


def test_export_allowed_when_approval_matches_current_handoff(mocked):
    global_service.client_factory=Fake
    client=TestClient(fastapi_app)
    r=client.post('/api/v1/conversations/turn',json={'message':'大学生'}).json()
    sid=r['session_id']
    for message in ['预算不限，主要用来写论文','跳过','选1','到店体验','通过']:
        r=client.post('/api/v1/conversations/turn',json={'session_id':sid,'message':message}).json()
    assert r['can_publish'] is True
    response=client.get(f'/api/v1/conversations/{sid}/export?format=markdown')
    assert response.status_code==200
    assert '# 一、场景判断' in response.text


def test_refresh_after_draft_reloads_catalog(mocked, monkeypatch):
    async def run():
        service=ConversationService(Fake)
        sid,r=await approve_current_draft(service)
        calls=[]
        original=conversation.load_catalog
        async def counted(*args):
            calls.append(1); return await original(*args)
        monkeypatch.setattr('app.conversation.load_catalog',counted)
        r=await service.turn(Turn(session_id=sid,action='refresh'))
        assert calls, 'refresh must actually re-read the catalog'
        assert r['status']=='draft_ready'
    asyncio.run(run())


def test_scope_and_scene_originals_checked():
    c=catalog()
    assert 'foreign' not in c.client.read
    assert c.client.trace[0]['rejected_search_hits']==1
    assert '虚构教学' not in str([f.text for p in c.products for f in p.facts])
    assert c.scene_sources[0]['usage'].startswith('仅用于场景')
    fake=Fake(); fake.bad_scene=True
    bad=asyncio.run(load_catalog('大学生','课堂学习',fake))
    assert not bad.scene_sources and any('标题与原文' in x for x in bad.issues)


def test_history_and_no_price_cannot_pass_finite_budget():
    c=catalog(); c.products[0].history=[{'amount':5899,'status':'historical_only'}]
    s=slots(); s['budget']=Budget(maximum=6000).model_dump()
    candidates,_=rank_products(c,s)
    assert candidates[0]['eligibility']=='pending_verification'
    assert candidates[0]['current_price'] is None
    assert candidates[0]['budget_status']=='unverified'

@pytest.mark.parametrize('change',[
    {'verified_at':now_local()-timedelta(days=2)}, {'verified_at':now_local()+timedelta(minutes=10)},
    {'valid_until':now_local()-timedelta(seconds=1)}, {'model_name':MODEL+' Pro'},
    {'available':False}, {'conditions':'以旧换新'}, {'currency':'USD'},
    {'source_url':'https://consumer.huawei.com.evil.test/x'}, {'configuration':'24GB'},
    {'price_type':'subsidized'}, {'verified_at':now_local().replace(tzinfo=None)},
])
def test_invalid_dynamic_records(change):
    assert price(**change).blockers(MODEL,'中国大陆','官方商城')


def test_price_scope_and_budget_filter():
    c=catalog(); attach_price(c,price())
    s=slots(region='中国大陆',channel='官方商城'); s['budget']=Budget(maximum=6000).model_dump()
    good,_=rank_products(c,s); assert good[0]['budget_status']=='verified'
    s['budget']=Budget(maximum=5000).model_dump(); assert not rank_products(c,s)[0]
    s['budget']=Budget(maximum=6000).model_dump(); s['region']='另一个地区'
    assert rank_products(c,s)[0][0]['budget_status']=='unverified'
    assert price().blockers(MODEL,None,'官方商城')


def test_same_sku_conflict_not_lowest_price_selection():
    c=catalog(); attach_price(c,price()); attach_price(c,price(amount=5400))
    good,bad=rank_products(c,slots(region='中国大陆',channel='官方商城'))
    assert not good and '冲突' in str(bad)


def test_explicit_software_unknown_os_mismatch_weight_mismatch():
    c=catalog()
    result,_=rank_products(c,slots(required_software=['必用某专业插件']))
    assert result[0]['eligibility']=='pending_verification'
    assert not rank_products(c,slots(required_os='Windows'))[0]
    assert not rank_products(c,slots(max_weight_kg=1.2))[0]

@pytest.mark.parametrize('mode',['good','wrong_model','wrong_product_url','wrong_bytes','duplicate','no_index'])
def test_image_identity_and_bytes(monkeypatch,mode):
    c=catalog()
    monkeypatch.setattr('app.evidence.read_resource',lambda url,*args:(b'not same' if mode=='wrong_bytes' and 'res.vmallres' in url else PNG,'image/png'))
    if mode=='wrong_model':
        for row in c.image_index: row['产品型号']='HUAWEI MateBook Another'
    if mode=='wrong_product_url':
        for row in c.image_index: row['商品页URL']='https://www.vmall.com/product/another'
    if mode=='no_index': c.image_index=[]
    if mode=='duplicate':
        entry={**c.entries[-1],'media_id':'image2','title':'vmall_matebook-test_主视觉2.png'}
        c.entries.append(entry); c.image_index.append({**c.image_index[0],'文件名':entry['title']})
    images,issues=asyncio.run(verified_images(c,c.products[0],'moments',[]))
    assert len(images)==(1 if mode in ['good','duplicate'] else 0)
    assert 'SECRET' not in str(images)
    if mode not in ['good','duplicate']: assert issues


def test_full_dialogue_six_groups_memory_and_xiaohongshu_handoff(mocked):
    async def run():
        service=ConversationService(Fake)
        r=await service.turn(Turn(message='大学生'))
        sid=r['session_id']
        r=await service.turn(Turn(session_id=sid,message='预算不限，主要用来写论文'))
        assert {q['field'] for q in r['questions']}=={'preferences','required_software'}
        r=await service.turn(Turn(session_id=sid,message='跳过'))
        assert r['status']=='candidates' and len(r['candidates'])==1
        r=await service.turn(Turn(session_id=sid,message='选1'))
        assert {q['field'] for q in r['questions']}=={'objective'}
        r=await service.turn(Turn(session_id=sid,message='到店体验'))
        assert r['can_generate'] and not r['can_publish'] and not r['competition_ready']
        h=r['handoff']
        assert all(k in h for k in ['task_context','product_identity','facts','images','risks_and_gaps','data_state'])
        assert '标准450' in str(h['facts']) and h['risks_and_gaps']['activities']==[]
        assert not h['product_identity']['sku_confirmed']
        assert h['materials']['task']['platform']=='xiaohongshu'
        r=await service.turn(Turn(session_id=sid,message='预算5000以内'))
        assert r['selected'] is None and r['status']=='candidates'
        assert r['slots']['audience']=='大学生'
    asyncio.run(run())


def test_finite_budget_selection_blocks_and_isolates_sessions(mocked):
    async def run():
        service=ConversationService(Fake)
        r=await service.turn(Turn(message='大学生，预算6000以内，主要用来写论文',action='skip_preferences'))
        r=await service.turn(Turn(session_id=r['session_id'],message='选1'))
        assert r['status']=='pending_verification' and not r['can_generate'] and not r['handoff']
        fresh=await service.turn(Turn(message='预算不限'))
        assert not fresh['slots']['audience'] and not fresh['slots']['scenario']
        with pytest.raises(DialogueError):
            await service.turn(Turn(session_id=r['session_id'],expected_revision=0))
    asyncio.run(run())


def test_scope_cannot_switch_on_refresh(mocked,monkeypatch):
    async def run():
        service=ConversationService(Fake)
        r=await service.turn(Turn(message='大学生，预算不限，主要用来写论文',action='skip_preferences'))
        async def other(*args):
            c=await load_catalog('大学生','课堂学习',Fake()); c.scope_hash='other'; return c
        monkeypatch.setattr('app.conversation.load_catalog',other)
        r=await service.turn(Turn(session_id=r['session_id'],action='refresh'))
        assert r['status']=='unavailable' and not r['can_generate']
    asyncio.run(run())


def test_draft_generation_and_two_confirmation_gates(mocked):
    """After the product/platform handoff is ready, a draft is produced and
    must pass an explicit operator approval (second confirmation gate) with
    a deterministic compliance audit before can_publish becomes true."""
    async def run():
        service=ConversationService(Fake)
        r=await service.turn(Turn(message='大学生'))
        sid=r['session_id']
        r=await service.turn(Turn(session_id=sid,message='预算不限，主要用来写论文'))
        r=await service.turn(Turn(session_id=sid,message='跳过'))
        r=await service.turn(Turn(session_id=sid,message='选1'))
        r=await service.turn(Turn(session_id=sid,message='到店体验'))
        assert r['status']=='draft_ready'
        assert r['draft'] and r['draft']['platform']=='xiaohongshu'
        assert not r['draft_approved'] and not r['can_publish']
        assert r['compliance_report'] is not None
        original_body=r['draft']['body']
        r=await service.turn(Turn(session_id=sid,message='再短一点'))
        assert r['status']=='draft_ready' and not r['draft_approved']
        assert len(r['draft']['body'])<=len(original_body)
        r=await service.turn(Turn(session_id=sid,message='通过'))
        assert r['draft_approved'] is True
        assert r['can_publish'] is True
        assert r['compliance_report']['passed'] is True
    asyncio.run(run())


def test_draft_never_invents_numbers_outside_facts(mocked):
    async def run():
        service=ConversationService(Fake)
        r=await service.turn(Turn(message='大学生'))
        sid=r['session_id']
        r=await service.turn(Turn(session_id=sid,message='预算不限，主要用来写论文'))
        r=await service.turn(Turn(session_id=sid,message='跳过'))
        r=await service.turn(Turn(session_id=sid,message='选1'))
        r=await service.turn(Turn(session_id=sid,message='到店体验'))
        s=service.sessions[sid]
        s.draft['body']+='\n\n现在下单立减2999元，全网最低！'
        r=await service.turn(Turn(session_id=sid,message='通过'))
        assert r['draft_approved'] is False and r['can_publish'] is False
        assert any('2999' in e or '绝对化' in e for e in r['compliance_report']['errors'])
    asyncio.run(run())
