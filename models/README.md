# 模型目录

服务器运行需要将 BGE-M3 放在：

```text
models/bge-m3/
```

模型权重不提交到 GitHub：当前 `pytorch_model.bin` 超过 GitHub 普通 Git 的单文件限制，
并且模型文件不适合进入应用源码版本历史。本地服务器部署副本已经包含该模型；从 GitHub
重新拉取项目时，请从受控的 OSS/NAS/内部模型仓库同步到上述目录。

默认使用远程 Rerank API，不需要 `bge-reranker-large`。
