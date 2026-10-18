"""ClarifyAgent and scenario strategy adapted from tv-buying-copilot's slot flow."""
import re
from pydantic import Field, model_validator
from .contracts import StrictModel, Platform, Task
from .platforms import PLATFORM_RULES

class Intake(StrictModel):
    message: str = Field(default='', max_length=4000)
    product_query: str = Field(default='', max_length=300)
    audience: str = Field(default='', max_length=300)
    scenario: str = Field(default='', max_length=300)
    objective: str = Field(default='', max_length=300)
    platform: Platform | None = None
    purchase_stage: str = Field(default='待确认', max_length=100)
    instructions: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def parse_explicit_message(self):
        """Small deterministic NeedParser; unknown slots stay empty instead of being guessed."""
        message=self.message
        if not message:
            return self
        aliases={'小红书':Platform.XIAOHONGSHU,'公众号':Platform.WECHAT_ARTICLE,
                 '朋友圈':Platform.MOMENTS,'海报':Platform.POSTER}
        platforms={value for key,value in aliases.items() if key in message}
        if not self.platform and len(platforms)==1:
            self.platform=platforms.pop()
        if not self.product_query:
            # A name is only a query: retrieval still requires exact catalog identity.
            match=re.search(r'(?:HUAWEI\s*|华为\s*)?(MateBook\s+[A-Za-z0-9 ]+(?:[（(][^）)]+[）)])?(?:\s*鸿蒙版)?)',message,re.I)
            if match:
                self.product_query=match.group(0).strip()
        if not self.audience:
            for words,label in [(['大学新生','新生','大学生'],'大学新生'),(['职场','上班族','商务人士'],'职场办公者'),
                                (['创作者','摄影师','设计师'],'内容创作者'),(['家用','家庭','父母'],'家庭用户'),
                                (['企业采购','团队采购'],'企业采购者')]:
                if any(word in message for word in words):
                    self.audience=label
                    break
        if not self.scenario:
            for words,label in [(['开学','上课','校园'],'开学购机与校园学习'),(['出差','差旅'],'差旅移动办公'),
                                (['视频剪辑','内容创作','摄影'],'内容创作'),(['家庭换新','旧机换新'],'家庭换新'),
                                (['企业采购','团队采购'],'企业采购'),(['职场','会议','办公'],'职场办公')]:
                if any(word in message for word in words):
                    self.scenario=label
                    break
        if not self.objective:
            for words,label in [(['到店','预约'],'邀请到店体验'),(['咨询','私信'],'引导进一步咨询'),
                                (['选型','怎么选'],'帮助比较选型'),(['了解产品','产品介绍'],'了解产品')]:
                if any(word in message for word in words):
                    self.objective=label
                    break
        # All user text goes downstream as a requirement, never as product evidence.
        if not self.instructions:
            self.instructions=message[:2000]
        return self

    def questions(self):
        labels = {'product_query':'要为哪款华为PC制作内容？如未选型，请先提供用途、必用软件和预算，查看候选后确认型号。',
                  'audience':'内容面向谁？例如大学新生、移动办公者或创作者。',
                  'scenario':'主要使用或营销场景是什么？例如差旅会议、开学购机。',
                  'objective':'希望读者下一步做什么？例如到店体验、咨询或了解选型。',
                  'platform':'准备发布在哪里：公众号、小红书、门店朋友圈还是门店海报？'}
        return [{'field':key,'question':text} for key,text in labels.items() if not getattr(self,key)]

    def as_task(self):
        return Task(**self.model_dump(exclude={'message'}))

def strategy(task):
    text = task.audience + task.scenario
    if any(x in text for x in ['创作','摄影','视频','设计']):
        angle, priorities, question = '围绕素材查看、编辑和流转展开', ['屏幕','处理器','内存','存储','接口'], '常用创作软件、插件与素材规模是否适配？'
    elif any(x in text for x in ['学生','新生','开学','学习']):
        angle, priorities, question = '围绕课堂学习、作业与校园携带展开', ['尺寸与重量','屏幕','电池','接口','操作系统'], '专业课程必用软件、携带需求与预算是什么？'
    elif any(x in text for x in ['企业','采购','团队']):
        angle, priorities, question = '围绕团队任务与部署验证展开', ['操作系统','接口','无线连接','内存','存储'], '企业应用、外设、管理与服务条款是否已有依据？'
    elif any(x in text for x in ['家庭','换新','父母']):
        angle, priorities, question = '围绕家庭日常任务与换机验证展开', ['屏幕','操作系统','存储','接口'], '家人常用软件和旧机数据如何验证迁移？'
    else:
        angle, priorities, question = '围绕办公一天中的文档、会议和移动任务展开', ['尺寸与重量','接口','电池','屏幕','摄像头'], '常用办公软件、会议工具和外接设备是否适配？'
    rules = PLATFORM_RULES[task.platform.value]
    return {'audience':task.audience,'scenario':task.scenario,'purchase_stage':task.purchase_stage,
            'content_objective':task.objective,'user_problem':question,'angle':angle,
            'hotspot':task.hotspot,
            'fact_priority_sections':priorities,'platform':task.platform.value,'platform_rules':rules,
            'cta':rules['cta'],
            'explanation':'按人群与任务选择事实优先级；只有材料包中有证据的卖点才能使用。',
            'forbidden_claims':['虚构亲身体验或软件实测','无依据的价格、补贴、库存和承诺','把系列参数继承给未确认SKU']}
