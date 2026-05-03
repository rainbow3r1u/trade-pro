---
name: auto-drawing-review
description: >
  AI自动审图系统（AI Drawing Review System）——基于多模态大模型与规则引擎的CAD建筑图纸自动审查平台。
  当用户需要开发、修改、扩展或讨论该系统的任何模块时触发，包括但不限于：
  (1) PDF图纸解析与预处理，(2) 多模态大模型图纸理解（PDF/图片→结构化JSON），
  (3) 建筑规范规则引擎设计与实现，(4) 审图报告生成（PDF/网页），
  (5) FastAPI后端服务开发，(6) React前端开发，(7) Celery异步任务队列，
  (8) 数据库模型设计，(9) Docker部署与DevOps，(10) 任何与建筑审图业务逻辑相关的代码编写。
---

# Auto Drawing Review — 项目级 Skill

## 项目概述

构建一个能自动读取CAD建筑图纸PDF、提取结构化信息、匹配国家建筑规范进行审查、并生成修改意见与报告的网站系统。

核心数据流：**PDF → 高清图片 → 多模态大模型 → JSON结构化数据 → 规则引擎 → 审图报告**

## 架构决策（不可随意更改）

| 层级 | 技术 | 决策理由 |
|------|------|---------|
| 前端 | React + PDF.js | PDF预览+坐标交互是核心体验，PDF.js业界最成熟 |
| 后端 | FastAPI + Pydantic | AI系统需高并发异步调用大模型API，FastAPI原生async最优 |
| 任务队列 | Celery + Redis | 单张图纸审图耗时30s~5min，必须用异步任务避免HTTP超时 |
| PDF处理 | PyMuPDF | CAD图纸PDF转高清图（300DPI）速度最快、最稳定 |
| OCR（备选） | PaddleOCR | 中文工程图识别率顶尖，可私有化部署 |
| AI理解 | GPT-4V / Qwen-VL | GPT-4V精度高（MVP首选）；Qwen-VL可离线部署（生产合规） |
| 数据存储 | PostgreSQL + Redis | PG的JSONB字段直接存储图纸JSON；Redis做队列+缓存 |
| 报告生成 | Jinja2 + WeasyPrint | HTML模板转PDF，排版效率远高于ReportLab手写代码 |
| 部署 | Docker Compose | 多服务（后端/Worker/Redis/DB/Nginx）一键编排 |

## 开发 Workflow

当用户提出任何需求时，按以下顺序执行：

1. **理解业务场景**：明确图纸类型（建筑平面/结构/水电）、审查规范、输出格式
2. **检查JSON Schema**：任何涉及图纸数据结构的修改，必须先更新 `references/json-schema.md`
3. **后端优先**：先写Pydantic模型 → 再写API接口 → 最后写业务逻辑
4. **规则即代码**：审图规则必须写成可配置Python类（见 `references/rules-examples.md`），禁止硬编码在API中
5. **异步一切**：任何调用大模型、生成报告、处理PDF的操作，必须走Celery任务，API只负责任务投递和状态查询
6. **测试驱动**：关键规则必须附带测试用例（JSON输入 → 预期违规输出）

## 服务目录结构

```
auto-drawing-review/
├── frontend/                 # React 应用
│   ├── src/
│   │   ├── components/       # PDF预览、审图结果列表、批注组件
│   │   ├── pages/            # 上传页、项目列表页、报告页
│   │   └── api/              # Axios封装，对接后端
│   └── package.json
├── backend/                  # FastAPI 应用
│   ├── app/
│   │   ├── api/              # 路由层（projects, upload, review, reports）
│   │   ├── core/             # 配置、安全、日志
│   │   ├── models/           # SQLAlchemy / Pydantic 模型
│   │   ├── services/         # 业务逻辑
│   │   │   ├── pdf_service.py        # PyMuPDF转图
│   │   │   ├── ai_parser.py          # 大模型调用+JSON解析
│   │   │   ├── rule_engine.py        # 规则引擎主入口
│   │   │   └── report_generator.py   # 报告生成
│   │   ├── rules/            # 规范条文审查规则（每条一个文件）
│   │   ├── tasks/            # Celery任务定义
│   │   └── main.py
│   ├── Dockerfile
│   └── requirements.txt
├── worker/                   # Celery Worker（可与backend共用镜像，也可分离）
├── docker-compose.yml
└── nginx.conf
```

## References 索引

按需读取，不要一次性加载所有：

- **技术架构详解** → 读 `references/architecture.md`（当需要解释/替换某个技术组件时）
- **图纸JSON数据结构** → 读 `references/json-schema.md`（当修改Room/Window等模型、或大模型输出格式时）
- **审图规则示例** → 读 `references/rules-examples.md`（当新增/修改规范审查逻辑时）
