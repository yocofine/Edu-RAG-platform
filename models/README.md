# 模型目录（已废弃）

当前版本**不需要任何本地模型**：向量化走远端 embedding API，重排走远端 rerank API。
镜像里已移除 torch / sentence-transformers 等本地推理依赖，`EMBEDDING_BACKEND=local`
或 `RERANK_BACKEND=local` 会直接报错。

历史说明：早期版本把 BGE-M3 放在：

```text
models/bge-m3/
```

用于本地向量化。该模型权重不提交到 GitHub（`pytorch_model.bin` 超过普通 Git 单文件限制，
且模型文件不适合进入源码版本历史）。升级到 API 模式后，该目录可以清空或删除。

`docker-compose.yml` 仍保留 `MODEL_HOST_PATH=./models` 的只读挂载，仅为兼容旧配置，
空目录不影响运行。
