# Third-party notices

本仓库为 Apache-2.0。以下是已接入或预留接入的第三方组件，许可与版权归各自作者所有。

## vue-fabric-editor（快图，编辑器，MIT）

- 上游：https://github.com/ikuaitu/vue-fabric-editor
- 用途：同源挂载的可编辑图文引擎（`/studio`）；本仓库只保存构建产物，不修改上游源码。
- 许可：MIT。构建产物包含上游 `LICENSE`（`app/static/studio/LICENSE.vue-fabric-editor`）。
- 字体：上游 bundle 内的华康金刚黑、汉体等字体为商业字体，**不在本仓库分发**；构建脚本会删除它们。需要商用字体时由运营方自行取得授权并登记版本。
- 模板/示例图：上游公开演示中的模板与图片可能另有条款；本项目只使用自研布局配方（`app/design_library.py`），不复制上游模板资产。

## 其他运行依赖

- FastAPI / Starlette / Pydantic / Uvicorn（MIT / BSD）
- SQLAlchemy（MIT）、psycopg（LGPL-3.0，仅作为驱动使用）、Redis client（MIT）
- Pillow（MIT-CMU）、Apache ECharts（Apache-2.0，前端图表按需引入）
- 未引入 TrendRadar（GPL-3.0）、ComfyUI（GPL-3.0）、迅排（AGPL-3.0）、RedInk（非商用限制）的代码。

## 字体

界面与海报渲染只使用系统字体或可商用开源字体（如 Noto Sans SC / Noto Serif SC）。服务端渲染使用容器内的 `fonts-noto-cjk`；如替换字体，请登记字体名、版本与授权来源。
