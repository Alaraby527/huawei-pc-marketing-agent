"""Synthetic fixtures exercise failure branches, never count as live product evidence."""
import asyncio
import base64
import copy
import time

import pytest
from fastapi.testclient import TestClient
from app.contracts import Task
from app.ima import ImaClient, ImaError, image_type, safe_resource_url
from app.intake import Intake, strategy
from app.main import app
from app.retrieval import (Document, ASSETS, conflicts, extract_facts, get_asset, guide,
                           image_matches, prepare_materials, store_asset)

MODEL='HUAWEI MateBook Test（测试版）'
TEXT='''# HUAWEI MateBook Test（测试版） - 产品事实

> 合成测试文档，不是真实规格。配置因版本而异。

## 来源
https://consumer.huawei.com/cn/test/matebook-test-version/specs/

## 操作系统
TEST OS

## 尺寸与重量
约 1.33 kg（圆键盘含电池）
约 1.32 kg（方键盘含电池）
*实际机身重量因配置不同而异。

## 屏幕
标准版：450 nits（典型值）
柔光版：500 nits（典型值）

## 电池
70 Wh（额定容量）
'''
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==')
VALUES=dict(product_query=MODEL,audience='大学新生',scenario='开学购机',objective='到店体验',platform='moments')


class FakeIma:
    kb_name='测试个人知识库'
    def __init__(self, mode='ok'):
        self.trace=[]
        self.mode=mode
        self.entries=[{'media_id':'doc','title':'产品事实.md','media_type':7}]
        if mode=='conflict': self.entries.append({'media_id':'doc2','title':'产品事实2.md','media_type':7})
        self.images=[{'media_id':f'image{i}','title':f'hwpc_matebook-test_主视觉_{i}.png','media_type':9} for i in range(6)]
    async def connect(self):
        if self.mode=='offline': raise ImaError('模拟权限不足')
        return dict(knowledge_base_name=self.kb_name,connection='verified',official_library=False)
    async def search(self, query):
        self.trace.append(dict(step='search_knowledge',status='success'))
        if self.mode=='empty_search': return []
        return [*self.entries,*self.images]
    async def catalog(self):
        return [*self.entries,*self.images]
    async def read_text(self, entry):
        if self.mode=='discovery': return '# '+MODEL+'\n\n## 建库状态\n产品发现卡，待补规格'
        return TEXT.replace('TEST OS','OTHER OS') if entry['media_id']=='doc2' else TEXT
    async def media(self, media_id):
        if self.mode=='no_images': raise ImaError('模拟图片失效')
        return {'url_info':{'url':'https://pkb.ima.myqcloud.com/test.png','headers':{'X-IMA-Sign':'private-test'}}}


@pytest.fixture(autouse=True)
def mock_resource(monkeypatch):
    monkeypatch.setattr('app.retrieval.read_resource',lambda *args:(PNG,'image/png'))
    ASSETS.clear()
    yield
    ASSETS.clear()


def run(mode='ok',**changes):
    return asyncio.run(prepare_materials(Task(**{**VALUES,**changes}),FakeIma(mode)))


def test_incomplete_task_asks_before_network(monkeypatch):
    monkeypatch.setattr('app.retrieval.ImaClient',lambda:pytest.fail('缺信息时不能连接网络'))
    result=asyncio.run(guide(Intake(product_query='想买轻薄本')))
    assert result['status']=='needs_input' and result['materials'] is None
    assert result['next_question']['field']=='audience'
    assert {q['field'] for q in result['questions']}=={'audience','scenario','objective','platform'}


def test_live_personal_handoff_keeps_evidence_and_does_not_claim_competition_readiness():
    result=run()
    assert result['materials']['status']=='ready'
    assert result['can_generate'] and not result['competition_ready'] and not result['can_publish']
    assert len(result['materials']['images'])==3
    weights=next(f for f in result['materials']['facts'] if f['text'].startswith('尺寸与重量'))
    assert '方键盘' in weights['text'] and '实际机身重量' in weights['source']['excerpt']
    assert '合成测试文档' in weights['conditions']
    assert 'private-test' not in str(result) and 'X-IMA-Sign' not in str(result)
    assert result['data_state']['read_at'] and result['image_plan']


def test_same_model_conflicting_specifications_stop_handoff():
    result=run('conflict')
    assert result['materials']['status']=='conflict'
    assert not result['can_generate'] and not result['materials']['facts']


def test_similar_model_is_not_substituted():
    result=run(product_query='HUAWEI MateBook Test Pro（测试版）')
    assert result['materials']['status']=='needs_input'
    assert not result['materials']['product']
    assert MODEL in [x['model_name'] for x in result['candidates']]


def test_empty_search_uses_catalog_and_reads_original():
    result=run('empty_search')
    assert result['materials']['status']=='ready'
    assert any(x['step']=='directory_fallback' for x in result['trace'])


def test_discovery_card_cannot_supply_complete_facts():
    result=run('discovery')
    assert result['materials']['status']=='unavailable'
    assert not result['materials']['facts']


@pytest.mark.parametrize('mode',['offline','no_images'])
def test_errors_do_not_become_ready(mode):
    result=run(mode)
    assert result['materials']['status']!='ready'
    assert not result['can_generate']


