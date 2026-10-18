# 本地官方知识库

把官方知识库 v7 的 ZIP 放在这里，例如：

```text
data/official-kb-v7.zip
```

启动时设置：

```bash
export KB_SOURCE=local
export LOCAL_KB_ZIP=/app/data/official-kb-v7.zip
```

该模式直接读取官方导出文件，不调用 ima 实时接口；`competition_ready` 仍为 `false`。
未放置 ZIP 时，应用会回退到 `KB_SOURCE=ima` 的 ima 适配器。
ZIP 文件被 `.gitignore` 排除，不会提交到 GitHub。
