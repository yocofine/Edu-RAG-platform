# -*- coding: utf-8 -*-
# ============================================================================
# 项目脚本包
# ============================================================================
# 根目录只保留高频入口脚本，低频专项脚本按主题放入子包：
#
#   子包结构：
#     enterprise_overlay/  — 企业仿真资料增强相关脚本
#       - analyze_dirty_enterprise_samples.py  — 脏样本治理风险分析
#       - analyze_enterprise_data_realism.py   — 资料真实度评估
#       - build_enterprise_overlay_dataset.py  — 构建 clean overlay 预览数据集
#       - check_enterprise_overlay_readiness.py — overlay 就绪门禁
#       - plan_enterprise_overlay_activation.py — 生成上线计划
#       - run_enterprise_overlay_activation.py  — 执行上线计划
#
#     kb/                  — 知识库版本对比和治理脚本
#       - compare_kb_versions.py     — 单场景版本召回对比
#       - compare_all_kb_versions.py — 全场景版本召回对比
#
#     ocr/                 — 离线 OCR 复核资料处理脚本
#       - run_offline_ocr.py       — 离线 OCR 清洗入口
#       - promote_ocr_candidates.py — 提升已复核 OCR 资料
#
#     tools/               — 低频本地辅助工具脚本
#       - capacity_estimate.py          — 知识库容量与参数压力估算
#       - check_local_runtime.py        — 本地运行环境诊断
#       - generate_requirements_lock.py — 生成项目级依赖锁文件
#
#   首轮必会脚本（本目录）：
#     - acceptance_smoke.py           — 本地服务真实链路验收
#     - api_e2e_smoke.py             — 真实 API E2E 验收
#     - rebuild_kb_version.py        — 一次性构建完整知识库版本
#     - rebuild_scenarios.py         — 批量重建多个场景的知识库版本
#     - check_project_guardrails.py  — 项目结构和代码约束守护检查
#     - evaluate_core_chain.py       — 主问答链路工程回归评测
#     - check_evaluation_gate.py     — 主问答链路评测门禁
#
#   进阶阶段掌握脚本（本目录）：
#     - manage_kb_versions.py        — 知识库版本管理命令行工具
#     - cleanup_missing_docs.py      — 清理已删除文档对应的 manifest/chunk
#     - evaluate_followup_chain.py   — 多轮追问链路回归评测
#     - check_followup_gate.py       — 多轮追问评测门禁
#     - evaluate_ragas_quality.py    — RAGAS 语义质量补充评测
#     - collect_performance_baseline.py — 采集主问答链路性能基线
#     - check_performance_gate.py    — 性能基线门禁
#     - demo_query_prepare.py        — 第 06 章查询准备演示
#     - demo_query_rewrite_variants.py — 第 07 章查询改写与变体演示
#
#   课程维护脚本（本目录）：
#     - check_codealong_alignment.py — 跟敲代码对齐检查
#     - sync_chapter_maps.py         — 同步章节地图
#     - sync_chapter_animations.py   — 同步章节动画
#     - export_xmind.py              — 导出课程 XMind
#     - export_rag_architecture_comparison_xmind.py — 导出客户汇报 XMind
#
#   公共基础设施（供其他脚本导入）：
#     - common.py     — 脚本层公共工具（JSON 读写、UTF-8 输出、命令执行、报告摘要）
#     - gate_utils.py — 门禁判断公共函数（阈值比较、必填项检查）
#     - eval_common.py — 评测和性能脚本共享的样本解析工具
# ============================================================================
"""项目脚本包。

根目录只保留高频入口脚本，低频专项脚本按主题放入子包。
"""