@pytest.mark.parametrize('platform,count',[('moments',3),('wechat_article',4),('xiaohongshu',6),('poster',1)])
def test_platform_changes_image_plan_and_structure(platform,count):
    result=run(platform=platform)
    assert len(result['materials']['images'])==count
    assert result['strategy']['platform']==platform
    assert result['strategy']['cta']==result['strategy']['platform_rules']['cta']


def test_scenario_changes_angle_and_fact_priorities():
    student=run()
    creator=run(audience='视频创作者',scenario='内容创作')
    assert student['strategy']['angle']!=creator['strategy']['angle']
    assert student['guidance']['priority_fact_ids']!=creator['guidance']['priority_fact_ids']
    rules=[strategy(Task(**{**VALUES,'platform':p})) for p in ['moments','poster','wechat_article','xiaohongshu']]
    assert len({r['cta'] for r in rules})==4
    assert len({r['platform_rules']['tone'] for r in rules})==4


def test_unverified_prices_and_absolute_claims_flagged():
    result=run(instructions='写全网最低，国补2000元，保证不卡')
    assert {r['type'] for r in result['risks']}=={'compliance','dynamic_claim'}
    assert all('2000' not in f['text'] for f in result['materials']['facts'])


def test_image_identity_uses_delimiters_to_avoid_similar_model():
    doc=Document({'media_id':'doc'},TEXT,MODEL)
    assert image_matches({'title':'hwpc_matebook-test_主图.png'},doc)
    assert not image_matches({'title':'hwpc_matebook-test-pro_主图.png'},doc)
    assert not image_matches({'title':'hwpc_matebook-test2_主图.png'},doc)


def test_message_parser_preserves_explicit_slots_and_asks_unknowns():
    intake=Intake(message='给大学新生写HUAWEI MateBook 14（鸿蒙版）的开学朋友圈，邀请到店体验')
    assert intake.platform.value=='moments' and intake.audience=='大学新生'
    assert intake.product_query=='HUAWEI MateBook 14（鸿蒙版）'
    assert not intake.questions()
    assert intake.as_task().product_query==intake.product_query
    assert Intake(message='想买轻薄本').questions()
    explicit=Intake(message='给大学新生写朋友圈',platform='poster',audience='家庭用户')
    assert explicit.platform.value=='poster' and explicit.audience=='家庭用户'


def test_negative_promotional_instructions_do_not_create_requested_claims():
    result=run(instructions='不写优惠价格；不要全网最低；保留配置条件')
    assert result['risks']==[]


def test_decimal_position_is_significant_for_conflicts():
    first,_=extract_facts(Document({'media_id':'one','title':'产品事实.md'},TEXT,MODEL),'P1')
    second,_=extract_facts(Document({'media_id':'two','title':'产品事实.md'},TEXT.replace('1.33','13.3'),MODEL),'P1')
    assert conflicts([*first,*second])


def test_asset_endpoint_serves_real_bytes_and_expires():
    path=store_asset(PNG,'image/png')
    client=TestClient(app)
    response=client.get(path)
    assert response.status_code==200 and response.content==PNG
    token=path.rsplit('/',1)[-1]
    ASSETS[token]=(PNG,'image/png',time.monotonic()-1)
    assert client.get(path).status_code==410
    assert get_asset(token) is None


@pytest.mark.parametrize('url',[
    'http://pkb.ima.myqcloud.com/test','https://127.0.0.1/test','https://localhost/test',
    'https://ima.qq.com.evil.test/test','https://user:password@ima.qq.com/test',
    'https://pkb.ima.myqcloud.com:444/test','file:///etc/passwd'])
def test_arbitrary_resource_urls_rejected(url):
    assert not safe_resource_url(url)


def test_hotspot_falls_back_to_editorial_when_public_feed_is_down(monkeypatch):
    from app.hotspot import related_hot_topics
    monkeypatch.setattr('app.hotspot._fetch', lambda: [])
    topics = related_hot_topics('课堂学习', '大学新生')
    assert topics and topics[0]['data_mode'] == 'editorial_fallback'
    assert topics[0]['source'] == 'editorial-fallback'


def test_ima_image_signature_and_url():
    assert safe_resource_url('https://pkb.ima.myqcloud.com/test.png')
    assert image_type(PNG)=='image/png'
    assert image_type(b'<html>expired</html>') is None


def test_repeated_pagination_cursor_fails_instead_of_silent_truncation():
    client=ImaClient()
    async def call(*args): return {'info_list':[],'is_end':False,'next_cursor':'same'}
    client.call=call
    with pytest.raises(ImaError,match='游标'):
        asyncio.run(client.pages('search_knowledge',{},'info_list'))


def test_http_guide_and_connection_use_real_adapter_boundary(monkeypatch):
    monkeypatch.setattr('app.retrieval.ImaClient',FakeIma)
    client=TestClient(app)
    response=client.post('/api/v1/guide',json=VALUES)
    assert response.status_code==200 and response.json()['status']=='ready'
    assert client.post('/api/v1/guide',json={}).json()['status']=='needs_input'
    assert client.post('/api/v1/guide',json={**VALUES,'platform':'unknown'}).status_code==422
