"""The LLM is untrusted input; malformed output must fall back, never 500."""
import pytest
import app.compose as compose
from app.compliance import audit_copy
from app.contracts import Task, Fact, Source, ImageAsset

TASK = Task(product_query='HUAWEI MateBook Test', audience='大学生', scenario='课堂学习',
            objective='到店体验', platform='xiaohongshu')
FACT = Fact(fact_id='F1', product_id='P1', text='屏幕：450 nits',
            source=Source(kind='ima', title='产品事实', locator='test', excerpt='450 nits'))
IMAGE = ImageAsset(image_id='I1', product_id='P1', access_url='/api/v1/assets/test',
                   caption='测试图', model_verified=True,
                   source=Source(kind='ima', title='图片', locator='test', excerpt='test'))


def test_rule_draft_has_no_six_page_plan():
    """原 test_rule_draft_has_six_page_plan 断言六页方案的角色顺序与事实/图片绑定。

    规格 §0 删掉了 7 页图文，所以结论反转为：草稿不再产出 pages，事实与图片绑定改由
    fact_ids / image_plan 承担（这两条原意保留）。
    """
    draft = compose.compose_draft(TASK, [FACT], [IMAGE],
                                  {'angle': '课堂', 'audience': '大学生', 'user_problem': '你常用什么软件？'}, [])
    assert 'pages' not in draft
    assert draft['fact_ids'] == ['F1']
    assert [item['image_id'] for item in draft['image_plan']] == ['I1']


def test_audit_accepts_product_identity_numbers_and_rejects_fake_experience():
    ok = audit_copy('MateBook 14 怎么选', '正文 450 nits', ['华为PC'], '', [FACT], [],
                    allowed_text='HUAWEI MateBook 14')
    assert ok['passed'] is True
    bad = audit_copy('标题', '正文：这是实测数据，续航很强。', [], '', [FACT], [],
                     allowed_text='HUAWEI MateBook 14')
    assert bad['passed'] is False
    assert any('亲测' in error or '实测' in error for error in bad['errors'])


@pytest.mark.parametrize('bad_output', [[], None, {}, {'title': '只有标题'}, {'title': 1, 'body': []}])
def test_malformed_llm_output_falls_back_to_rule(monkeypatch, bad_output):
    monkeypatch.setattr(compose, 'complete_json', lambda *args, **kwargs: (bad_output, {'provider': 'test'}))
    draft = compose.compose_draft(TASK, [FACT], [IMAGE], {'angle': '课堂'}, [], engine='llm')
    assert draft['title'] and draft['body']
    assert 'pages' not in draft
    assert draft['engine_meta']['provider'] == 'rule-fallback'


def test_llm_ids_outside_handoff_are_dropped(monkeypatch):
    payload = {'title': '测试标题', 'body': '测试正文', 'tags': ['华为PC'],
               'cta': '', 'fact_ids': ['F1', 'F-NOT-ALLOWED'],
               # 旧模型可能仍然吐 pages；新规格下它必须被忽略而不是被保存。
               'pages': [{'role': 'hook', 'title': '测试', 'text': '测试',
                          'fact_ids': ['F1', 'F-BAD'], 'image_id': 'I1'}],
               'image_plan': [{'image_id': 'I1'}, {'image_id': 'I-NOT-ALLOWED'}]}
    monkeypatch.setattr(compose, 'complete_json', lambda *args, **kwargs: (payload, {'provider': 'test'}))
    draft = compose.compose_draft(TASK, [FACT], [IMAGE], {'angle': '课堂'}, [], engine='llm')
    assert draft['fact_ids'] == ['F1']
    assert 'pages' not in draft
    assert [item['image_id'] for item in draft['image_plan']] == ['I1']
    assert draft['cta']
