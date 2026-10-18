"""The LLM is untrusted input; malformed output must fall back, never 500."""
import pytest
import app.compose as compose
from app.contracts import Task, Fact, Source, ImageAsset

TASK = Task(product_query='HUAWEI MateBook Test', audience='大学生', scenario='课堂学习',
            objective='到店体验', platform='xiaohongshu')
FACT = Fact(fact_id='F1', product_id='P1', text='屏幕：450 nits',
            source=Source(kind='ima', title='产品事实', locator='test', excerpt='450 nits'))
IMAGE = ImageAsset(image_id='I1', product_id='P1', access_url='/api/v1/assets/test',
                   caption='测试图', model_verified=True,
                   source=Source(kind='ima', title='图片', locator='test', excerpt='test'))


@pytest.mark.parametrize('bad_output', [[], None, {}, {'title': '只有标题'}, {'title': 1, 'body': []}])
def test_malformed_llm_output_falls_back_to_rule(monkeypatch, bad_output):
    monkeypatch.setattr(compose, 'complete_json', lambda *args, **kwargs: (bad_output, {'provider': 'test'}))
    draft = compose.compose_draft(TASK, [FACT], [IMAGE], {'angle': '课堂'}, [], engine='llm')
    assert draft['title'] and draft['body']
    assert draft['engine_meta']['provider'] == 'rule-fallback'


def test_llm_ids_outside_handoff_are_dropped(monkeypatch):
    payload = {'title': '测试标题', 'body': '测试正文', 'tags': ['华为PC'],
               'cta': '', 'fact_ids': ['F1', 'F-NOT-ALLOWED'],
               'image_plan': [{'image_id': 'I1'}, {'image_id': 'I-NOT-ALLOWED'}]}
    monkeypatch.setattr(compose, 'complete_json', lambda *args, **kwargs: (payload, {'provider': 'test'}))
    draft = compose.compose_draft(TASK, [FACT], [IMAGE], {'angle': '课堂'}, [], engine='llm')
    assert draft['fact_ids'] == ['F1']
    assert [item['image_id'] for item in draft['image_plan']] == ['I1']
    assert draft['cta']
